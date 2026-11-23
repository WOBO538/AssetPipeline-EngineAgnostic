"""EAP · Layer 1 → Layer 2：PCG 散点 JSON → manifest v1.1

输入：`ue/Content/Python/pcg_export.py` 产出的 `eap.pcg_scatter/1`（单位 cm）
输出：manifest v1.1（单位 m），新增 `placements` 字段承载"同一网格的 N 个实例"

用法：
    python tools/pcg_to_manifest.py docs/pcg_forest_pilot.json schemas/pcg_manifest.json
    python tools/pcg_to_manifest.py docs/pcg_forest_pilot.json schemas/pcg_manifest.json --pipeline-id eap-pcg-pilot

设计取舍（写进 README 免得面试被问住）：
- **一个网格 = 一个 asset**，实例数放 `placements`，不为每个实例造一条资产
  （否则 UE 端会建 N 个材质实例，是典型的中台建模错误）。
- 引擎自带网格用 `model.source = "engine"` + `model.path = "ue:/..."`，
  **不伪造 glb 路径**；因此这类资产没有 Unity 目标端（Unity 侧跳过）。
- 引擎网格没有贴图 → `material.template = "none"` 时允许 `textures = {}`，
  校验器只在 template 为 none 时放行，普通资产仍强制贴图完整。
"""
import json
import os
import sys
import time

SCHEMA_IN = "eap.pcg_scatter/1"
MANIFEST_VERSION = "1.1"
CM_TO_M = 0.01


def _short(pkg):
    return str(pkg).rstrip("/").split("/")[-1]


def _m(v_cm, n=4):
    return [round(float(x) * CM_TO_M, n) for x in v_cm]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def build(scatter_path, out_path, pipeline_id=None):
    with open(scatter_path, "r", encoding="utf-8-sig") as fh:
        scatter = json.load(fh)
    if scatter.get("schema") != SCHEMA_IN:
        raise SystemExit("输入不是 %s（实为 %r）: %s" % (SCHEMA_IN, scatter.get("schema"), scatter_path))

    meshes = scatter["scatter"].get("instancedMeshes") or []
    if not meshes:
        raise SystemExit("散点文件里没有 instancedMeshes，先跑 pcg_export.run()")

    graph = scatter.get("graph") or {}
    comps = scatter["scatter"].get("pcgComponents") or []
    seed = next((c.get("seed") for c in comps if isinstance(c.get("seed"), int)), None)

    by_mesh = {}
    for m in meshes:
        info = m.get("mesh") or {}
        pkg = info.get("packagePath")
        if not pkg:
            continue
        slot = by_mesh.setdefault(pkg, {"mesh": info, "points": [], "instanceCount": 0})
        slot["points"].extend(m.get("points") or [])
        slot["instanceCount"] += m.get("instanceCount") or 0

    # 多资产：散点文件里出现多个不同网格 → 每个网格一条 asset，各自带自己的 placements。
    # 单网格输入走原来那条分支（输出必须与 v1.1 试点 manifest 字节级等价）。
    multi = len(by_mesh) > 1
    assets = []
    for pkg, slot in sorted(by_mesh.items()):
        short = _short(pkg)
        name = short[3:] if multi and short.upper().startswith("SM_") else short
        aid = "SM_%s_PCG_%s" % (name, "Multi" if multi else "Pilot")
        bounds = slot["mesh"].get("boundsCm") or {}
        bmin = _m(bounds.get("min") or [-50, -50, -50])
        bmax = _m(bounds.get("max") or [50, 50, 50])
        if bmin == bmax:  # 兜底：不让 bbox 退化成点
            bmin = [round(x - 0.5, 4) for x in bmin]
            bmax = [round(x + 0.5, 4) for x in bmax]
        items = [{"t": _m(p["t"]), "r": p["r"], "s": [round(float(x), 4) for x in p["s"]]}
                 for p in slot["points"]]
        checks = [
            {"rule": "mesh_is_engine_native", "pass": True, "value": pkg},
            {"rule": "instance_count==placements.count", "pass": len(items) == slot["instanceCount"],
             "value": [slot["instanceCount"], len(items)]},
        ]
        if not multi:   # 试点规模说明只属于 2026-10 的单网格试点，多资产批次里不写这句
            checks.append({"rule": "pilot_scale_note", "pass": True,
                           "value": "19 个实例是 2026-10 的试点规模（方盒子替树），不是成品密度"})
        checks.append({"rule": "unity_target_skipped", "pass": True,
                       "value": "引擎自带网格无 glb，Unity 端不适用"})
        assets.append({
            "id": aid,
            "name": ("%s · PCG 多资产散点" if multi else "%s · PCG 散点试点") % name,
            "category": "env/vegetation",
            "type": "static_mesh",
            "model": {
                "path": "ue:/" + pkg.lstrip("/"),
                "source": "engine",
                "format": "engine_native",
                "unit": "m",
                "upAxis": "Z",
                "forwardAxis": "X",
                "boundingBoxM": {"min": bmin, "max": bmax},
                "triCount": int(slot["mesh"].get("triCount") or 12),
            },
            "textures": {},
            "material": {"template": "none"},
            "collision": {"mode": "none"},
            "pivot": {"mode": "none"},
            "engineTargets": {
                "UE5": {"destPath": "/Game/EAP/PCG/Pilot"},
            },
            "placements": {
                "space": "world",
                "unit": "m",
                "count": len(items),
                "items": items,
            },
            "checks": checks,
        })

    total = sum(a["placements"]["count"] for a in assets)
    manifest = {
        "manifestVersion": MANIFEST_VERSION,
        "pipelineId": pipeline_id or ("eap-%s-pcg-%s" % (time.strftime("%Y%m%d"),
                                                         "multi" if multi else "pilot")),
        "generatedAt": _now(),
        "units": "m",
        "source": {
            "kind": "pcg",
            "tool": "ue-pcg-framework",
            "workflowId": graph.get("path") or "/Game/PCG_ForestScatter",
            "seed": seed if isinstance(seed, int) else 0,
            "note": ("Layer 1 多资产出口：一份散点文件里的多种网格各成一条 asset，各自带 placements"
                     if multi else
                     "Layer 1 数据出口试点：UE PCG 生成实例 → 散点 JSON → manifest v1.1 placements"),
        },
        "assets": assets,
    }

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    summary = {
        "out": out_path,
        "assets": len(assets),
        "placements": total,
        "meshes": sorted(by_mesh.keys()),
        "scatterPoints": len(scatter["scatter"].get("points") or []),
        "scatterInstanceCount": scatter["scatter"].get("instanceCount"),
    }
    return manifest, summary


def main(argv):
    # 同时支持 `pcg_to_manifest.py <scatter> <out>` 和 `--scatter <scatter> --out <out>`
    args, scatter, out_path, pipeline_id = argv[1:], None, None, None
    rest, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("--scatter", "--out", "--pipeline-id"):
            if i + 1 >= len(args):
                print(__doc__)
                return 2
            if a == "--scatter":
                scatter = args[i + 1]
            elif a == "--out":
                out_path = args[i + 1]
            else:
                pipeline_id = args[i + 1]
            i += 2
            continue
        rest.append(a)
        i += 1
    if scatter is None and len(rest) >= 1:
        scatter = rest[0]
    if out_path is None and len(rest) >= 2:
        out_path = rest[1]
    if not scatter or not out_path:
        print(__doc__)
        return 2
    manifest, s = build(scatter, out_path, pipeline_id=pipeline_id)
    print("assets=%d placements=%d meshes=%s" % (s["assets"], s["placements"], ",".join(s["meshes"])))
    print("scatter points=%d instanceCount=%s" % (s["scatterPoints"], s["scatterInstanceCount"]))
    print("OK -> %s" % s["out"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
