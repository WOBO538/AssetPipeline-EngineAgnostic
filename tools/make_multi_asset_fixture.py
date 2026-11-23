"""EAP · 合成多资产散点 fixture（固定 seed，可复现）

用途：给「多资产 manifest」造假数据——一份散点文件里混多种网格，每种网格一个 asset。
不依赖 UE、不依赖网络；同一个 seed 重复运行，输出 JSON **逐字节一致**（方便 diff / 复算）。

输出 schema 对齐 `ue/Content/Python/pcg_export.py` 的 `eap.pcg_scatter/1`
（坐标 cm、旋转 deg），这样 `tools/pcg_to_manifest.py` 不用为 fixture 开特例。

用法：
    python tools/make_multi_asset_fixture.py --out docs/pcg_multi_fixture.json
    python tools/make_multi_asset_fixture.py --out docs/x.json --seed 20261123 --assets 20
"""
import argparse
import json
import os
import random
import sys

SCHEMA = "eap.pcg_scatter/1"
DEFAULT_SEED = 20261123
MESH_PREFIX = "/Game/EAP/PCG/Multi/Meshes"
COORD_MAX_CM = 20000.0
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO, "docs", "pcg_multi_fixture.json")
TRI_CHOICES = (12, 48, 96, 320, 1_240)


def _mesh_bounds(rng):
    """盒体包围盒（cm）：X/Y 居中、Z 从 0 起（与树资产"底面在原点"一致）。"""
    w = round(rng.uniform(200.0, 600.0), 1)
    d = round(rng.uniform(200.0, 600.0), 1)
    h = round(rng.uniform(150.0, 800.0), 1)
    return {"min": [round(-w / 2.0, 1), round(-d / 2.0, 1), 0.0],
            "max": [round(w / 2.0, 1), round(d / 2.0, 1), h]}


def build(out_path, seed=DEFAULT_SEED, asset_count=20, min_instances=6, max_instances=9):
    rng = random.Random(seed)   # 唯一随机源：固定 seed，调用顺序固定 → 结果可复现
    meshes, all_points = [], []
    for i in range(asset_count):
        name = "SM_Prop_A%02d" % (i + 1)
        pkg = MESH_PREFIX + "/" + name
        n = rng.randint(min_instances, max_instances)
        points = []
        for _ in range(n):
            points.append({
                "t": [round(rng.uniform(0.0, COORD_MAX_CM), 6),
                      round(rng.uniform(0.0, COORD_MAX_CM), 6),
                      round(rng.uniform(-50.0, 150.0), 6)],
                "r": [0.0, 0.0, round(rng.uniform(0.0, 360.0), 3)],
                "s": [round(rng.uniform(0.8, 1.2), 4)] * 3,
            })
        all_points.extend(points)
        meshes.append({
            "component": "ISM_%s_0" % name,
            "actor": "PCG_EAP_MultiFixture",
            "instanceCount": n,
            "mesh": {
                "packagePath": pkg,
                "objectPath": pkg + "." + name,
                "boundsCm": _mesh_bounds(rng),
                "triCount": rng.choice(TRI_CHOICES),
            },
            "points": points,
        })

    doc = {
        "schema": SCHEMA,
        # 合成数据：不写当前时间，避免每次运行产生 diff
        "exportedAt": "2026-11-23T00:00:00+08:00",
        "synthetic": True,
        "generator": "tools/make_multi_asset_fixture.py",
        "seed": seed,
        "translationUnit": "cm",
        "rotationUnit": "deg",
        "engine": {"version": "synthetic/fixture", "project": None, "map": None},
        "graph": {"path": "/Game/EAP/PCG/MultiFixture", "assetClass": "PCGGraph",
                  "nodesCount": 0, "nodes": []},
        "scatter": {
            "actor": "PCG_EAP_MultiFixture",
            "pcgComponents": [{
                "name": "PCG Component",
                "seed": seed,
                "generationTrigger": "<PCGComponentGenerationTrigger.GENERATE_ON_LOAD: 0>",
                "generatedGraphOutput": {"class": "<class 'PCGDataCollection'>",
                                         "readable": True, "taggedCount": 0},
            }],
            "instancedMeshes": meshes,
            "points": all_points,
            "instanceCount": len(all_points),
        },
        "output": {"instances": len(all_points), "meshes": len(meshes)},
        "warnings": [],
    }

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    return {"out": out_path, "assets": len(meshes), "instances": len(all_points), "seed": seed,
            "counts": {m["mesh"]["packagePath"]: m["instanceCount"] for m in meshes}}


def main(argv):
    ap = argparse.ArgumentParser(description="生成可复现的多资产散点 fixture")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--assets", type=int, default=20)
    ap.add_argument("--min-instances", type=int, default=6)
    ap.add_argument("--max-instances", type=int, default=9)
    args = ap.parse_args(argv[1:])
    s = build(args.out, seed=args.seed, asset_count=args.assets,
              min_instances=args.min_instances, max_instances=args.max_instances)
    print("assets=%d placements_total=%d seed=%d -> %s"
          % (s["assets"], s["instances"], s["seed"], s["out"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
