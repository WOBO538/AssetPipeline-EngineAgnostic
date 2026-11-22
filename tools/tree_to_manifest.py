"""EAP · Layer 2：真实树资产（glTF）→ manifest v1.1（双端可用）

    python tools/tree_to_manifest.py \
        --source assets/raw/trees/source_assets.json \
        --scatter docs/pcg_forest_pilot.json \
        --out schemas/tree_manifest.json --species conifer

和 `pcg_to_manifest.py` 的分工：
- `pcg_to_manifest.py` 处理 `model.source = "engine"`（引擎 Cube 占位，Unity 端跳过）；
- 本工具处理 `model.source = "file"`（磁盘上的 .glb），**双端都有目标**，这是管线第一次吃真资产。

三条设计决定（面试会被问）：
1. **不信任 sidecar 的数字**：bbox / 三角面 / 材质 / 外部依赖全部自己解析 GLB 二进制复算，
   再和 `source_assets.json` 对账，对不上就报错——中间件的活就是"别信上游"。
2. **轴心补偿**：散点的 t 是引擎 Cube 的**中心**（Cube 半高 0.5 m），而树的轴心在**树根底部**。
   直接照抄 t 会让 1892 棵树全体悬空 0.5 m，所以落位前统一减半高（`pivotZOffsetM`）。
3. **贴图内嵌**：glb 自带 PNG（无外部 uri），manifest 里 `material.template = "embedded"`、
   `textures = {}`，由校验器放行；Unity/UE 各自从 glb 里解出贴图。
"""
import argparse
import json
import os
import struct
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_VERSION = "1.1"
CM_TO_M = 0.01
PERMISSIVE = ("cc0-1.0", "cc-by-4.0", "cc-by-3.0", "cc-by-2.0", "public domain (cc0)")
TRI_BUDGET_LOD0 = 20000


# ---------------------------------------------------------------- GLB 解析（不依赖第三方库）
def parse_glb(path):
    with open(path, "rb") as fh:
        data = fh.read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("不是 glTF 二进制文件: " + path)
    off, js = 12, None
    while off < len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        body = data[off + 8: off + 8 + clen]
        if ctype == 0x4E4F534A:
            js = json.loads(body.decode("utf-8"))
        off += 8 + clen + ((4 - clen % 4) % 4 if clen % 4 else 0)
    if js is None:
        raise ValueError("GLB 里没有 JSON chunk: " + path)
    return {"json": js, "version": version, "declared_len": length, "actual_len": len(data)}


def _node_matrix(n):
    if "matrix" in n:
        m = n["matrix"]
        return [[m[0], m[4], m[8], m[12]], [m[1], m[5], m[9], m[13]],
                [m[2], m[6], m[10], m[14]], [m[3], m[7], m[11], m[15]]]
    t = n.get("translation", [0, 0, 0])
    r = n.get("rotation", [0, 0, 0, 1])
    s = n.get("scale", [1, 1, 1])
    x, y, z, w = r
    r00, r01, r02 = 1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)
    r10, r11, r12 = 2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)
    r20, r21, r22 = 2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)
    return [[r00 * s[0], r01 * s[1], r02 * s[2], t[0]],
            [r10 * s[0], r11 * s[1], r12 * s[2], t[1]],
            [r20 * s[0], r21 * s[1], r22 * s[2], t[2]],
            [0, 0, 0, 1]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _apply(m, p):
    return [m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3)]


def glb_stats(path):
    g = parse_glb(path)["json"]
    scene = g["scenes"][g.get("scene", 0)]
    acc = [[1e9, -1e9] for _ in range(3)]

    def walk(idx, parent):
        n = g["nodes"][idx]
        m = _mul(parent, _node_matrix(n))
        if "mesh" in n:
            for prim in g["meshes"][n["mesh"]]["primitives"]:
                pos = g["accessors"][prim["attributes"]["POSITION"]]
                mn, mx = pos.get("min"), pos.get("max")
                if not mn:
                    continue
                for cx in (mn[0], mx[0]):
                    for cy in (mn[1], mx[1]):
                        for cz in (mn[2], mx[2]):
                            p = _apply(m, [cx, cy, cz])
                            for d in range(3):
                                acc[d][0] = min(acc[d][0], p[d])
                                acc[d][1] = max(acc[d][1], p[d])
        for c in n.get("children", []):
            walk(c, m)

    for r in scene["nodes"]:
        walk(r, _node_matrix({}))

    tris = 0
    tangents = False
    for mesh in g["meshes"]:
        for prim in mesh["primitives"]:
            if "indices" in prim:
                tris += g["accessors"][prim["indices"]]["count"] // 3
            else:
                tris += g["accessors"][prim["attributes"]["POSITION"]]["count"] // 3
            tangents = tangents or "TANGENT" in prim["attributes"]

    mats = [{"name": m.get("name"), "alphaMode": m.get("alphaMode", "OPAQUE"),
             "doubleSided": bool(m.get("doubleSided")),
             "metallicFactor": (m.get("pbrMetallicRoughness") or {}).get("metallicFactor"),
             "roughnessFactor": (m.get("pbrMetallicRoughness") or {}).get("roughnessFactor"),
             "hasMetallicRoughnessTexture": "metallicRoughnessTexture" in (m.get("pbrMetallicRoughness") or {}),
             "hasNormalTexture": "normalTexture" in m}
            for m in g.get("materials", [])]
    bmin = [round(acc[d][0], 4) for d in range(3)]
    bmax = [round(acc[d][1], 4) for d in range(3)]
    return {
        "generator": (g.get("asset") or {}).get("generator"),
        "version": (g.get("asset") or {}).get("version"),
        "nodes": len(g.get("nodes", [])),
        "meshes": len(g.get("meshes", [])),
        "materials": mats,
        "triCount": tris,
        "hasTangents": tangents,
        "bbox": {"min": bmin, "max": bmax,
                 "size": [round(bmax[d] - bmin[d], 4) for d in range(3)]},
        "externalURIs": [b.get("uri") for b in g.get("buffers", []) if b.get("uri")] +
                        [i.get("uri") for i in g.get("images", []) if i.get("uri")],
        "images": len(g.get("images", [])),
        "animations": len(g.get("animations", [])),
        "extensionsUsed": list(g.get("extensionsUsed") or []),
    }


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _m(cm):
    return round(float(cm) * CM_TO_M, 4)


def check_entry(entry, stats):
    """硬门槛检查：返回 (errors, warnings)。全部数据来自 GLB 复算 + sidecar 对账。"""
    errors, warnings = [], []
    lic = str(entry.get("license", "")).strip().lower()
    if not lic:
        errors.append("缺 license")
    elif not (lic.startswith("cc0") or lic.startswith("cc-by") or lic.startswith("public domain")):
        errors.append("license 不是 CC0/CC-BY: %r" % entry.get("license"))
    if entry.get("format", "glb").lower() != "glb":
        errors.append("本工具只收 .glb（实为 %r）" % entry.get("format"))
    if str(entry.get("unit", "")).lower() != "m":
        errors.append("unit 必须是 m，实为 %r" % entry.get("unit"))
    if str(entry.get("up_axis", "")).upper() != "Y":
        errors.append("up_axis 必须是 Y，实为 %r" % entry.get("up_axis"))
    if stats["versions_ok"] is False:
        errors.append("glTF 版本不是 2.x")
    if stats["meshes"] != 1:
        errors.append("一个 .glb 必须只含 1 个网格（实为 %d 个），单棵树要求" % stats["meshes"])
    if stats["externalURIs"]:
        errors.append("存在外部依赖 uri（要求贴图内嵌）: %r" % stats["externalURIs"])
    if stats["animations"]:
        errors.append("含动画（要求静态树）: %d 段" % stats["animations"])
    bmin, bmax = stats["bbox"]["min"], stats["bbox"]["max"]
    if abs(bmin[1]) > 0.01:
        errors.append("树根没落在原点：bbox min y = %.4f m（要求 |y| ≤ 0.01）" % bmin[1])
    if bmax[1] < 1.0:
        errors.append("树高可疑：bbox max y = %.4f m" % bmax[1])
    if stats["triCount"] > TRI_BUDGET_LOD0:
        errors.append("三角面超预算：%d > %d" % (stats["triCount"], TRI_BUDGET_LOD0))
    if not stats["hasTangents"]:
        warnings.append("网格无 TANGENT 属性，由导入端生成切线空间（UE/Unity 都会自动处理）")
    blend = [m["name"] for m in stats["materials"] if str(m["alphaMode"]).upper() == "BLEND"]
    if blend:
        warnings.append("材质 %r 是 BLEND（半透明）：落 1800+ 实例会有排序/开销问题，正式出图应改 MASKED" % blend)
    metal = [m for m in stats["materials"] if (m.get("metallicFactor") or 0) > 0.05]
    if metal:
        warnings.append("材质 metallicFactor>0（%s）：转换器默认值，UE 里会偏塑料感"
                        % ", ".join("%s=%.2f" % (m["name"], m["metallicFactor"]) for m in metal))
    # sidecar 对账
    for key, mine in (("height_m", round(bmax[1] - bmin[1], 3)),
                      ("tri_count_lod0", stats["triCount"]),
                      ("materials", len(stats["materials"]))):
        theirs = entry.get(key)
        if theirs is None or str(theirs).lower() == "unknown":
            warnings.append("sidecar 未给 %s（我复算为 %r）" % (key, mine))
            continue
        try:
            same = abs(float(theirs) - float(mine)) <= max(0.01, abs(float(mine)) * 0.01)
        except (TypeError, ValueError):
            same = False
        if not same:
            errors.append("sidecar %s=%r 与我复算的 %r 对不上" % (key, theirs, mine))
    return errors, warnings


def build(source_path, scatter_path, out_path, species="conifer", pipeline_id=None):
    entries = read_json(source_path)
    if isinstance(entries, dict):
        entries = [entries]
    entry = next((e for e in entries if str(e.get("file", "")).lower().startswith(species)), None)
    if entry is None:
        raise SystemExit("source_assets.json 里没有 file 以 %r 开头的资产：%s" % (species, source_path))
    glb_rel = os.path.join("assets", "raw", "trees", entry["file"]).replace("\\", "/")
    glb_abs = os.path.join(REPO, glb_rel)
    if not os.path.isfile(glb_abs):
        raise SystemExit("glb 不存在: " + glb_abs)

    stats = glb_stats(glb_abs)
    stats["versions_ok"] = str(stats.get("version", "")).startswith("2")
    errors, warnings = check_entry(entry, stats)
    if errors:
        for e in errors:
            print("ERROR " + e)
        raise SystemExit("硬门槛未通过，不生成 manifest（%d 条错误）" % len(errors))

    scatter = read_json(scatter_path)
    meshes = (scatter.get("scatter") or {}).get("instancedMeshes") or []
    if not meshes:
        raise SystemExit("散点文件里没有 instancedMeshes")
    slot = meshes[0]
    cube = (slot.get("mesh") or {}).get("boundsCm") or {"min": [-50, -50, -50], "max": [50, 50, 50]}
    bmin_cm, bmax_cm = cube["min"], cube["max"]
    # 引擎网格轴心在中心：落位前要把 t 从"中心"换算成"底部中心的底面"
    pivot_z_cm = ((bmax_cm[2] + bmin_cm[2]) / 2.0) - bmin_cm[2]
    pivot_z_m = round(pivot_z_cm * CM_TO_M, 4)
    pts = slot.get("points") or []
    if not pts:
        raise SystemExit("散点里没有 points")

    items = []
    for p in pts:
        t = p.get("t") or [0, 0, 0]
        items.append({
            "t": [_m(t[0]), _m(t[1]), _m(t[2]) - pivot_z_m],
            "r": [round(float(x), 3) for x in (p.get("r") or [0, 0, 0])],
            "s": [round(float(x), 4) for x in (p.get("s") or [1, 1, 1])],
        })

    name = str(entry.get("source_model_title") or species)
    asset_id = "SM_Tree_%s_PCG_Pilot" % species
    dest = "/Game/EAP/Trees"
    mesh_name = entry["file"].rsplit(".", 1)[0]
    ue_mesh = "%s/%s/StaticMeshes/%s.%s" % (dest, mesh_name, mesh_name, mesh_name)
    bbox = stats["bbox"]
    asset = {
        "id": asset_id,
        "name": "%s · 真实树试点（PCG %d 实例）" % (name, len(items)),
        "category": "env/vegetation",
        "type": "static_mesh",
        "model": {
            "path": glb_rel,
            "source": "file",
            "format": "gltf2.0",
            "unit": "m",
            "upAxis": "Y",
            "forwardAxis": "X",
            "boundingBoxM": {"min": bbox["min"], "max": bbox["max"]},
            "triCount": stats["triCount"],
            "generator": stats["generator"],
        },
        "textures": {},
        "material": {"template": "embedded", "source": "gltf_pbr", "materials": stats["materials"]},
        "collision": {"mode": "none"},
        "pivot": {
            "mode": "base_center",
            "zOffsetM": pivot_z_m,
            "note": "glTF 根节点已把树根底部中心归零；散点 t 是引擎 Cube 中心（半高 %.3f m），落位前统一扣掉，避免全体悬空" % pivot_z_m,
        },
        "license": {
            "id": entry.get("license"),
            "url": entry.get("license_url"),
            "author": entry.get("author"),
            "source": entry.get("source_url"),
            "evidence": entry.get("license_evidence"),
        },
        "engineTargets": {
            "UE5": {
                "destPath": dest,
                "meshPath": ue_mesh,
                "sourceGlb": glb_rel,
                "note": "Interchange 导入 .glb 后网格落在 <dest>/<文件名>/StaticMeshes/，材质由 Interchange 自动生成",
            },
            "Unity": {
                "destPath": "Assets/EAP/Trees",
                "sourceGlb": glb_rel,
                "note": "Unity 端直接导 .glb；placements 同一份数据",
            },
        },
        "placements": {"space": "world", "unit": "m", "count": len(items), "items": items},
        "checks": [
            {"rule": "license_permissive", "pass": True, "value": entry.get("license")},
            {"rule": "glb_is_gltf2", "pass": True, "value": stats["version"]},
            {"rule": "unit_metres", "pass": True, "value": entry.get("unit")},
            {"rule": "up_axis_y", "pass": True, "value": entry.get("up_axis")},
            {"rule": "pivot_at_trunk_base", "pass": abs(bbox["min"][1]) <= 0.01, "value": bbox["min"][1]},
            {"rule": "single_tree", "pass": stats["meshes"] == 1, "value": stats["meshes"]},
            {"rule": "tri_budget_lod0", "pass": stats["triCount"] <= TRI_BUDGET_LOD0, "value": stats["triCount"]},
            {"rule": "textures_embedded", "pass": not stats["externalURIs"], "value": stats["images"]},
            {"rule": "sidecar_matches_glb", "pass": True, "value": "height/tri/materials 已复算对账"},
            {"rule": "pivot_compensated", "pass": True,
             "value": {"engineMeshCenterZm": pivot_z_m, "from": "/Engine/BasicShapes/Cube"}},
        ],
    }
    manifest = {
        "manifestVersion": MANIFEST_VERSION,
        "pipelineId": pipeline_id or ("eap-tree-%s-%s" % (species, time.strftime("%Y%m%d"))),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "units": "m",
        "source": {
            "kind": "glb_ingest",
            "tool": "interchange-gltf",
            "workflowId": (scatter.get("graph") or {}).get("path") or "/Game/PCG_ForestScatter",
            "scatter": os.path.relpath(scatter_path, REPO).replace("\\", "/"),
            "sourceAssets": os.path.relpath(source_path, REPO).replace("\\", "/"),
            "note": "散点来自 UE PCG（导入时是 Cube 占位），落位网格换成真实树 glb；双端消费",
        },
        "assets": [asset],
    }
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    summary = {"out": out_path, "asset": asset_id, "species": species, "placements": len(items),
               "glb": glb_rel, "triCount": stats["triCount"], "heightM": bbox["size"][1],
               "pivotZOffsetM": pivot_z_m, "warnings": warnings,
               "ueMeshPath": ue_mesh, "unityDest": "Assets/EAP/Trees"}
    return manifest, summary


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.path.join(REPO, "assets", "raw", "trees", "source_assets.json"))
    ap.add_argument("--scatter", default=os.path.join(REPO, "docs", "pcg_forest_pilot.json"))
    ap.add_argument("--out", default=os.path.join(REPO, "schemas", "tree_manifest.json"))
    ap.add_argument("--species", default="conifer")
    ap.add_argument("--pipeline-id", default=None)
    args = ap.parse_args(argv[1:])
    manifest, s = build(args.source, args.scatter, args.out, species=args.species, pipeline_id=args.pipeline_id)
    for w in s["warnings"]:
        print("WARN  " + w)
    print("asset=%s glb=%s tris=%d height=%.3fm placements=%d pivotZOffset=%.3fm"
          % (s["asset"], s["glb"], s["triCount"], s["heightM"], s["placements"], s["pivotZOffsetM"]))
    print("UE5 mesh -> %s" % s["ueMeshPath"])
    print("OK -> %s" % s["out"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
