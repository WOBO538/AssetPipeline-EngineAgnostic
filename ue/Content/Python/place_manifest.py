"""EAP · placements 落位（回灌）：manifest v1.1 的 placements[*] → UE ISM 实例

这是把 Layer 1 从"单向导出"变成"可回灌"的那一格：
manifest 里 1 个资产 + N 条 `placements.items`（世界坐标、米、度），本脚本在 UE 里
建 1 个 Actor + 1 个 InstancedStaticMeshComponent，把 N 条落成 ISM 实例，然后存关卡。

用法（无头也能跑，不需要 -nullrhi 之外的东西）：

    # 落位（建/清 Actor + ISM，落 233 条，存关卡）
    python -c "..."   # 或 UE 里 -ExecutePythonScript=place_manifest.py
    # 验证（新进程重新加载关卡，读回实例数/transform，写 verify 证据）
    set EAP_PLACE_MODE=verify

环境变量：
- EAP_PLACE_MODE   place（默认）| verify
- EAP_MANIFEST     manifest 路径，默认仓库 schemas/pcg_manifest.json
- EAP_LEVEL_PKG    目标关卡，默认 /Game/EAP/PCG/PlacedPilot
- EAP_PLACE_SAVE   0 = 不存关卡（只测不落盘）
- EAP_PLACE_OUT    落位证据输出路径，默认仓库 docs/placed_pilot.json
- EAP_VERIFY_OUT   验证证据输出路径，默认仓库 docs/placed_verify.json

**只动一个专用关卡**（默认 PlacedPilot），不碰用户的 `/Game/Untitled` 试点现场。
幂等：每次先 `clear_instances()` 再重落，跑 10 次仍是 N 条。
"""
import hashlib
import json
import os
import struct
import time
import zlib

import unreal

CM_PER_M = 100.0
DEFAULT_MANIFEST = r"D:\Projects\AssetPipeline-EngineAgnostic\schemas\pcg_manifest.json"
DEFAULT_LEVEL = "/Game/EAP/PCG/PlacedPilot"
DEFAULT_OUT = r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_pilot.json"
DEFAULT_VERIFY_OUT = r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_verify.json"
ACTOR_LABEL = "EAP_Placements"
MAT_DIR = "/Game/EAP/Materials"
# _v2：第一版母材质把法线采样节点建成了"没法线贴图"的法线采样器，编译失败 → 全白，改名重建
FOLIAGE_MAT = MAT_DIR + "/M_EAP_Foliage_Masked_v2"
OPAQUE_MAT = MAT_DIR + "/M_EAP_Opaque_v2"
LEGACY_MATS = (MAT_DIR + "/M_EAP_Opaque", MAT_DIR + "/M_EAP_Foliage_Masked",
               MAT_DIR + "/M_EAP_Tree_Bark", MAT_DIR + "/M_EAP_Tree_Leaf")
USAGE_FLAGS = ("MATUSAGE_STATIC_MESH", "MATUSAGE_INSTANCED_STATIC_MESHES", "MATUSAGE_NANITE")
MULTI_TAG_PREFIX = "EAP_Asset_"
TEX_DIR = MAT_DIR + "/Tex"
WHITE_TEX = "/Engine/EngineResources/WhiteSquareTexture"
ROUGHNESS_SCALE = 0.9
# 贴图三档（照任务单表，不自创第四档）
TEX_KINDS = {
    "basecolor": (unreal.TextureCompressionSettings.TC_DEFAULT, True, "TC_Default"),
    "normal": (unreal.TextureCompressionSettings.TC_NORMALMAP, False, "TC_Normalmap"),
    "data": (unreal.TextureCompressionSettings.TC_MASKS, False, "TC_Masked"),
}
# 合成 fixture 的占位网格来源（只给 --multi 用，需 EAP_ALLOW_SYNTHETIC_MESH=1）
SYNTHETIC_BASES = ("/Engine/BasicShapes/Cube", "/Engine/BasicShapes/Sphere",
                   "/Engine/BasicShapes/Cylinder", "/Engine/BasicShapes/Cone")


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return "ERR: %r" % (exc,) if default is None else default


def _dump(path, payload):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    _log("证据已写入 " + path)


def _read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _mesh_asset_path(model_path):
    """manifest 里引擎网格写 `ue:/Engine/BasicShapes/Cube`，去掉 `ue:` 前缀。"""
    p = str(model_path or "")
    if p.startswith("ue:"):
        p = p[3:]
    return p


def _repo_root(manifest_path):
    """manifest 在 <repo>/schemas/ 下，模型相对路径按 repo 根解析。"""
    d = os.path.dirname(os.path.abspath(manifest_path))
    return os.path.dirname(d) if os.path.basename(d) in ("schemas", "docs") else d


def _tc_label(value):
    s = str(value).upper()
    if "NORMALMAP" in s:
        return "TC_Normalmap"
    if "MASK" in s:
        return "TC_Masked"
    if "DEFAULT" in s:
        return "TC_Default"
    return str(value)


def _read_texture_settings(tex, kind=None):
    cs = _safe(lambda: tex.get_editor_property("compression_settings"))
    srgb = _safe(lambda: tex.get_editor_property("srgb"), None)
    return {
        "name": _safe(lambda: str(tex.get_path_name())),
        "compression": _tc_label(cs),
        "srgb": None if isinstance(srgb, str) else bool(srgb),
        "kind": kind,
    }


def _set_texture_settings(tex, kind):
    """按三档设置压缩/sRGB，保存后立即读回（调用方拿读回值当证据，不自己猜）。"""
    cs, srgb, label = TEX_KINDS[kind]
    _safe(lambda: tex.set_editor_property("compression_settings", cs))
    _safe(lambda: tex.set_editor_property("srgb", srgb))
    _safe(lambda: unreal.EditorAssetLibrary.save_loaded_asset(tex))
    ev = _read_texture_settings(tex, kind)
    if ev["compression"] != label or ev["srgb"] != srgb:
        _warn("贴图设置未生效 %s kind=%s read=%r" % (ev["name"], kind, ev))
    return ev


def _png_unfilter(raw, width, height, bpp):
    stride = width * bpp
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if (pa <= pb and pa <= pc) else (b if pb <= pc else c))) & 0xFF
        elif ft != 0:
            raise RuntimeError("PNG 行过滤 %d 不支持" % ft)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def _decode_png(data):
    """只解 glTF 里最常见的 8-bit RGB/RGBA、非隔行 PNG（stdlib 实现，不依赖 PIL）。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("不是 PNG")
    pos, w, h, bd, ct, idat = 8, None, None, None, None, []
    while pos < len(data):
        ln, ty = struct.unpack_from(">I4s", data, pos)
        pos += 8
        seg = data[pos:pos + ln]
        pos += ln + 4
        if ty == b"IHDR":
            w, h, bd, ct, _cm, _fl, il = struct.unpack(">IIBBBBB", seg)
            if il != 0:
                raise RuntimeError("PNG 隔行不支持")
        elif ty == b"IDAT":
            idat.append(seg)
        elif ty == b"IEND":
            break
    if bd != 8 or ct not in (2, 6):
        raise RuntimeError("PNG bit=%r color=%r 不支持" % (bd, ct))
    return w, h, ct, _png_unfilter(zlib.decompress(b"".join(idat)), w, h, 4 if ct == 6 else 3)


def _encode_gray_png(width, height, gray):
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += gray[y * width:(y + 1) * width]

    def chunk(ty, body):
        return struct.pack(">I", len(body)) + ty + body + struct.pack(">I", zlib.crc32(ty + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _tex_out_dir():
    root = os.environ.get("EAP_TEX_DIR")
    if not root:
        saved = str(_safe(lambda: unreal.Paths.project_saved_dir(), "") or "")
        root = os.path.join(saved or os.path.expanduser("~"), "EAP", "tex")
    if not os.path.isdir(root):
        os.makedirs(root)
    return root


_GLB_PNG_CACHE = {}


def _glb_basecolor_pngs(glb_path):
    """按 glTF material 顺序返回每个 material 的 BaseColor PNG 字节（内嵌、无外部 uri）。"""
    key = os.path.abspath(glb_path)
    if key in _GLB_PNG_CACHE:
        return _GLB_PNG_CACHE[key]
    with open(key, "rb") as fh:
        blob = fh.read()
    magic, _ver, total = struct.unpack_from("<III", blob, 0)
    if magic != 0x46546C67:
        raise RuntimeError("不是 GLB: " + key)
    off, gltf, binary = 12, None, None
    while off < total:
        ln, ty = struct.unpack_from("<II", blob, off)
        off += 8
        seg = blob[off:off + ln]
        off += ln
        if ty == 0x4E4F534A:
            gltf = json.loads(seg.decode("utf-8"))
        elif ty == 0x004E4942:
            binary = seg
    textures = (gltf or {}).get("textures") or []
    images = (gltf or {}).get("images") or []
    views = (gltf or {}).get("bufferViews") or []
    out = []
    for mat in (gltf or {}).get("materials") or []:
        idx = (((mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {}).get("index"))
        if idx is None:
            out.append(None)
            continue
        src = textures[int(idx)].get("source")
        view = views[int(images[int(src)]["bufferView"])]
        start = int(view.get("byteOffset", 0))
        data = binary[start:start + int(view["byteLength"])]
        out.append(data if data[:8] == b"\x89PNG\r\n\x1a\n" else None)
    _GLB_PNG_CACHE[key] = out
    return out


def _make_roughness_texture(base_tex, target_lum_range=(0.65, 0.95), tag=None, slot=0, glb_path=None):
    """从 BaseColor 像素生成 1024² 灰度 roughness PNG（亮度线性映射到目标区间），导入并设为数据贴图。

    确定性：同一份 glb / 同一槽位 → 字节级相同的 PNG（两次 SHA256 可复算），不掺时间/随机。
    `base_tex` 只用于命名与证据；像素从 glb 内嵌的原始 BaseColor PNG 解（不受 UE 压缩损失影响）。
    """
    lo, hi = target_lum_range
    name = "%s_%d_Roughness" % (tag or "EAP", slot)
    base_name = _safe(lambda: str(base_tex.get_name()), "?") if base_tex is not None else "?"
    src_png = None
    if glb_path:
        try:
            pngs = [p for p in _glb_basecolor_pngs(glb_path) if p]
            if pngs:
                src_png = pngs[min(int(slot), len(pngs) - 1)]
        except Exception as exc:  # noqa: BLE001
            _warn("读 glb BaseColor 失败，roughness 退回常量: %r" % (exc,))
    if src_png is not None:
        sw, sh, ct, px = _decode_png(src_png)
        ch = 4 if ct == 6 else 3
        gray = bytearray(1024 * 1024)
        for y in range(1024):
            row = min(sh - 1, y * sh // 1024) * sw
            for x in range(1024):
                i = (row + min(sw - 1, x * sw // 1024)) * ch
                lum = (0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255.0
                gray[y * 1024 + x] = int(round((lo + lum * (hi - lo)) * 255))
        source = "basecolor:%s#material%d(base=%s)" % (os.path.basename(glb_path), int(slot), base_name)
    else:
        gray = bytearray([int(round((lo + hi) / 2.0 * 255))]) * (1024 * 1024)
        source = "fallback:constant(base=%s)" % base_name
    png = _encode_gray_png(1024, 1024, gray)
    sha = hashlib.sha256(png).hexdigest().upper()
    png_path = os.path.join(_tex_out_dir(), name + ".png")
    with open(png_path, "wb") as fh:
        fh.write(png)
    alb = unreal.EditorAssetLibrary
    _safe(lambda: alb.make_directory(TEX_DIR))
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", png_path)
    task.set_editor_property("destination_path", TEX_DIR)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    want = TEX_DIR + "/" + name + "." + name
    if not bool(_safe(lambda: alb.does_asset_exist(want), False)):
        imported = _safe(lambda: [str(p) for p in task.get_editor_property("imported_object_paths")], [])
        texs = [p for p in imported
                if str(_safe(lambda p=p: alb.load_asset(p).get_class().get_name(), "")) == "Texture2D"]
        if not texs:
            raise RuntimeError("roughness 贴图导入失败（imported=%r）" % imported)
        want = texs[0]
    tex = alb.load_asset(want)
    ev = _set_texture_settings(tex, "data")
    ev.update({"sha256": sha, "source": source, "pngPath": png_path})
    _log("roughness 贴图 %s sha=%s（1024²，%s）" % (want, sha[:16], source))
    return tex, ev


def _import_glb(asset, manifest_path):
    """manifest 的 model 是磁盘 .glb 时，先无头导入成 StaticMesh（Interchange），返回资产路径。

    2026-11-22 实测：UE 5.7 `-nullrhi -unattended` 下 Interchange glTF 导入可用（~0.4-1.2 s/棵），
    导入布局固定为 `<destPath>/<文件名>/StaticMeshes/<文件名>.<文件名>`，材质/贴图由 Interchange 生成。
    """
    model = asset.get("model") or {}
    rel = str(model.get("path") or "")
    glb = rel if os.path.isabs(rel) else os.path.join(_repo_root(manifest_path), rel)
    if not os.path.isfile(glb):
        raise RuntimeError("glb 不存在: " + glb)
    ue5 = (asset.get("engineTargets") or {}).get("UE5") or {}
    dest = ue5.get("destPath") or "/Game/EAP/Trees"
    mesh_path = _mesh_asset_path(ue5.get("meshPath"))
    if mesh_path and _safe(lambda: unreal.EditorAssetLibrary.does_asset_exist(mesh_path), False):
        _log("网格已导入，复用: " + mesh_path)
        return mesh_path
    _safe(lambda: unreal.EditorAssetLibrary.make_directory(dest))
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", glb)
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", True)
    t0 = time.time()
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    _log("glTF 导入完成 %.2fs ← %s" % (time.time() - t0, glb))
    imported = _safe(lambda: [str(p) for p in task.get_editor_property("imported_object_paths")], [])
    # 刚导入的贴图先按三档设一遍（MIC 若已存在会走复用路径，这里保证新导入的资产不是默认压缩）
    tex_ev = []
    for p in imported:
        obj = _safe(lambda p=p: unreal.EditorAssetLibrary.load_asset(p))
        if obj is not None and str(_safe(lambda o=obj: o.get_class().get_name(), "")) == "Texture2D":
            low = p.rsplit("/", 1)[-1].lower()
            kind = "normal" if "normal" in low else (
                "basecolor" if ("basecolor" in low or "base_color" in low or "_bc" in low or "albedo" in low)
                else "data")
            tex_ev.append(_set_texture_settings(obj, kind))
    if tex_ev:
        _log("glTF 贴图规范化 %d 张: %s" % (len(tex_ev), [(e["kind"], e["compression"]) for e in tex_ev]))
    meshes = [p for p in imported
              if str(_safe(lambda p=p: unreal.EditorAssetLibrary.load_asset(p).get_class().get_name(), "")) == "StaticMesh"]
    if mesh_path and _safe(lambda: unreal.EditorAssetLibrary.does_asset_exist(mesh_path), False):
        return mesh_path
    if meshes:
        _warn("manifest.meshPath 不可用，改用导入结果: %s" % meshes[0])
        return meshes[0]
    raise RuntimeError("导入后找不到 StaticMesh（imported=%r）" % imported)


def _ensure_level(level_pkg):
    """关卡存在就加载；不存在就新建空关卡。返回 (created, world/path)。"""
    alb = unreal.EditorAssetLibrary
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    exists = bool(_safe(lambda: alb.does_asset_exist(level_pkg), False))
    if exists:
        _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(level_pkg))
        _log("已加载已有落位关卡 " + level_pkg)
        return False, _safe(lambda: str(les.get_current_level().get_path_name()), None)
    d = level_pkg.rsplit("/", 1)[0]
    _safe(lambda: alb.make_directory(d))
    created = bool(_safe(lambda: les.new_level(level_pkg), False))
    _log("新建落位关卡 %s -> %r（注意：会先关掉当前关卡，不保存它的未存改动）" % (level_pkg, created))
    return created, _safe(lambda: str(les.get_current_level().get_path_name()), None)


def _find_actor(label):
    eas = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem), None)
    if eas is None or isinstance(eas, str):
        return None
    for a in _safe(lambda: list(eas.get_all_level_actors()), []):
        if str(_safe(lambda a=a: a.get_actor_label(), "")) == label:
            return a
    return None


def _spawn_actor():
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = eas.spawn_actor_from_class(unreal.Actor, unreal.Vector(0, 0, 0), unreal.Rotator())
    actor.set_actor_label(ACTOR_LABEL)
    _log("已生成落位 Actor: %s" % actor.get_name())
    return actor


def _add_ism(actor):
    """给关卡里的 Actor 实例加一个 InstancedStaticMeshComponent（Details→Add Component 的 Python 路径）。

    注意：`unreal.new_object(ISM, outer=actor)` 能建组件但**不在 OwnedComponents 里**，
    存关卡后组件会被丢掉（2026-11-22 实测）。必须走 SubobjectDataSubsystem.add_new_subobject。
    """
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = list(sds.k2_gather_subobject_data_for_instance(actor))
    if not handles:
        raise RuntimeError("Actor 没有可用的 subobject handle")
    params = unreal.AddNewSubobjectParams()
    params.set_editor_property("parent_handle", handles[0])
    params.set_editor_property("new_class", unreal.InstancedStaticMeshComponent)
    params.set_editor_property("blueprint_context", None)
    out = sds.add_new_subobject(params)
    handle = out[0] if isinstance(out, (tuple, list)) else out
    data = sds.k2_find_subobject_data_from_handle(handle)
    comp = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
    _log("已给 Actor 加 ISM 组件: %s" % _safe(lambda: comp.get_name()))
    return comp


def _ism_of(actor):
    comps = _safe(lambda: list(actor.get_components_by_class(unreal.InstancedStaticMeshComponent)), [])
    return comps[0] if comps else None


def _transform_of(item):
    t = item.get("t") or [0, 0, 0]
    r = item.get("r") or [0, 0, 0]
    s = item.get("s") or [1, 1, 1]
    loc = unreal.Vector(float(t[0]) * CM_PER_M, float(t[1]) * CM_PER_M, float(t[2]) * CM_PER_M)
    rot = unreal.Rotator(roll=float(r[0]), pitch=float(r[1]), yaw=float(r[2]))
    scale = unreal.Vector(float(s[0]), float(s[1]), float(s[2]))
    return unreal.Transform(loc, rot, scale)


def _bbox(items_m):
    xs = [p[0] for p in items_m]
    ys = [p[1] for p in items_m]
    zs = [p[2] for p in items_m]
    return {"min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
            "max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)]}


def _mesh_info(mesh):
    """落位网格的实测信息（证明"真的换成树了"：高度、面数、材质、LOD 数）。"""
    box = _safe(lambda: mesh.get_bounding_box())
    size_cm = _safe(lambda: [round(box.max.x - box.min.x, 2), round(box.max.y - box.min.y, 2),
                             round(box.max.z - box.min.z, 2)])
    mats = []
    nsec = _safe(lambda: int(mesh.get_num_sections(0)), 0) or 0
    for i in range(nsec):
        mi = _safe(lambda i=i: mesh.get_material(i))
        mats.append(_safe(lambda mi=mi: str(mi.get_name()), None))
    return {
        "path": _safe(lambda: str(mesh.get_path_name())),
        "bboxSizeCm": size_cm,
        "heightM": round(size_cm[2] / CM_PER_M, 3) if isinstance(size_cm, list) else None,
        "triangles": _safe(lambda: int(mesh.get_num_triangles(0))),
        "numLods": _safe(lambda: int(mesh.get_num_lods())),
        "materials": mats,
    }


def _blend_of(material_like):
    """沿父链找到 Material，返回 blend_mode 字符串（MIC 上没有 blend_mode 属性）。"""
    node = material_like
    for _ in range(6):
        if node is None or isinstance(node, str):
            return None
        cls = _safe(lambda node=node: str(node.get_class().get_name()), "")
        if cls == "Material":
            return _safe(lambda node=node: str(node.get_editor_property("blend_mode")))
        node = _safe(lambda node=node: node.get_editor_property("parent"))
    return None


def _material_source_map(mi):
    """从 Interchange 自动生成的 MIC 里读出它挂了哪些贴图/标量参数。

    Interchange glTF 的 MIC 参数名形如 `BaseColorTexture` / `NormalTexture` / `MetallicFactor`，
    读出来再喂给 EAP 自己的母材质——这样换任何 glTF 都不用猜贴图角色。
    """
    out = {"textures": {}, "scalars": {}}
    for item in _safe(lambda: list(mi.get_editor_property("texture_parameter_values")), []) or []:
        name = _safe(lambda: str(item.get_editor_property("parameter_info").get_editor_property("name")), "")
        val = _safe(lambda: item.get_editor_property("parameter_value"))
        if name and val is not None and not isinstance(val, str):
            out["textures"][name] = val
    for item in _safe(lambda: list(mi.get_editor_property("scalar_parameter_values")), []) or []:
        name = _safe(lambda: str(item.get_editor_property("parameter_info").get_editor_property("name")), "")
        out["scalars"][name] = _safe(lambda: float(item.get_editor_property("parameter_value")), None)
    return out


_REBUILT_MATS = set()
LINEAR_WHITE = TEX_DIR + "/T_EAP_White_Linear"


def _linear_white_texture():
    """Roughness 采样器是 SAMPLERTYPE_LINEAR_COLOR，默认贴图必须是"线性颜色"源。

    直接拿 `/Engine/EngineResources/WhiteSquareTexture`（sRGB=true）当默认贴图会让整个母材质编译失败：
    `Sampler type is 线性颜色, should be 颜色`。复制一份引擎白图并存到 /Game 下关 sRGB 即可。
    """
    alb = unreal.EditorAssetLibrary
    if bool(_safe(lambda: alb.does_asset_exist(LINEAR_WHITE), False)):
        return alb.load_asset(LINEAR_WHITE)
    _safe(lambda: alb.make_directory(TEX_DIR))
    _safe(lambda: alb.duplicate_asset(WHITE_TEX, LINEAR_WHITE))
    if not bool(_safe(lambda: alb.does_asset_exist(LINEAR_WHITE), False)):
        return None
    tex = alb.load_asset(LINEAR_WHITE)
    _safe(lambda: tex.set_editor_property("srgb", False))
    _safe(lambda: alb.save_loaded_asset(tex))
    _log("线性白贴图（Roughness 默认）: " + LINEAR_WHITE)
    return tex


def _ensure_material(pkg, masked, normal_tex=None, base_tex=None):
    """建/复用 EAP 母材质：BaseColor(+Normal) 参数化、metallic 0、roughness = 贴图 × RoughnessScale(0.9)。

    2026-11-22 实测：`-nullrhi` 下建材质 + `recompile_material` 都能跑（没有渲染管线也能编译节点图），
    所以材质规范化可以完全无头做，不需要用户点编辑器。

    **三个必须遵守的建法（踩过才写下来）**：
    1. 法线采样节点必须有默认贴图（`texture`），否则编译器报
       `Sampler type is Normal, should be Color for DefaultTexture` → 整个材质编译失败 → 退回**默认白材质**。
       没有法线贴图的资产（树叶）就干脆不建法线节点。
    2. BaseColor 采样节点也给它默认贴图，这样即使 MIC 参数没生效也是"带贴图的"，不会是纯白。
    3. v1.6 起节点图变了（roughness 贴图 × RoughnessScale）。已存在的资产**原地清空节点再重建**，
       路径与变体名不变（`M_EAP_Opaque_v2*` / `M_EAP_Foliage_Masked_v2*`），已有 MIC 的 parent 不受影响。
    """
    alb = unreal.EditorAssetLibrary
    mel = unreal.MaterialEditingLibrary
    _safe(lambda: alb.make_directory(MAT_DIR))
    if pkg in _REBUILT_MATS:
        return alb.load_asset(pkg)
    mat = alb.load_asset(pkg) if bool(_safe(lambda: alb.does_asset_exist(pkg), False)) else None
    if mat is not None:
        try:
            mel.delete_all_material_expressions(mat)
            _log("重建 EAP 母材质节点图（路径不变）: " + pkg)
        except Exception as exc:  # noqa: BLE001
            _warn("清空旧节点失败，删资产重建 %s: %r" % (pkg, exc))
            _safe(lambda: alb.delete_asset(pkg))
            mat = None
    if mat is None:
        mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            pkg.rsplit("/", 1)[-1], MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    _safe(lambda: mat.set_editor_property(
        "blend_mode", unreal.BlendMode.BLEND_MASKED if masked else unreal.BlendMode.BLEND_OPAQUE))
    if masked:
        _safe(lambda: mat.set_editor_property("opacity_mask_clip_value", 0.33))
    # 植被卡片两面可见（glTF 里 doubleSided=false，单面会让背面穿帮）
    _safe(lambda: mat.set_editor_property("two_sided", bool(masked)))
    bc = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -500, -200)
    _safe(lambda: bc.set_editor_property("parameter_name", "BaseColor"))
    _safe(lambda: bc.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_COLOR))
    if base_tex is not None:
        _safe(lambda: bc.set_editor_property("texture", base_tex))
    _safe(lambda: mel.connect_material_property(bc, "RGB", unreal.MaterialProperty.MP_BASE_COLOR))
    if masked:
        _safe(lambda: mel.connect_material_property(bc, "A", unreal.MaterialProperty.MP_OPACITY_MASK))
    if normal_tex is not None:
        nm = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -500, 200)
        _safe(lambda: nm.set_editor_property("parameter_name", "Normal"))
        _safe(lambda: nm.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL))
        _safe(lambda: nm.set_editor_property("texture", normal_tex))
        _safe(lambda: mel.connect_material_property(nm, "RGB", unreal.MaterialProperty.MP_NORMAL))
    for pname, val, y, prop in (("Metallic", 0.0, 420, unreal.MaterialProperty.MP_METALLIC),):
        sp = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -300, y)
        _safe(lambda sp=sp, pname=pname: sp.set_editor_property("parameter_name", pname))
        _safe(lambda sp=sp, val=val: sp.set_editor_property("default_value", val))
        _safe(lambda sp=sp, prop=prop: mel.connect_material_property(sp, "", prop))
    # roughness v1.6：贴图 R × RoughnessScale(0.9)。没有 override 时贴图默认白 → 1×0.9，与老行为一致
    rs = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -300, 560)
    _safe(lambda: rs.set_editor_property("parameter_name", "RoughnessScale"))
    _safe(lambda: rs.set_editor_property("default_value", ROUGHNESS_SCALE))
    rt = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -700, 700)
    _safe(lambda: rt.set_editor_property("parameter_name", "Roughness"))
    _safe(lambda: rt.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR))
    white = _safe(lambda: _linear_white_texture())
    if white is not None and not isinstance(white, str):
        _safe(lambda: rt.set_editor_property("texture", white))
    rmul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -150, 700)
    _safe(lambda: mel.connect_material_expressions(rt, "R", rmul, "A"))
    _safe(lambda: mel.connect_material_expressions(rs, "", rmul, "B"))
    _safe(lambda: mel.connect_material_property(rmul, "", unreal.MaterialProperty.MP_ROUGHNESS))
    for usage_name in USAGE_FLAGS:
        if hasattr(unreal.MaterialUsage, usage_name):
            _safe(lambda u=getattr(unreal.MaterialUsage, usage_name): mel.set_material_usage(mat, u))
    _safe(lambda: mel.recompile_material(mat))
    _safe(lambda: alb.save_loaded_asset(mat))
    _REBUILT_MATS.add(pkg)
    _log("已建 EAP 母材质 %s（%s%s%s，roughness=贴图×%.2f）" % (pkg, "MASKED" if masked else "OPAQUE",
                                                          "，带法线" if normal_tex is not None else "",
                                                          "，带默认 BaseColor" if base_tex is not None else "",
                                                          ROUGHNESS_SCALE))
    return mat


def _is_alpha_material(src, expected_alpha_mode=None):
    """判断某个槽是不是"带 alpha 的植被材质"。

    优先用 manifest 里 glTF 原始的 `alphaMode`（BLEND/MASK → masked），这是最可靠的口径；
    拿不到时再沿父链看 blend_mode（注意枚举串 "BLEND_OPAQUE" 里也含 "BLEND"，不能简单包含匹配）。
    """
    mode = str(expected_alpha_mode or "").upper()
    if mode:
        return mode in ("BLEND", "MASK", "MASKED")
    bu = str(_blend_of(src) or "").upper()
    return ("MASKED" in bu) or ("BLEND_BLEND" in bu) or ("TRANSLUCENT" in bu)


def _normalize_materials(mesh, tag, expected_alpha_modes=None, glb_path=None):
    """把 Interchange 自动生成的树材质换成 EAP 母材质派生的 MIC，返回覆盖材质列表 + 证据。

    四件事一次做完：叶子 BLEND → MASKED（省排序、能正常裁边）、metallic 0.4 → 0（去塑料感）、
    贴图按三档设压缩、每槽生成 roughness 灰度贴图并挂到 MIC（`Roughness`）+ `RoughnessScale`=0.9。
    补 `bUsedWithNanite` / `bUsedWithInstancedStaticMeshes` 用法标记（否则打包后可能丢材质）。
    """
    alb = unreal.EditorAssetLibrary
    mel = unreal.MaterialEditingLibrary
    n = int(_safe(lambda: mesh.get_num_sections(0), 0) or 0)
    expected_alpha_modes = list(expected_alpha_modes or [])
    overrides, evidence = [], []
    for slot in range(n):
        src = _safe(lambda slot=slot: mesh.get_material(slot))
        if src is None:
            overrides.append(None)
            continue
        blend = str(_blend_of(src) or "")
        want = (expected_alpha_modes[slot].get("alphaMode")
                if slot < len(expected_alpha_modes) and isinstance(expected_alpha_modes[slot], dict) else None)
        masked = _is_alpha_material(src, want)
        src_map = _material_source_map(src)
        tex_map = {}
        for pname, tex in (src_map.get("textures") or {}).items():
            low = pname.lower()
            if "basecolor" in low:
                tex_map["BaseColor"] = tex
            elif "normal" in low:
                tex_map["Normal"] = tex
        # 有法线贴图才建法线节点（否则材质编译失败 → 全白）；母材质按"带不带法线"分变体
        base_pkg = (FOLIAGE_MAT if masked else OPAQUE_MAT) + ("_N" if "Normal" in tex_map else "")
        pkg = MAT_DIR + "/MI_EAP_%s_%d_%s%s" % (tag, slot, "masked" if masked else "opaque",
                                                "_n" if "Normal" in tex_map else "")
        base_mat = _ensure_material(base_pkg, masked, normal_tex=tex_map.get("Normal"),
                                    base_tex=tex_map.get("BaseColor"))
        # 贴图三档：BaseColor=TC_Default+sRGB，Normal=TC_Normalmap+线性，数据贴图=TC_Masked+线性
        tex_settings = []
        for pname, kind in (("BaseColor", "basecolor"), ("Normal", "normal")):
            if tex_map.get(pname) is not None:
                tex_settings.append(_set_texture_settings(tex_map[pname], kind))
        rough_tex, rough_ev = None, None
        if tex_map.get("BaseColor") is not None:
            try:
                rough_tex, rough_ev = _make_roughness_texture(
                    tex_map["BaseColor"], tag=tag, slot=slot, glb_path=glb_path)
                tex_settings.append(rough_ev)
            except Exception as exc:  # noqa: BLE001
                _warn("roughness 贴图生成失败 tag=%s slot=%d: %r" % (tag, slot, exc))
        mi = alb.load_asset(pkg) if bool(_safe(lambda: alb.does_asset_exist(pkg), False)) else None
        if mi is None:
            mi = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                pkg.rsplit("/", 1)[-1], MAT_DIR, unreal.MaterialInstanceConstant,
                unreal.MaterialInstanceConstantFactoryNew())
        _safe(lambda: mel.set_material_instance_parent(mi, base_mat))
        _safe(lambda: mel.update_material_instance(mi))
        for param, tex in tex_map.items():
            _safe(lambda param=param, tex=tex: mel.set_material_instance_texture_parameter_value(mi, param, tex))
        if rough_tex is not None:
            _safe(lambda: mel.set_material_instance_texture_parameter_value(mi, "Roughness", rough_tex))
        _safe(lambda: mel.set_material_instance_scalar_parameter_value(mi, "RoughnessScale", ROUGHNESS_SCALE))
        _safe(lambda: mel.update_material_instance(mi))
        _safe(lambda: alb.save_loaded_asset(mi))
        applied_base = _safe(lambda: str(mel.get_material_instance_texture_parameter_value(mi, "BaseColor").get_path_name()))
        applied_normal = _safe(lambda: str(mel.get_material_instance_texture_parameter_value(mi, "Normal").get_path_name())) \
            if "Normal" in tex_map else None
        applied_rough = _safe(lambda: str(mel.get_material_instance_texture_parameter_value(mi, "Roughness").get_path_name())) \
            if rough_tex is not None else None
        overrides.append(mi)
        evidence.append({
            "slot": slot,
            "sourceMaterial": _safe(lambda src=src: str(src.get_name())),
            "sourceBlend": blend,
            "kind": "masked" if masked else "opaque",
            "material": pkg,
            "baseMaterial": base_pkg,
            "baseColorTex": _safe(lambda t=tex_map["BaseColor"]: str(t.get_path_name())) if "BaseColor" in tex_map else None,
            "normalTex": _safe(lambda t=tex_map["Normal"]: str(t.get_path_name())) if "Normal" in tex_map else None,
            "appliedBaseColor": applied_base,
            "appliedNormal": applied_normal,
            "roughnessTex": applied_rough,
            "roughnessScale": ROUGHNESS_SCALE,
            "roughnessMode": "texture" if rough_tex is not None else "scalar",
            "roughnessSha256": (rough_ev or {}).get("sha256"),
            "roughnessSource": (rough_ev or {}).get("source"),
            "textureSettings": tex_settings,
            "texturesApplied": bool(applied_base and applied_base.endswith(
                str(_safe(lambda t=tex_map["BaseColor"]: t.get_path_name())) if "BaseColor" in tex_map else "?")),
            "sourceMetallic": (src_map.get("scalars") or {}).get("MetallicFactor"),
            "metallic": 0.0,
            "roughness": ROUGHNESS_SCALE,
            "opacityClip": 0.33 if masked else None,
            "twoSided": bool(masked),
            "blendModeAfter": _safe(lambda: str(base_mat.get_editor_property("blend_mode"))),
            "normalDefaultTexture": bool(tex_map.get("Normal") is not None),
            "usage": {u: bool(_safe(lambda u=u: mel.has_material_usage(base_mat, getattr(unreal.MaterialUsage, u)), False))
                      for u in USAGE_FLAGS},
        })
        _log("材质规范化 slot %d: %s %s → %s（metallic %.2f→0，BaseColor=%s，roughness=%s×%.2f）"
             % (slot, evidence[-1]["sourceMaterial"], blend, pkg,
                float(evidence[-1]["sourceMetallic"] or 0.0), applied_base, applied_rough, ROUGHNESS_SCALE))
    bad = [e for e in evidence if not all(e["usage"].values())]
    if bad:
        _warn("材质用法标记仍然缺失: %s" % [e["material"] for e in bad])
    # 清理同一资产的历史 MIC（命名变了/槽位数变了留下的孤儿）
    keep = set(e["material"] for e in evidence)
    prefix = MAT_DIR + "/MI_EAP_" + tag + "_"
    for p in _safe(lambda: list(alb.list_assets(MAT_DIR, recursive=False, include_folder=False)), []) or []:
        p = str(p).split(".")[0]
        if p.startswith(prefix) and p not in keep:
            _safe(lambda p=p: alb.delete_asset(p))
            _log("清理旧材质实例 %s" % p)
    # 清理第一版（编译失败）的母材质
    for legacy in LEGACY_MATS:
        if bool(_safe(lambda legacy=legacy: alb.does_asset_exist(legacy), False)):
            _safe(lambda legacy=legacy: alb.delete_asset(legacy))
            _log("清理旧母材质 %s" % legacy)
    return {"overrideMaterials": overrides, "evidence": evidence}


def _instance_items(comp):
    """把 ISM 实例读回成 manifest 同格式（m / deg roll,pitch,yaw / scale）。"""
    n = int(comp.get_instance_count())
    out = []
    for i in range(n):
        xf = comp.get_instance_transform(i, True)
        rot = xf.rotation.rotator()
        out.append({
            "t": [round(xf.translation.x / CM_PER_M, 4),
                  round(xf.translation.y / CM_PER_M, 4),
                  round(xf.translation.z / CM_PER_M, 4)],
            "r": [round(rot.roll, 3), round(rot.pitch, 3), round(rot.yaw, 3)],
            "s": [round(xf.scale3d.x, 4), round(xf.scale3d.y, 4), round(xf.scale3d.z, 4)],
        })
    return out


def _sync_ground(items):
    """落位后把 EAP_Ground 地面平面按新 bbox 重设（纯 actor transform，无头安全）。

    空关卡没有地形，地面平面是唯一的"地"；换地块后它必须跟着放大，否则实例会浮在外面。
    """
    ground = None
    for a in _level_actors():
        if _safe(lambda a=a: str(a.get_actor_label()), "") == "EAP_Ground":
            ground = a
            break
    if ground is None or not items:
        return None
    bx = _bbox([it["t"] for it in items])
    cx = (bx["min"][0] + bx["max"][0]) / 2.0
    cy = (bx["min"][1] + bx["max"][1]) / 2.0
    ex = (bx["max"][0] - bx["min"][0]) / 2.0 + 25.0
    ey = (bx["max"][1] - bx["min"][1]) / 2.0 + 25.0
    z = bx["min"][2] - 0.5
    _safe(lambda: ground.set_actor_scale3d(unreal.Vector(ex, ey, 1.0)))
    _safe(lambda: ground.set_actor_location(unreal.Vector(cx * CM_PER_M, cy * CM_PER_M, z * CM_PER_M), False))
    info = {"centerM": [round(cx, 2), round(cy, 2)], "sizeM": [round(ex * 2, 1), round(ey * 2, 1)], "zM": round(z, 2)}
    _log("地面平面已同步: 中心 %s m，尺寸 %s m，z=%.2f m" % (info["centerM"], info["sizeM"], z))
    return info


def _glb_path_of(asset, manifest_path):
    """asset 的文件型模型绝对路径（roughness 贴图要从 glb 内嵌 BaseColor 取像素）。"""
    model = asset.get("model") or {}
    rel = str(model.get("path") or "")
    if not rel:
        return None
    return rel if os.path.isabs(rel) else os.path.join(_repo_root(manifest_path), rel)


def _asset_mesh_path(asset, manifest_path):
    """manifest 的一条 asset → UE 里可加载的网格资产路径（glb 先导入，引擎网格直接用）。"""
    model = asset.get("model") or {}
    rel = str(model.get("path") or "")
    if str(model.get("source", "file")) == "file" and rel.lower().endswith((".glb", ".gltf")):
        return _import_glb(asset, manifest_path)
    pkg = _mesh_asset_path(model.get("path"))
    if not bool(_safe(lambda: unreal.EditorAssetLibrary.does_asset_exist(pkg), False)):
        return _ensure_synthetic_mesh(pkg)
    return pkg


_SYNTHETIC_SEQ = [0]


def _ensure_synthetic_mesh(pkg):
    """合成 fixture 的网格在 UE 里不存在时，从 BasicShapes 复制一个占位网格。

    只有 `tools/run_pcg_chain.py --multi` 会设 `EAP_ALLOW_SYNTHETIC_MESH=1`；
    真实资产链路（文件型 glb / 引擎自带网格）走不到这里，避免"静默造资产"。
    """
    if os.environ.get("EAP_ALLOW_SYNTHETIC_MESH") != "1":
        raise RuntimeError("网格不存在（未开启 EAP_ALLOW_SYNTHETIC_MESH）: " + pkg)
    d = pkg.rsplit("/", 1)[0]
    _safe(lambda: unreal.EditorAssetLibrary.make_directory(d))
    base = SYNTHETIC_BASES[_SYNTHETIC_SEQ[0] % len(SYNTHETIC_BASES)]
    _SYNTHETIC_SEQ[0] += 1
    _safe(lambda: unreal.EditorAssetLibrary.duplicate_asset(base, pkg))
    if not bool(_safe(lambda: unreal.EditorAssetLibrary.does_asset_exist(pkg), False)):
        raise RuntimeError("合成网格复制失败: %s ← %s" % (pkg, base))
    _safe(lambda: unreal.EditorAssetLibrary.save_asset(pkg))
    _log("合成网格占位（测试用）: %s ← %s" % (pkg, base))
    return pkg


def _tag_of(comp):
    tags = _safe(lambda: list(comp.get_editor_property("component_tags") or []), [])
    return str(tags[0]) if tags else ""


def _multi_components(actor):
    """Actor 下带 `EAP_Asset_<i>` 标签的 ISM 组件（tag → comp）。"""
    out = {}
    if actor is None:
        return out
    for comp in _safe(lambda: list(actor.get_components_by_class(unreal.InstancedStaticMeshComponent)), []):
        tag = _tag_of(comp)
        if tag.startswith(MULTI_TAG_PREFIX):
            out[tag] = comp
    return out


def _component_for(actor, index, existing):
    """取（或新建）第 index 条 asset 的 ISM 组件，名字与标签都固定为 `EAP_Asset_<index>`。

    `comp.rename()` 在 5.7 的 Python 里可用（2026-11-23 实测，返回 True 且名字立刻生效）；
    component_tags 一起写，读回时不依赖组件创建顺序。
    """
    tag = "%s%d" % (MULTI_TAG_PREFIX, index)
    comp = existing.get(tag)
    if comp is not None:
        return comp
    comp = _add_ism(actor)
    _safe(lambda: comp.rename(tag))
    _safe(lambda: comp.set_editor_property("component_tags", [tag]))
    _log("多资产组件: %s（实际名 %s）" % (tag, _safe(lambda: comp.get_name())))
    return comp


def _add_transforms(comp, items):
    transforms = [_transform_of(it) for it in items]
    try:
        comp.add_instances(transforms, True)
    except Exception as exc:  # noqa: BLE001
        _warn("批量 add_instances 失败，逐条 add_instance: %r" % (exc,))
        for xf in transforms:
            comp.add_instance(xf, True)
    return int(_safe(lambda: comp.get_instance_count(), -1))


TOL_LOC_M = 1e-3
TOL_ROT_DEG = 0.1
TOL_SCALE = 1e-3


def _ang_diff(a, b):
    return abs(((float(a) - float(b) + 180.0) % 360.0) - 180.0)


def _compare_items(manifest_items, placed_items):
    """逐 asset 把读回实例和 manifest items 对上（最近匹配），给出计数与最大 transform 误差。"""
    used = [False] * len(placed_items)
    worst = {"t": 0.0, "r": 0.0, "s": 0.0}
    unmatched = abs(len(manifest_items) - len(placed_items))
    matched = 0
    for m in manifest_items:
        mt = m.get("t") or [0, 0, 0]
        ms = m.get("s") or [1, 1, 1]
        mr = m.get("r") or [0, 0, 0]
        best = None
        for vi, v in enumerate(placed_items):
            if used[vi]:
                continue
            vt = v.get("t") or []
            if len(vt) != 3:
                continue
            dt = max(abs(float(mt[i]) - float(vt[i])) for i in range(3))
            if best is None or dt < best[0]:
                best = (dt, vi)
        if best is None:
            continue
        used[best[1]] = True
        matched += 1
        v = placed_items[best[1]]
        vt, vs, vr = v.get("t") or [0, 0, 0], v.get("s") or [1, 1, 1], v.get("r") or [0, 0, 0]
        worst["t"] = max(worst["t"], max(abs(float(mt[i]) - float(vt[i])) for i in range(3)))
        worst["r"] = max(worst["r"], max(_ang_diff(mr[i], vr[i]) for i in range(3)))
        worst["s"] = max(worst["s"], max(abs(float(ms[i]) - float(vs[i])) for i in range(3)))
    ok = (matched == len(manifest_items) and unmatched == 0
          and worst["t"] < TOL_LOC_M and worst["r"] < TOL_ROT_DEG and worst["s"] < TOL_SCALE)
    return {
        "expected": len(manifest_items), "count": len(placed_items), "matched": matched,
        "maxLocErrM": round(worst["t"], 6), "maxRotErrDeg": round(worst["r"], 4),
        "maxScaleErr": round(worst["s"], 6), "ok": ok,
    }


def _place_multi(manifest, assets, manifest_path, level_pkg, save, out_path):
    """多资产落位：一个 Actor + 每 asset 一个 ISM 组件（EAP_Asset_<i>），幂等清空重落。"""
    _log("多资产落位: assets=%d -> %s" % (len(assets), level_pkg))
    created, level = _ensure_level(level_pkg)
    actor = _find_actor(ACTOR_LABEL)
    if actor is None:
        actor = _spawn_actor()
    existing = _multi_components(actor)
    used, states, all_readback = set(), [], []
    for i, asset in enumerate(assets):
        aid = asset.get("id")
        items = (asset.get("placements") or {}).get("items") or []
        if not items:
            raise RuntimeError("asset %s 没有 placements.items" % aid)
        mesh_path = _asset_mesh_path(asset, manifest_path)
        mesh = unreal.EditorAssetLibrary.load_asset(mesh_path)
        if mesh is None:
            raise RuntimeError("加载不到网格: " + mesh_path)
        tag = "%s%d" % (MULTI_TAG_PREFIX, i)
        used.add(tag)
        comp = _component_for(actor, i, existing)
        comp.set_static_mesh(mesh)
        mat_info = None
        model = asset.get("model") or {}
        if str(model.get("source", "file")) == "file":
            mat_tag = "".join(ch for ch in str(aid or "asset") if ch.isalnum() or ch == "_")
            mat_info = _normalize_materials(
                mesh, mat_tag, expected_alpha_modes=(asset.get("material") or {}).get("materials"),
                glb_path=_glb_path_of(asset, manifest_path))
            for slot, mi in enumerate(mat_info["overrideMaterials"]):
                if mi is not None:
                    _safe(lambda slot=slot, mi=mi: comp.set_material(slot, mi))
        _safe(lambda: comp.clear_instances())
        placed = _add_transforms(comp, items)
        readback = _instance_items(comp)
        all_readback.extend(readback)
        manifest_bbox = _bbox([it.get("t") or [0, 0, 0] for it in items])
        states.append({
            "index": i,
            "id": aid,
            "tag": tag,
            "component": _safe(lambda: str(comp.get_name())),
            "mesh": mesh_path,
            "meshInfo": _mesh_info(mesh),
            "materials": (mat_info or {}).get("evidence"),
            "expected": len(items),
            "placed": placed,
            "bboxManifestM": manifest_bbox,
            "bboxPlacedM": _bbox([it["t"] for it in readback]) if readback else None,
            "items": readback,
        })
        _log("多资产落位 %d/%d: %s %d/%d" % (i + 1, len(assets), aid, placed, len(items)))

    # 幂等：上一轮多出来的组件清空并移除（asset 数量变少时）
    for tag, comp in existing.items():
        if tag in used:
            continue
        _safe(lambda comp=comp: comp.clear_instances())
        _safe(lambda comp=comp: comp.destroy_component())
        _log("清理多余组件 %s" % tag)

    ground_info = _sync_ground(all_readback)
    saved = None
    if save:
        saved = _safe(lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
        _log("关卡已保存: %r" % (saved,))

    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.run/1",
        "multi": True,
        "manifest": manifest_path,
        "levelPackage": level_pkg,
        "level": level,
        "levelCreated": created,
        "actor": _safe(lambda: str(actor.get_actor_label())),
        "levelSaved": saved,
        "ground": ground_info,
        "expected": sum(s["expected"] for s in states),
        "placed": sum(s["placed"] for s in states),
        "bboxPlacedM": _bbox([it["t"] for it in all_readback]) if all_readback else None,
        "assets": states,
    }
    _dump(out_path, state)
    return state


def _verify_multi(manifest, assets, level_pkg, out_path):
    """新进程读回多资产关卡：逐 asset 找 `EAP_Asset_<i>` 组件，记数量/transform/材质。"""
    loaded = _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(level_pkg))
    _log("加载多资产落位关卡: %r" % (loaded,))
    mel = unreal.MaterialEditingLibrary
    actor = _find_actor(ACTOR_LABEL)
    existing = _multi_components(actor)
    states, all_items = [], []
    for i, asset in enumerate(assets):
        tag = "%s%d" % (MULTI_TAG_PREFIX, i)
        comp = existing.get(tag)
        items = _instance_items(comp) if comp is not None else []
        mesh = _safe(lambda: comp.get_editor_property("static_mesh")) if comp is not None else None
        mats = []
        mat_params = []
        if comp is not None:
            for j in range(int(_safe(lambda: comp.get_num_materials(), 0) or 0)):
                mi = _safe(lambda j=j: comp.get_material(j))
                mats.append(_safe(lambda mi=mi: str(mi.get_path_name()), None))
                texs = []
                for pname, kind in (("BaseColor", "basecolor"), ("Normal", "normal"), ("Roughness", "data")):
                    tex = _safe(lambda mi=mi, pname=pname:
                                mel.get_material_instance_texture_parameter_value(mi, pname))
                    if tex is not None and not isinstance(tex, str):
                        texs.append(_read_texture_settings(tex, kind))
                mat_params.append({
                    "material": _safe(lambda mi=mi: str(mi.get_path_name()), None),
                    "roughnessTex": _safe(lambda mi=mi: str(
                        mel.get_material_instance_texture_parameter_value(mi, "Roughness").get_path_name()), None),
                    "roughnessScale": _safe(lambda mi=mi: float(
                        mel.get_material_instance_scalar_parameter_value(mi, "RoughnessScale")), None),
                    "textureSettings": texs,
                })
        all_items.extend(items)
        cmp = _compare_items(((asset.get("placements") or {}).get("items")) or [], items)
        states.append({
            "index": i,
            "id": asset.get("id"),
            "tag": tag,
            "component": _safe(lambda: str(comp.get_name())) if comp is not None else None,
            "mesh": str(mesh.get_path_name()) if mesh else None,
            "materials": mats,
            "materialParams": mat_params,
            "bboxPlacedM": _bbox([it["t"] for it in items]) if items else None,
            "items": items,
            **cmp,
        })
    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.verify/1",
        "multi": True,
        "levelPackage": level_pkg,
        "actor": _safe(lambda: str(actor.get_actor_label())) if actor is not None else None,
        "count": sum(s["count"] for s in states),
        "bboxPlacedM": _bbox([it["t"] for it in all_items]) if all_items else None,
        "ok": all(s["ok"] for s in states),
        "assetsMatched": sum(1 for s in states if s["ok"]),
        "assets": states,
    }
    _log("多资产验证读回: assets=%d count=%d ok=%s" % (len(states), state["count"], state["ok"]))
    _dump(out_path, state)
    return state


def place(manifest_path=None, level_pkg=None, save=True, out_path=None):
    manifest_path = manifest_path or os.environ.get("EAP_MANIFEST") or DEFAULT_MANIFEST
    level_pkg = level_pkg or os.environ.get("EAP_LEVEL_PKG") or DEFAULT_LEVEL
    out_path = out_path or os.environ.get("EAP_PLACE_OUT") or DEFAULT_OUT
    if os.environ.get("EAP_PLACE_SAVE") == "0":
        save = False

    manifest = _read(manifest_path)
    assets = manifest.get("assets") or []
    if len(assets) > 1:
        return _place_multi(manifest, assets, manifest_path, level_pkg, save, out_path)
    if len(assets) != 1:
        _warn("本试点只处理单资产 manifest，实际 assets=%d（只用第一个）" % len(assets))
    asset = assets[0]
    pl = asset.get("placements") or {}
    items = pl.get("items") or []
    if not items:
        raise RuntimeError("manifest 里没有 placements.items：%s" % manifest_path)

    model = asset.get("model") or {}
    if str(model.get("source", "file")) == "file" and str(model.get("path", "")).lower().endswith((".glb", ".gltf")):
        mesh_path = _import_glb(asset, manifest_path)
    else:
        mesh_path = _mesh_asset_path(model.get("path"))
    mesh = unreal.EditorAssetLibrary.load_asset(mesh_path)
    if mesh is None:
        raise RuntimeError("加载不到网格: " + mesh_path)
    _log("目标关卡=%s 网格=%s 条数=%d" % (level_pkg, mesh_path, len(items)))

    created, level = _ensure_level(level_pkg)
    actor = _find_actor(ACTOR_LABEL)
    if actor is None:
        actor = _spawn_actor()
    comp = _ism_of(actor)
    if comp is None:
        comp = _add_ism(actor)
    comp.set_static_mesh(mesh)
    mat_info = None
    if str(model.get("source", "file")) == "file":
        tag = os.path.splitext(os.path.basename(str(model.get("path") or "asset")))[0]
        tag = "".join(ch for ch in tag if ch.isalnum() or ch == "_")
        mat_info = _normalize_materials(
            mesh, tag, expected_alpha_modes=(asset.get("material") or {}).get("materials"),
            glb_path=_glb_path_of(asset, manifest_path))
        for slot, mi in enumerate(mat_info["overrideMaterials"]):
            if mi is not None:
                _safe(lambda slot=slot, mi=mi: comp.set_material(slot, mi))
    _safe(lambda: comp.clear_instances())

    transforms = [_transform_of(it) for it in items]
    try:
        comp.add_instances(transforms, True)
    except Exception as exc:  # noqa: BLE001
        _warn("批量 add_instances 失败，逐条 add_instance: %r" % (exc,))
        for xf in transforms:
            comp.add_instance(xf, True)
    placed = int(_safe(lambda: comp.get_instance_count(), -1))
    _log("落位完成: %d / %d" % (placed, len(items)))

    readback = _instance_items(comp)
    manifest_bbox = _bbox([it.get("t") or [0, 0, 0] for it in items])
    placed_bbox = _bbox([it["t"] for it in readback]) if readback else None
    mesh_info = _mesh_info(mesh)
    ground_info = _sync_ground(readback)
    saved = None
    if save:
        saved = _safe(lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
        _log("关卡已保存: %r" % (saved,))

    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.run/1",
        "manifest": manifest_path,
        "levelPackage": level_pkg,
        "level": level,
        "levelCreated": created,
        "actor": _safe(lambda: str(actor.get_actor_label())),
        "component": _safe(lambda: str(comp.get_name())),
        "mesh": mesh_path,
        "meshInfo": mesh_info,
        "materials": (mat_info or {}).get("evidence"),
        "materialOverrides": [_safe(lambda mi=mi: str(mi.get_path_name())) if mi is not None else None
                              for mi in ((mat_info or {}).get("overrideMaterials") or [])],
        "expected": len(items),
        "placed": placed,
        "levelSaved": saved,
        "ground": ground_info,
        "bboxManifestM": manifest_bbox,
        "bboxPlacedM": placed_bbox,
        "items": readback,
    }
    _dump(out_path, state)
    return state


def verify(level_pkg=None, out_path=None):
    level_pkg = level_pkg or os.environ.get("EAP_LEVEL_PKG") or DEFAULT_LEVEL
    out_path = out_path or os.environ.get("EAP_VERIFY_OUT") or DEFAULT_VERIFY_OUT
    manifest_path = os.environ.get("EAP_MANIFEST") or DEFAULT_MANIFEST
    manifest = _read(manifest_path) if os.path.isfile(manifest_path) else {}
    assets = manifest.get("assets") or []
    if len(assets) > 1:
        return _verify_multi(manifest, assets, level_pkg, out_path)
    loaded = _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(level_pkg))
    _log("加载落位关卡: %r" % (loaded,))
    actor = _find_actor(ACTOR_LABEL)
    comp = _ism_of(actor) if actor is not None else None
    items = _instance_items(comp) if comp is not None else []
    mesh = _safe(lambda: comp.get_editor_property("static_mesh")) if comp is not None else None
    mats = []
    if comp is not None:
        for i in range(int(_safe(lambda: comp.get_num_materials(), 0) or 0)):
            mi = _safe(lambda i=i: comp.get_material(i))
            mats.append(_safe(lambda mi=mi: str(mi.get_path_name()), None))
    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.verify/1",
        "levelPackage": level_pkg,
        "actor": _safe(lambda: str(actor.get_actor_label())) if actor is not None else None,
        "component": _safe(lambda: str(comp.get_name())) if comp is not None else None,
        "mesh": str(mesh.get_path_name()) if mesh else None,
        "materials": mats,
        "count": len(items),
        "bboxPlacedM": _bbox([it["t"] for it in items]) if items else None,
        "items": items,
    }
    _log("验证读回: count=%d" % len(items))
    _dump(out_path, state)
    return state


def _level_actors():
    eas = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem), None)
    if eas is None or isinstance(eas, str):
        return []
    return _safe(lambda: list(eas.get_all_level_actors()), []) or []


def _find_by_class(cls):
    for a in _level_actors():
        if _safe(lambda a=a: str(a.get_class().get_name()), "") == cls.__name__:
            return a
    return None


def _spawn(cls, label=None):
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    a = eas.spawn_actor_from_class(cls, unreal.Vector(0, 0, 0), unreal.Rotator())
    if label:
        _safe(lambda: a.set_actor_label(label))
    _log("新增 Actor: %s %s" % (cls.__name__, label or ""))
    return a


def stage(level_pkg=None, out_path=None, save=True):
    """给空关卡补灯光/天空/地面，让视口截图能用（不是落位逻辑的一部分）。

    **只能在真实编辑器里跑**：2026-11-22 无头（-unattended -nullrhi）跑这个会
    EXCEPTION_INT_DIVIDE_BY_ZERO 崩溃（SkyAtmosphere/SkyLight 需要渲染管线）。
    真实编辑器里可以用一行 Python 等价实现（或直接跑本函数）。
    """
    level_pkg = level_pkg or os.environ.get("EAP_LEVEL_PKG") or DEFAULT_LEVEL
    out_path = out_path or os.environ.get("EAP_STAGE_OUT") or r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_level_stage.json"
    if os.environ.get("EAP_PLACE_SAVE") == "0":
        save = False
    loaded = _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(level_pkg))
    _log("加载落位关卡: %r" % (loaded,))
    created = []

    if _find_by_class(unreal.DirectionalLight) is None:
        dl = _spawn(unreal.DirectionalLight, "EAP_Sun")
        _safe(lambda: dl.set_actor_rotation(unreal.Rotator(pitch=-42.0, yaw=35.0, roll=0.0), False))
        created.append("DirectionalLight")
    if _find_by_class(unreal.SkyLight) is None:
        _spawn(unreal.SkyLight, "EAP_SkyLight")
        created.append("SkyLight")
    if _find_by_class(unreal.SkyAtmosphere) is None:
        _spawn(unreal.SkyAtmosphere, "EAP_SkyAtmosphere")
        created.append("SkyAtmosphere")
    if _find_by_class(unreal.ExponentialHeightFog) is None:
        _spawn(unreal.ExponentialHeightFog, "EAP_Fog")
        created.append("ExponentialHeightFog")

    ground = None
    for a in _level_actors():
        if _safe(lambda a=a: str(a.get_actor_label()), "") == "EAP_Ground":
            ground = a
            break
    actor = _find_actor(ACTOR_LABEL)
    comp = _ism_of(actor) if actor is not None else None
    items = _instance_items(comp) if comp is not None else []
    if ground is None and items:
        bx = _bbox([it["t"] for it in items])
        cx = (bx["min"][0] + bx["max"][0]) / 2.0 * CM_PER_M
        cy = (bx["min"][1] + bx["max"][1]) / 2.0 * CM_PER_M
        ex = (bx["max"][0] - bx["min"][0]) / 2.0 + 25.0
        ey = (bx["max"][1] - bx["min"][1]) / 2.0 + 25.0
        ground = _spawn(unreal.StaticMeshActor, "EAP_Ground")
        plane = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Plane")
        _safe(lambda: ground.static_mesh_component.set_static_mesh(plane))
        ground.set_actor_scale3d(unreal.Vector(ex, ey, 1.0))
        ground.set_actor_location(unreal.Vector(cx, cy, -50.0), False)
        created.append("EAP_Ground")

    saved = None
    if save:
        saved = _safe(lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
        _log("关卡已保存: %r" % (saved,))
    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.stage/1",
        "levelPackage": level_pkg,
        "created": created,
        "levelSaved": saved,
        "actorCount": len(_level_actors()),
    }
    _dump(out_path, state)
    return state


def main():
    mode = (os.environ.get("EAP_PLACE_MODE") or "place").lower()
    if mode == "verify":
        return verify()
    if mode == "stage":
        return stage()
    return place()


if __name__ == "__main__":
    main()
