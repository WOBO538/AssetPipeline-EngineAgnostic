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
SCATTER_JSON = os.path.join(REPO, "docs", "pcg_forest_pilot.json")
MANIFEST_JSON = os.path.join(REPO, "schemas", "pcg_manifest.json")
PLACE_JSON = os.path.join(REPO, "docs", "placed_pilot.json")
VERIFY_JSON = os.path.join(REPO, "docs", "placed_verify.json")
TREE_MANIFEST_JSON = os.path.join(REPO, "schemas", "tree_manifest.json")
TREE_PLACE_JSON = os.path.join(REPO, "docs", "placed_tree.json")
TREE_VERIFY_JSON = os.path.join(REPO, "docs", "placed_tree_verify.json")
DEFAULT_LEVEL_PKG = "/Game/EAP/PCG/PlacedPilot"
DEFAULT_TREE_LEVEL_PKG = "/Game/EAP/PCG/PlacedTrees"
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


def _ue_cmd(ue, project, script):
    return [ue, project, "-ExecutePythonScript=%s" % script, "-unattended", "-nosplash", "-nullrhi", "-stdout"]


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
    args = ap.parse_args(argv[1:])

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
