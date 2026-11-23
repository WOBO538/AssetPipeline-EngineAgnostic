"""EAP · Layer 1 数据出口链路（一条命令跑完整条链）

    python tools/run_pcg_chain.py
    python tools/run_pcg_chain.py --ue "C:\\Program Files\\Epic Games\\UE_5.7\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" \
                                  --project "D:\\UE_Projects\\VegetationPCG\\VegetationPCG.uproject"

链路（四次无头 UE + 两次纯 Python）：
  1) UE 无头跑 ue/Content/Python/pcg_export.py → docs/pcg_forest_pilot.json（cm）
  2) tools/pcg_to_manifest.py → schemas/pcg_manifest.json（manifest v1.1，m）
     `--tree` 时改跑 tools/tree_to_manifest.py → schemas/tree_manifest.json（真实树 glb，双端）
  3) tools/validate_manifest.py → 必须 errors=0
  4) UE 无头跑 consume_manifest.run(..., dry_run=True) → 必须 assets 数、placements 数一致
  5) UE 无头跑 place_manifest.place() → 在专用关卡建 ISM 落 1892 条（`--tree` 时先无头导入 glb）
  6) UE 无头跑 place_manifest.verify() → 新进程重载关卡读回实例
  7) 断言：散点数 == 实例数 == manifest placements == 消费端报告 placements == 落位实例数，
     且落位 bbox / 每条 transform 与 manifest 一致（< 1 cm、< 0.05°）；
     `--tree` 还会断言落位网格确实是树资产、UE 侧树高 == glTF 树高（< 5 cm）、
     材质槽已规范化（metallic=0 / usage 标记齐全 / alpha 槽 MASKED / 组件覆盖是 EAP 材质资产）

退出码：0 全绿；1 任一断言失败（失败信息打在最后）。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_SCRIPT = os.path.join(REPO, "ue", "Content", "Python", "pcg_export.py")
PLACE_SCRIPT = os.path.join(REPO, "ue", "Content", "Python", "place_manifest.py")
COMPILE_SCRIPT = os.path.join(REPO, "ue", "Content", "Python", "check_material_compile.py")
SCATTER_JSON = os.path.join(REPO, "docs", "pcg_forest_pilot.json")
MANIFEST_JSON = os.path.join(REPO, "schemas", "pcg_manifest.json")
PLACE_JSON = os.path.join(REPO, "docs", "placed_pilot.json")
VERIFY_JSON = os.path.join(REPO, "docs", "placed_verify.json")
TREE_MANIFEST_JSON = os.path.join(REPO, "schemas", "tree_manifest.json")
TREE_PLACE_JSON = os.path.join(REPO, "docs", "placed_tree.json")
TREE_VERIFY_JSON = os.path.join(REPO, "docs", "placed_tree_verify.json")
COMPILE_JSON = os.path.join(REPO, "docs", "material_compile_check.json")
MULTI_FIXTURE_JSON = os.path.join(REPO, "docs", "pcg_multi_fixture.json")
MULTI_MANIFEST_JSON = os.path.join(REPO, "schemas", "pcg_manifest_multi.json")
MULTI_PLACE_JSON = os.path.join(REPO, "docs", "placed_multi.json")
MULTI_VERIFY_JSON = os.path.join(REPO, "docs", "placed_multi_verify.json")
DEFAULT_LEVEL_PKG = "/Game/EAP/PCG/PlacedPilot"
DEFAULT_TREE_LEVEL_PKG = "/Game/EAP/PCG/PlacedTrees"
DEFAULT_MULTI_LEVEL_PKG = "/Game/EAP/PCG/PlacedMulti"
DEFAULT_UE = r"C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
DEFAULT_PROJECT = r"D:\UE_Projects\VegetationPCG\VegetationPCG.uproject"

sys.path.insert(0, os.path.join(REPO, "tools"))


def _ang_diff(a, b):
    """角度差，处理 360 环绕。"""
    return abs(((float(a) - float(b) + 180.0) % 360.0) - 180.0)


def _match_items(manifest_items, placed_items, tol_m=0.01, tol_scale=0.002, tol_deg=0.05):
    """把落位实例和 manifest items 一一对上（贪心最近匹配）。返回没对上的 manifest 下标。"""
    used = [False] * len(placed_items)
    bad = []
    for mi, m in enumerate(manifest_items):
        mt = m.get("t") or [0, 0, 0]
        ms = m.get("s") or [1, 1, 1]
        mr = m.get("r") or [0, 0, 0]
        hit = None
        for vi, v in enumerate(placed_items):
            if used[vi]:
                continue
            vt, vs, vr = v.get("t") or [], v.get("s") or [], v.get("r") or []
            if len(vt) != 3 or len(vs) != 3 or len(vr) != 3:
                continue
            if max(abs(float(mt[i]) - float(vt[i])) for i in range(3)) > tol_m:
                continue
            if max(abs(float(ms[i]) - float(vs[i])) for i in range(3)) > tol_scale:
                continue
            if max(_ang_diff(mr[i], vr[i]) for i in range(3)) > tol_deg:
                continue
            hit = vi
            break
        if hit is None:
            bad.append(mi)
        else:
            used[hit] = True
    return bad


def _bbox_diff(a, b):
    if not a or not b:
        return 1e9
    return max(max(abs(float(a["min"][i]) - float(b["min"][i])) for i in range(3)),
               max(abs(float(a["max"][i]) - float(b["max"][i])) for i in range(3)))


def _item_bbox(items):
    pts = [(it.get("t") or [0, 0, 0]) for it in items]
    if not pts:
        return None
    return {"min": [round(min(p[i] for p in pts), 4) for i in range(3)],
            "max": [round(max(p[i] for p in pts), 4) for i in range(3)]}


def _run(label, cmd, timeout=900, env=None):
    print("[%s] %s" % (label, " ".join(cmd[:2]) + " ..."))
    t0 = time.time()
    run_env = dict(os.environ)
    run_env.update(env or {})
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=run_env)
    lines = [ln.strip() for ln in (p.stdout or "").splitlines() if "[EAP]" in ln]
    for ln in lines:
        print("     " + ln.split("[EAP]")[-1].strip())
    errs = [ln.strip() for ln in (p.stdout or "").splitlines()
            if ("LogPython: Error" in ln or "Traceback" in ln or "Python script executed with errors" in ln)]
    for ln in errs[:20]:
        print("     " + ln.split("LogPython: ")[-1].strip())
    print("[%s] exit=%d %.1fs" % (label, p.returncode, time.time() - t0))
    if p.returncode != 0:
        tail = "\n".join(((p.stdout or "") + (p.stderr or "")).splitlines()[-25:])
        print(tail)
    return p, lines


def _ue_cmd(ue, project, script, nullrhi=True):
    cmd = [ue, project, "-ExecutePythonScript=%s" % script, "-unattended", "-nosplash", "-stdout"]
    if nullrhi:
        cmd.insert(-1, "-nullrhi")   # 材质编译复检必须带 RHI，其余步骤用 -nullrhi 免得依赖显卡
    return cmd


def _run_multi(args):
    """多资产链路：合成 fixture → 多 asset manifest → validate → UE 落位 → 新进程读回。"""
    import make_multi_asset_fixture
    import pcg_to_manifest
    import validate_manifest

    expect_assets = args.expect_assets or 20
    fails = []
    print("[0/6] 多资产链路（fixture → manifest → validate → UE 落位 → 读回）")

    fx = make_multi_asset_fixture.build(MULTI_FIXTURE_JSON, seed=args.multi_seed,
                                        asset_count=expect_assets)
    with open(MULTI_FIXTURE_JSON, "r", encoding="utf-8-sig") as fh:
        scatter = json.load(fh)
    sc = scatter.get("scatter") or {}
    points_n = len(sc.get("points") or [])
    instance_n = sc.get("instanceCount")
    ism_n = sum((m.get("instanceCount") or 0) for m in (sc.get("instancedMeshes") or []))
    print("[1/6] fixture: assets=%d points=%d instanceCount=%s ism_sum=%d seed=%d -> %s"
          % (fx["assets"], points_n, instance_n, ism_n, fx["seed"], MULTI_FIXTURE_JSON))

    manifest, summ = pcg_to_manifest.build(MULTI_FIXTURE_JSON, MULTI_MANIFEST_JSON)
    pl_n = summ["placements"]
    print("[2/6] manifest: assets=%d placements=%d pipelineId=%s -> %s"
          % (summ["assets"], pl_n, manifest.get("pipelineId"), MULTI_MANIFEST_JSON))

    res = validate_manifest.validate(MULTI_MANIFEST_JSON)
    for w in res["warnings"]:
        print("     WARN " + w)
    for e in res["errors"]:
        print("     ERROR " + e)
    print("[3/6] validate: assets=%d errors=%d warnings=%d"
          % (res["assets"], len(res["errors"]), len(res["warnings"])))
    if res["errors"]:
        fails.append("manifest 校验有 %d 个 error" % len(res["errors"]))

    # ---- 纯 Python 断言：fixture / manifest 自洽 ----
    checks = [
        ("fixture.assets == %d" % expect_assets, fx["assets"] == expect_assets, "assets=%d" % fx["assets"]),
        ("points == instanceCount", points_n == instance_n, "%d vs %s" % (points_n, instance_n)),
        ("points == ism_sum", points_n == ism_n, "%d vs %d" % (points_n, ism_n)),
        ("fixture.instances == manifest.placements", points_n == pl_n, "%d vs %d" % (points_n, pl_n)),
        ("manifest.assets == %d" % expect_assets, summ["assets"] == expect_assets, "assets=%d" % summ["assets"]),
        ("validate errors == 0", not res["errors"], "errors=%d" % len(res["errors"])),
    ]
    bad_assets = []
    for a in manifest.get("assets") or []:
        pl = a.get("placements") or {}
        claimed = None
        for c in a.get("checks") or []:
            if c.get("rule") == "instance_count==placements.count":
                val = c.get("value") or []
                if isinstance(val, list) and len(val) == 2:
                    claimed = int(val[0])
        if claimed is None or claimed != pl.get("count") or len(pl.get("items") or []) != pl.get("count"):
            bad_assets.append(a.get("id"))
    checks.append(("每个 asset instance_count == placements.count", not bad_assets,
                   "bad=%s" % (bad_assets or "none")))

    # ---- UE 落位 / 读回 ----
    ue_reason = None
    if args.skip_ue:
        ue_reason = "显式 --skip-ue"
    elif not os.path.isfile(args.ue):
        ue_reason = "找不到 " + args.ue
    elif not os.path.isfile(args.project):
        ue_reason = "找不到 " + args.project

    placed = verify = None
    if ue_reason:
        print("[4/6][5/6] SKIP UE: %s（只跑 fixture/manifest/validate）" % ue_reason)
    else:
        env = {
            "EAP_PLACE_MODE": "place",
            "EAP_MANIFEST": MULTI_MANIFEST_JSON,
            "EAP_LEVEL_PKG": DEFAULT_MULTI_LEVEL_PKG,
            "EAP_PLACE_SAVE": "1",
            "EAP_PLACE_OUT": MULTI_PLACE_JSON,
            "EAP_VERIFY_OUT": MULTI_VERIFY_JSON,
            "EAP_ALLOW_SYNTHETIC_MESH": "1",   # 合成 fixture 的网格在 UE 侧从 BasicShapes 派生
        }
        p, _ = _run("4/6 UE 多资产落位", _ue_cmd(args.ue, args.project, PLACE_SCRIPT), env=env)
        if p.returncode != 0:
            fails.append("UE 多资产落位退出码 %d" % p.returncode)
        if os.path.isfile(MULTI_PLACE_JSON):
            with open(MULTI_PLACE_JSON, "r", encoding="utf-8-sig") as fh:
                placed = json.load(fh)
            pa = placed.get("assets") or []
            print("[4/6] 落位: assets=%d placed=%s/%s levelSaved=%s"
                  % (len(pa), placed.get("placed"), placed.get("expected"), placed.get("levelSaved")))
            print("     bbox=%s 组件=%s" % (placed.get("bboxPlacedM"),
                                           [x.get("component") for x in pa[:4]]))
        else:
            fails.append("多资产落位证据未生成: " + MULTI_PLACE_JSON)

        env2 = dict(env)
        env2["EAP_PLACE_MODE"] = "verify"
        p, _ = _run("5/6 UE 多资产读回", _ue_cmd(args.ue, args.project, PLACE_SCRIPT), env=env2)
        if p.returncode != 0:
            fails.append("UE 多资产读回退出码 %d" % p.returncode)
        if os.path.isfile(MULTI_VERIFY_JSON):
            with open(MULTI_VERIFY_JSON, "r", encoding="utf-8-sig") as fh:
                verify = json.load(fh)
            va = verify.get("assets") or []
            print("[5/6] 读回: assets=%d count=%s ok=%s matched=%s/%s"
                  % (len(va), verify.get("count"), verify.get("ok"),
                     verify.get("assetsMatched"), len(va)))
        else:
            fails.append("多资产读回证据未生成: " + MULTI_VERIFY_JSON)

    if placed is not None:
        pa = placed.get("assets") or []
        checks.append(("UE 落位 asset 数 == manifest", len(pa) == summ["assets"],
                       "%d vs %d" % (len(pa), summ["assets"])))
        checks.append(("UE 落位总数 == manifest.placements", placed.get("placed") == pl_n,
                       "%s vs %d" % (placed.get("placed"), pl_n)))
    if verify is not None:
        va = verify.get("assets") or []
        checks.append(("verify asset 数 == manifest", len(va) == summ["assets"],
                       "%d vs %d" % (len(va), summ["assets"])))
        bad = [(v.get("index"), v.get("count"), v.get("expected")) for v in va
               if v.get("count") != v.get("expected") or v.get("matched") != v.get("expected")]
        checks.append(("每个 asset 读回数量 == manifest", not bad, "bad=%s" % (bad or "none")))
        mt = max([float(v.get("maxLocErrM") or 0.0) for v in va] or [0.0])
        mr = max([float(v.get("maxRotErrDeg") or 0.0) for v in va] or [0.0])
        ms = max([float(v.get("maxScaleErr") or 0.0) for v in va] or [0.0])
        checks.append(("读回 transform 误差 (<1e-3m/<0.1deg/<1e-3)", mt < 1e-3 and mr < 0.1 and ms < 1e-3,
                       "t=%.6f m r=%.4f deg s=%.6f" % (mt, mr, ms)))
        checks.append(("verify.ok == true", verify.get("ok") is True, "ok=%s" % verify.get("ok")))

    print("[6/6] 断言")
    for name, ok, detail in checks:
        print("     %-42s %s  (%s)" % (name, "PASS" if ok else "FAIL", detail))
        if not ok:
            fails.append("%s: %s" % (name, detail))

    if fails:
        print("\nCHAIN FAIL: " + " | ".join(fails))
        return 1
    if ue_reason:
        print("注意: UE 未参与本次多资产链路（%s）。" % ue_reason)
    print("\nCHAIN PASS assets=%d placements=%d" % (summ["assets"], pl_n))
    print("产物: %s" % MULTI_FIXTURE_JSON)
    print("      %s" % MULTI_MANIFEST_JSON)
    if verify is not None:
        print("      %s" % MULTI_PLACE_JSON)
        print("      %s" % MULTI_VERIFY_JSON)
    return 0


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ue", default=DEFAULT_UE)
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--skip-ue-export", action="store_true", help="复用已有 docs/pcg_forest_pilot.json")
    ap.add_argument("--skip-ue-consume", action="store_true", help="跳过第 4 步（只做 1-3 + 断言）")
    ap.add_argument("--skip-place", action="store_true", help="跳过第 5-6 步（placements 落位 + 验证）")
    ap.add_argument("--level-pkg", default=DEFAULT_LEVEL_PKG, help="落位目标关卡（默认 %s）" % DEFAULT_LEVEL_PKG)
    ap.add_argument("--expect-span-m", type=float, default=None,
                    help="期望的落位地块边长（米）；给了就断言 bbox 的 X/Y 跨度在 ±--span-tol 内")
    ap.add_argument("--span-tol", type=float, default=0.15, help="地块跨度容差（默认 0.15 = ±15%%）")
    ap.add_argument("--tree", action="store_true",
                    help="换真实树资产链路：manifest 用 tools/tree_to_manifest.py（glb 双击端），"
                         "落位到 /Game/EAP/PCG/PlacedTrees")
    ap.add_argument("--tree-species", default="conifer", help="source_assets.json 里选哪个树种（默认 conifer）")
    ap.add_argument("--tree-source", default=os.path.join(REPO, "assets", "raw", "trees", "source_assets.json"),
                    help="树资产清单（默认 assets/raw/trees/source_assets.json）")
    ap.add_argument("--skip-compile-check", action="store_true",
                    help="跳过带 RHI 的材质编译复检（--tree 时默认会跑，约多 30~60s）")
    ap.add_argument("--multi", action="store_true",
                    help="多资产链路：合成 fixture（20 网格）→ 多 asset manifest → 20 组件落位/读回")
    ap.add_argument("--expect-assets", type=int, default=None,
                    help="--multi 期望的 asset 数（默认 20）")
    ap.add_argument("--multi-seed", type=int, default=20261123, help="--multi fixture 的固定随机 seed")
    ap.add_argument("--skip-ue", action="store_true",
                    help="--multi 跳过 UE 落位/读回，只跑 fixture/manifest/validate + 纯 Python 断言")
    args = ap.parse_args(argv[1:])

    if args.multi:
        return _run_multi(args)

    fails = []
    if not os.path.isfile(args.ue):
        fails.append("找不到 UnrealEditor-Cmd: " + args.ue)
    if not os.path.isfile(args.project):
        fails.append("找不到 UE 工程: " + args.project)
    if fails:
        print("FAIL " + " | ".join(fails))
        return 1

    # 换树链路：换 manifest / 证据路径 / 默认关卡
    manifest_json = TREE_MANIFEST_JSON if args.tree else MANIFEST_JSON
    place_json = TREE_PLACE_JSON if args.tree else PLACE_JSON
    verify_json = TREE_VERIFY_JSON if args.tree else VERIFY_JSON
    if args.tree and args.level_pkg == DEFAULT_LEVEL_PKG:
        args.level_pkg = DEFAULT_TREE_LEVEL_PKG
    if args.tree:
        print("[0/7] 链路 = 换真实树资产（species=%s）" % args.tree_species)

    # ---- 1) UE 导出散点 ----
    if not args.skip_ue_export:
        p, _ = _run("1/5 UE 导出", _ue_cmd(args.ue, args.project, EXPORT_SCRIPT))
        if p.returncode != 0:
            fails.append("UE 导出退出码 %d" % p.returncode)
    if not os.path.isfile(SCATTER_JSON):
        fails.append("散点 JSON 不存在: " + SCATTER_JSON)
    if fails:
        print("FAIL " + " | ".join(fails))
        return 1

    with open(SCATTER_JSON, "r", encoding="utf-8-sig") as fh:
        scatter = json.load(fh)
    sc = scatter.get("scatter") or {}
    points_n = len(sc.get("points") or [])
    instance_n = sc.get("instanceCount")
    ism_n = sum((m.get("instanceCount") or 0) for m in (sc.get("instancedMeshes") or []))
    print("[1/7] 散点: points=%d instanceCount=%s ism_sum=%d" % (points_n, instance_n, ism_n))

    # ---- 2) 生成 manifest ----
    if args.tree:
        import tree_to_manifest
        manifest, summ = tree_to_manifest.build(args.tree_source, SCATTER_JSON, manifest_json,
                                                species=args.tree_species)
        pl_n = summ["placements"]
        print("[2/7] tree manifest: asset=%s glb=%s tris=%d height=%.3fm placements=%d pivotZOffset=%.3fm"
              % (summ["asset"], summ["glb"], summ["triCount"], summ["heightM"], pl_n, summ["pivotZOffsetM"]))
        for w in summ["warnings"]:
            print("     WARN " + w)
    else:
        import pcg_to_manifest
        manifest, summ = pcg_to_manifest.build(SCATTER_JSON, manifest_json)
        pl_n = summ["placements"]
        print("[2/7] manifest: assets=%d placements=%d -> %s" % (summ["assets"], pl_n, manifest_json))

    # ---- 3) 校验 ----
    import validate_manifest
    res = validate_manifest.validate(manifest_json)
    for w in res["warnings"]:
        print("     WARN " + w)
    for e in res["errors"]:
        print("     ERROR " + e)
    print("[3/7] validate: assets=%d errors=%d warnings=%d" % (res["assets"], len(res["errors"]), len(res["warnings"])))
    if res["errors"]:
        fails.append("manifest 校验有 %d 个 error" % len(res["errors"]))

    # ---- 4) UE dry-run 消费（读 placements + 统计） ----
    report_path = os.path.splitext(manifest_json)[0] + ".ue_report.json"
    ue_placements = None
    if not args.skip_ue_consume:
        tmpdir = tempfile.mkdtemp(prefix="eap_chain_")
        tmp_script = os.path.join(tmpdir, "ue_consume_dryrun.py")
        with open(tmp_script, "w", encoding="utf-8") as fh:
            fh.write("import sys\n")
            fh.write("sys.path.append(r'%s')\n" % os.path.join(REPO, "ue", "Content", "Python"))
            fh.write("import consume_manifest\n")
            fh.write("consume_manifest.run(r'%s', dry_run=True)\n" % manifest_json)
        p, _ = _run("4/7 UE dry-run", _ue_cmd(args.ue, args.project, tmp_script))
        if p.returncode != 0:
            fails.append("UE dry-run 退出码 %d" % p.returncode)
        if os.path.isfile(report_path):
            with open(report_path, "r", encoding="utf-8-sig") as fh:
                rep = json.load(fh)
            ue_placements = sum((rep.get("placements") or {}).values())
            print("[4/7] 报告: ok=%d failed=%d placements=%s warnings=%d"
                  % (len(rep.get("ok") or []), len(rep.get("failed") or []), ue_placements,
                     len(rep.get("warnings") or [])))
            if rep.get("failed"):
                fails.append("UE 消费端有失败资产: %s" % [f.get("id") for f in rep["failed"]])
        else:
            fails.append("UE 报告未生成: " + report_path)
    else:
        print("[4/7] 跳过 UE dry-run")

    # ---- 5) placements 落位（UE 无头：建 Actor + ISM，落 233 条，存专用关卡） ----
    placed = None
    verify = None
    compile_bad = None
    if not args.skip_place:
        place_env = {
            "EAP_PLACE_MODE": "place",
            "EAP_MANIFEST": manifest_json,
            "EAP_LEVEL_PKG": args.level_pkg,
            "EAP_PLACE_SAVE": "1",
            "EAP_PLACE_OUT": place_json,
            "EAP_VERIFY_OUT": verify_json,
        }
        p, _ = _run("5/7 UE 落位", _ue_cmd(args.ue, args.project, PLACE_SCRIPT), env=place_env)
        if p.returncode != 0:
            fails.append("UE 落位退出码 %d" % p.returncode)
        if os.path.isfile(place_json):
            with open(place_json, "r", encoding="utf-8-sig") as fh:
                placed = json.load(fh)
            print("[5/7] 落位: placed=%s expected=%s levelSaved=%s mesh=%s"
                  % (placed.get("placed"), placed.get("expected"), placed.get("levelSaved"),
                     (placed.get("meshInfo") or {}).get("path") or placed.get("mesh")))
            print("     bbox=%s meshHeight=%s m" % (placed.get("bboxPlacedM"),
                                                   (placed.get("meshInfo") or {}).get("heightM")))
        else:
            fails.append("落位证据未生成: " + place_json)

        # ---- 6) 新进程重载关卡读回（证明实例真的存进关卡，不是内存里的假象） ----
        verify_env = dict(place_env)
        verify_env["EAP_PLACE_MODE"] = "verify"
        p, _ = _run("6/7 UE 落位验证", _ue_cmd(args.ue, args.project, PLACE_SCRIPT), env=verify_env)
        if p.returncode != 0:
            fails.append("UE 落位验证退出码 %d" % p.returncode)
        if os.path.isfile(verify_json):
            with open(verify_json, "r", encoding="utf-8-sig") as fh:
                verify = json.load(fh)
            print("[6/7] 验证读回: count=%s mesh=%s bbox=%s"
                  % (verify.get("count"), verify.get("mesh"), verify.get("bboxPlacedM")))
        else:
            fails.append("验证证据未生成: " + verify_json)

        # ---- 6b) 材质编译复检（必须带 RHI：-nullrhi 下材质不真正编译，坏了也看不出来 → 树会全白）----
        if args.tree and not args.skip_compile_check:
            compile_env = dict(place_env)
            compile_env["EAP_COMPILE_OUT"] = COMPILE_JSON
            p, _ = _run("6b/7 UE 材质编译复检", _ue_cmd(args.ue, args.project, COMPILE_SCRIPT, nullrhi=False),
                        env=compile_env, timeout=900)
            out = (p.stdout or "") + "\n" + (p.stderr or "")
            compile_bad = [ln.strip() for ln in out.splitlines() if "Failed to compile Material" in ln]
            print("[6b/7] 材质编译: failed=%d（期望 0）" % len(compile_bad))
            for ln in compile_bad[:4]:
                print("     " + ln[:200])
            if os.path.isfile(COMPILE_JSON):
                with open(COMPILE_JSON, "r", encoding="utf-8-sig") as fh:
                    cs = json.load(fh)
                print("[6b/7] 复检材质 %d 个: %s" % (len(cs.get("materials") or {}),
                                                   sorted((cs.get("materials") or {}).keys())))
            else:
                fails.append("材质编译复检证据未生成: " + COMPILE_JSON)
    else:
        print("[5/7][6/7] 跳过 placements 落位与验证")

    # ---- 7) 断言 ----
    pl_items = (((manifest.get("assets") or [{}])[0].get("placements") or {}).get("items")) or []
    checks = [
        ("points == instanceCount", points_n == instance_n, "%d vs %s" % (points_n, instance_n)),
        ("points == ism_sum", points_n == ism_n, "%d vs %d" % (points_n, ism_n)),
        ("points == manifest.placements", points_n == pl_n, "%d vs %d" % (points_n, pl_n)),
    ]
    if ue_placements is not None:
        checks.append(("manifest.placements == ue_report.placements", pl_n == ue_placements,
                       "%d vs %s" % (pl_n, ue_placements)))
    if placed is not None:
        checks.append(("manifest.placements == placed", pl_n == placed.get("placed"),
                       "%d vs %s" % (pl_n, placed.get("placed"))))
    if verify is not None:
        checks.append(("verify.count == placements", pl_n == verify.get("count"),
                       "%d vs %s" % (pl_n, verify.get("count"))))
        d_bbox = _bbox_diff(verify.get("bboxPlacedM"), _item_bbox(pl_items))
        checks.append(("placed bbox == manifest bbox (<1cm)", d_bbox <= 0.01, "diff=%.4f m" % d_bbox))
        bad = _match_items(pl_items, verify.get("items") or [])
        checks.append(("placed items == manifest items (<1cm/0.05deg)", not bad,
                       "mismatch=%d/%d" % (len(bad), pl_n)))
        if args.expect_span_m and verify.get("bboxPlacedM"):
            bb = verify["bboxPlacedM"]
            spans = [float(bb["max"][i]) - float(bb["min"][i]) for i in range(2)]
            ok_span = all(abs(sp - args.expect_span_m) <= args.expect_span_m * args.span_tol for sp in spans)
            checks.append(("land span == %.0f m (±%.0f%%)" % (args.expect_span_m, args.span_tol * 100),
                           ok_span, "X=%.1f m / Y=%.1f m" % (spans[0], spans[1])))
        if args.tree:
            asset0 = (manifest.get("assets") or [{}])[0]
            model0 = asset0.get("model") or {}
            bbox0 = model0.get("boundingBoxM") or {}
            up = str(model0.get("upAxis") or "Y").upper()
            axis = {"X": 0, "Y": 1, "Z": 2}.get(up, 1)
            expect_h = None
            if bbox0.get("min") and bbox0.get("max"):
                expect_h = round(float(bbox0["max"][axis]) - float(bbox0["min"][axis]), 3)
            mesh_path = str(verify.get("mesh") or "")
            checks.append(("落位网格是树资产（非引擎 Cube）",
                           "/Trees/" in mesh_path and "BasicShapes" not in mesh_path, mesh_path))
            got_h = ((placed or {}).get("meshInfo") or {}).get("heightM")
            if expect_h and got_h is not None:
                checks.append(("UE 侧树高 == glTF 树高 (<5cm)",
                               abs(float(got_h) - expect_h) <= 0.05,
                               "UE=%.3f m / glTF=%.3f m" % (float(got_h), expect_h)))
            # 材质规范化（v1.3）：EAP 母材质派生 MIC，metallic 清零、usage 标记齐全、alpha 槽转 MASKED
            mat_ev = (placed or {}).get("materials") or []
            if mat_ev:
                all_usage = all(all((e.get("usage") or {}).values()) for e in mat_ev)
                all_metal = all(abs(float(e.get("metallic") or 0.0)) < 1e-6 for e in mat_ev)
                checks.append(("材质槽已规范化（metallic=0 且 usage 标记齐全）", all_usage and all_metal,
                               "%d 槽 %s" % (len(mat_ev), [e.get("kind") for e in mat_ev])))
                masked_ok = all("MASKED" in str(e.get("blendModeAfter", ""))
                                for e in mat_ev if e.get("kind") == "masked")
                checks.append(("alpha 材质已从 BLEND 转 MASKED", masked_ok,
                               str([e.get("blendModeAfter") for e in mat_ev])))
            vmat = verify.get("materials") or []
            if vmat:
                checks.append(("落位组件材质覆盖 = EAP 材质资产",
                               all("/MI_EAP_" in (m or "") for m in vmat), str(vmat)))
            if mat_ev:
                tex_ok = all(e.get("texturesApplied") for e in mat_ev)
                checks.append(("材质贴图参数已生效（BaseColor 挂上目标贴图）", tex_ok,
                               str([str(e.get("appliedBaseColor")).rsplit(".", 1)[0] for e in mat_ev])))
            if compile_bad is not None:
                checks.append(("材质编译通过（无 Failed to compile，否则会退回白材质）",
                               not compile_bad, "failed=%d" % len(compile_bad)))
    print("[7/7] 断言")
    for name, ok, detail in checks:
        print("     %-42s %s  (%s)" % (name, "PASS" if ok else "FAIL", detail))
        if not ok:
            fails.append("%s: %s" % (name, detail))

    if fails:
        print("\nCHAIN FAIL: " + " | ".join(fails))
        return 1
    print("\nCHAIN PASS points=%d assets=%d placements=%d" % (points_n, summ.get("assets", 1), pl_n))
    print("产物: %s" % SCATTER_JSON)
    print("      %s" % manifest_json)
    if not args.skip_ue_consume:
        print("      %s" % report_path)
    if not args.skip_place:
        print("      %s" % place_json)
        print("      %s" % verify_json)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
