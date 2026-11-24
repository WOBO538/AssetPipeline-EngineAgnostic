"""EAP · 树 LOD 源生成（Blender 无头 Decimate，确定性）

    python tools/make_tree_lods.py            # 外层：调 Blender 三次 × 两树种，写 docs/tree_lods.json
    python tools/make_tree_lods.py --self-check-sha   # 再跑一遍比对 SHA（幂等自检）

内层（同一个文件，Blender 里执行）：
    blender -b --factory-startup --python tools/make_tree_lods.py -- \
        --blender <src.glb> <out.fbx> <ratio>

做法与 `glb_to_fbx.py` 完全同口径：导入 glb → 沿 X 预镜像 → 加 Decimate(COLLAPSE, ratio)
→ apply → 导出（`FBX_SCALE_ALL` + `bake_space_transform`）。区别只有一个：先减面再导出。

**LOD0 不动**：本脚本只读 `assets/raw/trees/*.glb`，产物全部写到 `assets/raw/trees/lod/`，
不会回写源 glb、`assets/raw/trees/fbx/*.fbx` 或贴图。

确定性：Decimate 本身确定；但 Blender 的 FBX 导出器有两个非确定源——
① 头里 `CreationTimeStamp` 取 `datetime.datetime.now()`；② 对象 UID 用 `hash(key)`（每个进程随机）。
实测同一输入连导两次，差 80 字节、20 处，全部落在时间字段和 UID 上。
在 Blender 进程内把这两处固定掉（`_make_fbx_export_deterministic`），不改 Blender 安装，
之后两次导出**逐字节相同**，所以 `docs/tree_lods.json` 只记一个 `sha256`。
"""
import hashlib
import json
import os
import struct
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDER = os.environ.get("EAP_BLENDER", r"D:\Apps\Blender 5.2\blender.exe")
SPECIES = ("conifer", "broadleaf")
RATIOS = (("lod1", 0.5), ("lod2", 0.25), ("lod3", 0.10))
SRC_DIR = os.path.join(REPO, "assets", "raw", "trees")
LOD_DIR = os.path.join(SRC_DIR, "lod")
OUT_JSON = os.path.join(REPO, "docs", "tree_lods.json")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- glb 二进制解析（源数据，不经过 Blender）

_GLB_JSON = 0x4E4F534A   # "JSON"
_GLB_BIN = 0x004E4942    # "BIN\0"
_COMP = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _glb_chunks(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"glTF":
        raise RuntimeError("不是 glb: %s" % path)
    off, gltf, blob = 12, None, b""
    while off + 8 <= len(data):
        clen, ctype = struct.unpack("<II", data[off:off + 8])
        chunk = data[off + 8: off + 8 + clen]
        if ctype == _GLB_JSON:
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == _GLB_BIN:
            blob = chunk
        off += 8 + clen
    if gltf is None:
        raise RuntimeError("glb 缺 JSON chunk: %s" % path)
    return gltf, blob


def _accessor_vec(gltf, blob, idx):
    """读一个 accessor（支持 byteStride；不支持 sparse —— 这两棵树的 glb 没有）。"""
    acc = gltf["accessors"][idx]
    if acc.get("sparse"):
        raise RuntimeError("accessor %d 是 sparse，暂不支持" % idx)
    bv = gltf["bufferViews"][acc["bufferView"]]
    fmt = _COMP[acc["componentType"]]
    n = _NCOMP[acc["type"]]
    size = struct.calcsize(fmt)
    stride = bv.get("byteStride") or n * size
    base = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    return [struct.unpack_from("<" + fmt * n, blob, base + i * stride) for i in range(acc["count"])]


def _glb_source_stats(path):
    """从 glb 二进制算 LOD0 的源数据（triCount / vertexCount / UV / 材质名 / UV 范围）。

    **按 primitive 分别求和**：这两棵树的每个材质是一个 primitive（conifer 2 个、
    broadleaf 2 个），只读 primitives[0] 会拿到一半的面和一半的顶点。
    triCount = indices accessor count / 3（mode=4 TRIANGLES）；
    vertexCount = POSITION accessor count 求和（不跨 primitive 去重，与 glTF 口径一致）。
    """
    gltf, blob = _glb_chunks(path)
    mats = [m.get("name") for m in (gltf.get("materials") or [])]
    detail = []
    total_tri = 0
    total_vert = 0
    uv_channels = 0
    names = []
    uv_min = [1e18, 1e18]
    uv_max = [-1e18, -1e18]
    uv_ok = True

    for mi, mesh in enumerate(gltf.get("meshes") or []):
        for pi, prim in enumerate(mesh.get("primitives") or []):
            mode = prim.get("mode", 4)   # 4 = TRIANGLES
            if mode != 4:
                raise RuntimeError("primitive mode=%d 不是 TRIANGLES: %s" % (mode, path))
            if "indices" not in prim or "POSITION" not in prim.get("attributes", {}):
                raise RuntimeError("primitive 缺 indices/POSITION: %s" % path)
            tri = int(gltf["accessors"][prim["indices"]]["count"]) // 3
            vert = int(gltf["accessors"][prim["attributes"]["POSITION"]]["count"])
            total_tri += tri
            total_vert += vert
            attrs = prim.get("attributes") or {}
            n_uv = len([k for k in attrs if k.startswith("TEXCOORD_")])
            uv_channels = max(uv_channels, n_uv)
            mat_i = prim.get("material")
            mname = mats[mat_i] if (mat_i is not None and mat_i < len(mats)) else None
            if mi == 0:
                names.append(mname)
            if n_uv:
                try:
                    for u, v in _accessor_vec(gltf, blob, attrs["TEXCOORD_0"]):
                        uv_min[0] = min(uv_min[0], float(u))
                        uv_min[1] = min(uv_min[1], float(v))
                        uv_max[0] = max(uv_max[0], float(u))
                        uv_max[1] = max(uv_max[1], float(v))
                except (KeyError, struct.error):
                    uv_ok = False
            detail.append({"mesh": mi, "primitive": pi, "material": mname,
                           "triCount": tri, "vertexCount": vert, "uvChannels": n_uv})

    uv_bounds = None
    if uv_ok and uv_min[0] < 1e18:
        uv_bounds = {"min": [round(uv_min[0], 6), round(uv_min[1], 6)],
                     "max": [round(uv_max[0], 6), round(uv_max[1], 6)],
                     "space": "glTF TEXCOORD_0 原始值（V 向下）；UE 侧 MeshDescription 实测存的是同一组值（未翻转）"}
    return {
        "glb": os.path.relpath(path, REPO).replace("\\", "/"),
        "primitives": len(detail),
        "triCount": total_tri,
        "vertexCount": total_vert,
        "uvChannels": uv_channels,
        "materialNames": names,
        "uvBounds": uv_bounds,
        "primitiveDetail": detail,
    }


def _make_fbx_export_deterministic():
    """把 Blender FBX 导出器的两个非确定源固定住（只作用于本进程，不改安装）。

    1. `export_fbx_bin` 的 `datetime.datetime.now()` → 固定时刻；
    2. `fbx_utils._key_to_uuid` 里 `hash(key)` → sha256(key) 派生（同 key 恒等，
       冲突时 +1 线性探测，调用顺序固定则结果固定）。
    """
    import datetime as _dt
    import types

    import io_scene_fbx.export_fbx_bin as _exp
    import io_scene_fbx.fbx_utils as _fu

    class _FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 0, 0, 0)

    _exp.datetime = types.SimpleNamespace(datetime=_FixedDatetime)

    def _key_to_uuid(uuids, key):
        u = int.from_bytes(hashlib.sha256(repr(key).encode("utf-8")).digest()[:8], "big") % (2 ** 63)
        while u in uuids and uuids[u] != key:
            u = (u + 1) % (2 ** 63)
        return u

    _fu._key_to_uuid = _key_to_uuid


# ---------------------------------------------------------------- 内层（Blender）

def _blender_main(argv):
    import bpy
    from mathutils import Vector

    src, out, ratio = argv[0], argv[1], float(argv[2])

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.import_scene.gltf(filepath=src)

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    src_tris = 0
    for o in meshes:
        o.data.calc_loop_triangles()
        src_tris += len(o.data.loop_triangles)

    # 与 glb_to_fbx 同口径：烘变换 → 沿 X 镜像 → 统一法线
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for obj in meshes:
        obj.scale.x *= -1.0
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for obj in meshes:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode="OBJECT")

    # Decimate（COLLAPSE，固定 ratio）——逐对象 apply
    for obj in meshes:
        bpy.context.view_layer.objects.active = obj
        mod = obj.modifiers.new(name="EAP_Decimate", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    tris = 0
    verts = 0
    mats = []
    uv_layers = []
    uv_samples = []
    mat_face_counts = {}
    bmin = [1e18] * 3
    bmax = [-1e18] * 3
    for obj in meshes:
        me = obj.data
        me.calc_loop_triangles()
        tris += len(me.loop_triangles)
        verts += len(me.vertices)
        mats.extend([m.name if m else None for m in me.materials])
        uv_layers.extend([l.name for l in me.uv_layers])
        if me.uv_layers:
            uvl = me.uv_layers[0].data
            for li in range(min(3, len(uvl))):
                uv_samples.append([round(uvl[li].uv[0], 6), round(uvl[li].uv[1], 6)])
        for poly in me.polygons:
            mat_face_counts[poly.material_index] = mat_face_counts.get(poly.material_index, 0) + 1
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            g = [w.x, w.z, -w.y]
            for d in range(3):
                bmin[d] = min(bmin[d], g[d])
                bmax[d] = max(bmax[d], g[d])

    _make_fbx_export_deterministic()
    bpy.ops.export_scene.fbx(
        filepath=out,
        path_mode="COPY",
        embed_textures=True,
        apply_scale_options="FBX_SCALE_ALL",
        bake_space_transform=True,
        object_types={"MESH"},
        use_selection=False,
    )

    meta = {
        "srcTriCount": src_tris,
        "triCount": tris,
        "vertexCount": verts,
        "materialSlots": mats,
        "materialFaceCounts": mat_face_counts,
        "uvLayers": uv_layers,
        "uvSamples": uv_samples,
        "bboxM": {"min": [round(v, 4) for v in bmin], "max": [round(v, 4) for v in bmax]},
        "ratio": ratio,
        "blenderVersion": bpy.app.version_string,
    }
    print("LOD_META " + json.dumps(meta, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- 外层

def _run_blender(src, out, ratio):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [BLENDER, "-b", "--factory-startup", "--python", os.path.abspath(__file__),
           "--", "--blender", src, out, str(ratio)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    # Blender 成功也常返回 exit=1：只看产物 + LOD_META
    meta = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("LOD_META "):
            meta = json.loads(line[len("LOD_META "):])
    if meta is None or not os.path.isfile(out):
        tail = "\n".join(((r.stdout or "") + (r.stderr or "")).splitlines()[-20:])
        raise RuntimeError("Blender 减面失败 src=%s ratio=%s rc=%d\n%s" % (src, ratio, r.returncode, tail))
    return meta


def main(argv):
    if argv and argv[0] == "--blender":
        return _blender_main(argv[1:])

    self_check = "--self-check-sha" in argv
    old = {}
    if self_check and os.path.isfile(OUT_JSON):
        with open(OUT_JSON, "r", encoding="utf-8-sig") as fh:
            old = json.load(fh)

    doc = {
        "schema": "eap.tree_lods/1",
        "generator": "blender-decimate",
        "blenderVersion": None,
        "axisNote": "与 glb_to_fbx 同口径：FBX_SCALE_ALL + 沿 X 预镜像（Unity 左手系导入再取反）",
        "hashNote": "sha256 是磁盘原字节；导出前已在 Blender 进程内固定 CreationTimeStamp 与对象 UID，"
                    "同一输入重复导出逐字节一致（不是事后归一化）",
        "species": {},
    }

    problems = []
    for sp in SPECIES:
        src = os.path.join(SRC_DIR, sp + ".glb")
        src_stats = _glb_source_stats(src)
        print("[EAP] %s source: primitives=%d tri=%d vert=%d uvChannels=%d mats=%r"
              % (sp, src_stats["primitives"], src_stats["triCount"], src_stats["vertexCount"],
                 src_stats["uvChannels"], src_stats["materialNames"]))
        for pd in src_stats["primitiveDetail"]:
            print("[EAP]   prim %d/%d mat=%s tri=%d vert=%d uv=%d"
                  % (pd["mesh"], pd["primitive"], pd["material"], pd["triCount"],
                     pd["vertexCount"], pd["uvChannels"]))
        entry = {"sourceGlb": "assets/raw/trees/%s.glb" % sp, "source": src_stats, "lods": []}
        for lod, ratio in RATIOS:
            out = os.path.join(LOD_DIR, "%s_%s.fbx" % (sp, lod))
            meta = _run_blender(src, out, ratio)
            doc["blenderVersion"] = meta.get("blenderVersion")
            rec = {
                "level": lod,
                "ratio": ratio,
                "fbx": os.path.relpath(out, REPO).replace("\\", "/"),
                "bytes": os.path.getsize(out),
                "sha256": _sha256(out),
                "triCount": meta["triCount"],
                "vertexCount": meta["vertexCount"],
                "materialSlots": meta["materialSlots"],
                "materialFaceCounts": meta["materialFaceCounts"],
                "uvLayers": meta["uvLayers"],
                "uvSamples": meta["uvSamples"],
                "bboxM": meta["bboxM"],
            }
            # 硬门槛：双材质槽还在、UV 还在、面数严格低于源
            if len([m for m in meta["materialSlots"] if m]) != 2:
                problems.append("%s %s 材质槽 != 2: %r" % (sp, lod, meta["materialSlots"]))
            if not meta["uvLayers"]:
                problems.append("%s %s 丢了 UV 层" % (sp, lod))
            if not (0 < meta["triCount"] < meta["srcTriCount"]):
                problems.append("%s %s 面数没降: %d" % (sp, lod, meta["triCount"]))
            entry["sourceTriCount"] = meta["srcTriCount"]
            entry["lods"].append(rec)
            print("[EAP] %s %s ratio=%.2f tris=%d verts=%d slots=%r uv=%r sha=%s"
                  % (sp, lod, ratio, meta["triCount"], meta["vertexCount"],
                     [m for m in meta["materialSlots"] if m], meta["uvLayers"],
                     rec["sha256"][:16]))
        # 源数据自洽：glb 二进制解析 vs Blender 导入（不一致说明解析口径或源文件有问题）
        if entry.get("sourceTriCount") != src_stats["triCount"]:
            problems.append("%s 源面数 glb解析=%d vs Blender导入=%s"
                            % (sp, src_stats["triCount"], entry.get("sourceTriCount")))
        src_names = [m for m in src_stats["materialNames"] if m]
        for rec in entry["lods"]:
            if [m for m in rec["materialSlots"] if m] != src_names:
                problems.append("%s %s 材质名 glb=%r vs Blender=%r"
                                % (sp, rec["level"], src_names, rec["materialSlots"]))
        doc["species"][sp] = entry

    doc["problems"] = problems
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    print("[EAP] 写入 %s species=%d problems=%d" % (OUT_JSON, len(doc["species"]), len(problems)))

    if self_check and old:
        diffs = []
        for sp, ent in doc["species"].items():
            for old_rec, new_rec in zip(((old.get("species") or {}).get(sp) or {}).get("lods", []), ent["lods"]):
                if old_rec.get("sha256") != new_rec["sha256"]:
                    diffs.append("%s %s %s != %s" % (sp, new_rec["level"],
                                                     old_rec.get("sha256"), new_rec["sha256"]))
        print("[EAP] 幂等自检：%s" % ("SHA 全部一致" if not diffs else "有不一致 " + "; ".join(diffs)))
        if diffs:
            return 1

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]))
