"""EAP · 合成「真实双树种」散点 fixture（固定 seed，可复现）

和 `make_multi_asset_fixture.py` 的区别：
- 那份是**机制**验证（20 个 BasicShapes 占位网格）；
- 这份是**真实资产**验证：散点里按 `species` 分成 conifer / broadleaf，
  网格元数据直接来自 `assets/raw/trees/*.glb` 的二进制复算（不是占位 Cube）。

地块沿用 200×200 m、密度 0.05/m² → 1892 点；t 直接是树根底部的世界坐标（`scatterOrigin=tree_base`），
所以落位端不需要再做 Cube 半高补偿。

用法：
    python tools/make_multi_species_fixture.py --out docs/pcg_multi_species_fixture.json

固定 seed + 不写当前时间 → 两次生成 SHA256 一致。
"""
import argparse
import json
import os
import random
import sys

import tree_to_manifest as ttm

SCHEMA = "eap.pcg_scatter/1"
DEFAULT_SEED = 20261123
DEFAULT_COUNT = 1892
DEFAULT_SPAN_M = 200.0
DEFAULT_RATIO = 0.7
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO, "docs", "pcg_multi_species_fixture.json")
DEFAULT_SOURCE = os.path.join(REPO, "assets", "raw", "trees", "source_assets.json")
SPECIES_ORDER = ("conifer", "broadleaf")


def _entry_for(entries, species):
    for e in entries:
        if str(e.get("file", "")).lower().startswith(species):
            return e
    raise SystemExit("source_assets.json 里没有 %r 资产" % species)


def _mesh_meta(entry, species):
    glb_rel = os.path.join("assets", "raw", "trees", entry["file"]).replace("\\", "/")
    glb_abs = os.path.join(REPO, glb_rel)
    if not os.path.isfile(glb_abs):
        raise SystemExit("glb 不存在: " + glb_abs)
    stats = ttm.glb_stats(glb_abs)
    b = stats["bbox"]
    return {
        "species": species,
        "packagePath": glb_rel,          # 真实资产路径（不是 /Engine/BasicShapes/*）
        "objectPath": glb_rel,
        "source": "file",
        "boundsCm": {
            "min": [round(b["min"][0] * 100.0, 2), round(b["min"][1] * 100.0, 2),
                    round(b["min"][2] * 100.0, 2)],
            "max": [round(b["max"][0] * 100.0, 2), round(b["max"][1] * 100.0, 2),
                    round(b["max"][2] * 100.0, 2)],
        },
        "triCount": stats["triCount"],
        "heightM": round(b["size"][1], 3),
    }


def build(out_path, source_path=DEFAULT_SOURCE, seed=DEFAULT_SEED, count=DEFAULT_COUNT,
          span_m=DEFAULT_SPAN_M, conifer_ratio=DEFAULT_RATIO):
    entries = ttm.read_json(source_path)
    if isinstance(entries, dict):
        entries = [entries]
    metas = {sp: _mesh_meta(_entry_for(entries, sp), sp) for sp in SPECIES_ORDER}

    n_conifer = int(round(count * conifer_ratio))
    n_broadleaf = count - n_conifer
    if n_conifer <= 0 or n_broadleaf <= 0:
        raise SystemExit("两种树种都要有实例：conifer=%d broadleaf=%d" % (n_conifer, n_broadleaf))
    species_seq = ["conifer"] * n_conifer + ["broadleaf"] * n_broadleaf

    rng = random.Random(seed)
    rng.shuffle(species_seq)   # 固定 seed → 固定分布

    half_cm = span_m * 50.0
    points_by_species = {sp: [] for sp in SPECIES_ORDER}
    all_points = []
    for sp in species_seq:
        p = {
            "t": [round(rng.uniform(-half_cm, half_cm), 6),
                  round(rng.uniform(-half_cm, half_cm), 6),
                  round(rng.uniform(-30.0, 30.0), 6)],
            "r": [0.0, 0.0, round(rng.uniform(0.0, 360.0), 3)],
            "s": [round(rng.uniform(0.8, 1.2), 4)] * 3,
            "species": sp,
        }
        points_by_species[sp].append(p)
        all_points.append(p)

    meshes = []
    for sp in SPECIES_ORDER:
        meta = dict(metas[sp])
        meshes.append({
            "component": "ISM_%s_0" % sp,
            "actor": "PCG_EAP_MultiSpecies",
            "instanceCount": len(points_by_species[sp]),
            "mesh": {k: meta[k] for k in ("packagePath", "objectPath", "source", "boundsCm", "triCount")},
            "points": points_by_species[sp],
        })

    doc = {
        "schema": SCHEMA,
        "exportedAt": "2026-11-23T00:00:00+08:00",   # 固定值，保证可复现
        "synthetic": True,
        "generator": "tools/make_multi_species_fixture.py",
        "seed": seed,
        "translationUnit": "cm",
        "rotationUnit": "deg",
        "scatterOrigin": "tree_base",
        "pivotNote": "合成散点的 t 已是树根底部世界坐标，落位端不再扣 Cube 半高",
        "sourceAssets": "assets/raw/trees/source_assets.json",
        "species": {sp: len(points_by_species[sp]) for sp in SPECIES_ORDER},
        "graph": {"path": "/Game/EAP/PCG/MultiSpeciesFixture", "assetClass": "PCGGraph",
                  "nodesCount": 0, "nodes": []},
        "scatter": {
            "actor": "PCG_EAP_MultiSpecies",
            "pcgComponents": [{"name": "PCG Component", "seed": seed}],
            "instancedMeshes": meshes,
            "points": all_points,
            "instanceCount": len(all_points),
        },
        "output": {"instances": len(all_points), "meshes": len(meshes), "spanM": span_m},
        "warnings": [],
    }

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    return {"out": out_path, "assets": len(meshes), "instances": len(all_points), "seed": seed,
            "species": doc["species"], "meshes": {sp: metas[sp]["packagePath"] for sp in SPECIES_ORDER}}


def main(argv):
    ap = argparse.ArgumentParser(description="生成可复现的真实双树种散点 fixture")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT)
    ap.add_argument("--span-m", type=float, default=DEFAULT_SPAN_M)
    ap.add_argument("--conifer-ratio", type=float, default=DEFAULT_RATIO)
    args = ap.parse_args(argv[1:])
    s = build(args.out, source_path=args.source, seed=args.seed, count=args.count,
              span_m=args.span_m, conifer_ratio=args.conifer_ratio)
    print("assets=%d placements_total=%d seed=%d species=conifer:%d,broadleaf:%d -> %s"
          % (s["assets"], s["instances"], s["seed"], s["species"]["conifer"],
             s["species"]["broadleaf"], s["out"]))
    for sp in SPECIES_ORDER:
        print("     %s <- %s" % (sp, s["meshes"][sp]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
