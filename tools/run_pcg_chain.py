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
import shutil
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
REAL_MULTI_FIXTURE_JSON = os.path.join(REPO, "docs", "pcg_multi_species_fixture.json")
REAL_MULTI_MANIFEST_JSON = os.path.join(REPO, "schemas", "tree_manifest_multi.json")
REAL_MULTI_PLACE_JSON = os.path.join(REPO, "docs", "placed_tree_multi.json")
REAL_MULTI_VERIFY_JSON = os.path.join(REPO, "docs", "placed_tree_multi_verify.json")
REAL_MULTI_COMPILE_JSON = os.path.join(REPO, "docs", "material_compile_check_pair.json")
DEFAULT_LEVEL_PKG = "/Game/EAP/PCG/PlacedPilot"
DEFAULT_TREE_LEVEL_PKG = "/Game/EAP/PCG/PlacedTrees"
DEFAULT_MULTI_LEVEL_PKG = "/Game/EAP/PCG/PlacedMulti"
DEFAULT_REAL_MULTI_LEVEL_PKG = "/Game/EAP/PCG/PlacedTreeMulti"
DEFAULT_UE = r"C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
DEFAULT_PROJECT = r"D:\UE_Projects\VegetationPCG\VegetationPCG.uproject"
DEFAULT_UNITY = r"D:\Program Files\unity AZ\2022.3.62f3c1\Editor\Unity.exe"
DEFAULT_UNITY_PROJECT = r"D:\unity\EAP_Unity_Consumer"
UNITY_CS = os.path.join(REPO, "unity", "Editor", "ManifestImporter.cs")
UNITY_PLACE_SCENE = "Assets/Scenes/EAP_PlacedTrees.unity"

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


def ue_to_unity_point(t):
    """UE(Z-up，左手) → Unity(Y-up，左手) 的唯一权威换算。

        unityPos   = (ue_x, ue_z, ue_y)      // 米，Unity 1 unit = 1 m
        unityYaw   = -ue_yaw（度）           // 绕 Y 轴取反，避免镜像
        unityScale = s（三值）

    C# 消费端 `ManifestImporter.UeToUnityPos` / `UeToUnityYaw` 必须与此逐字一致；
    任何一处改动都要同步另一处，并用 --unity-placements-check 的 bbox 断言复验。
    """
    return [float(t[0]), float(t[2]), float(t[1])]


def ue_to_unity_bounds(box):
    """把 UE 系 {min,max} 包围盒换算成 Unity 系（逐轴重排后取小的当 min）。"""
    a = ue_to_unity_point(box["min"])
    b = ue_to_unity_point(box["max"])
    return {"min": [min(a[i], b[i]) for i in range(3)],
            "max": [max(a[i], b[i]) for i in range(3)]}


def _placements_unity_bbox(manifest):
    """manifest placements[].items[].t 全部换算成 Unity 坐标后的包围盒。"""
    pts = []
    for a in manifest.get("assets") or []:
        for it in ((a.get("placements") or {}).get("items") or []):
            pts.append(ue_to_unity_point(it.get("t") or [0, 0, 0]))
    if not pts:
        return None
    return {"min": [min(p[i] for p in pts) for i in range(3)],
            "max": [max(p[i] for p in pts) for i in range(3)]}


def _bbox_max_diff(a, b):
    if not a or not b or not a.get("min") or not b.get("min"):
        return 1e9
    return max(max(abs(float(a["min"][i]) - float(b["min"][i])) for i in range(3)),
               max(abs(float(a["max"][i]) - float(b["max"][i])) for i in range(3)))


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


def _clear_evidence(paths):
    """跑 UE 前删掉上一轮证据，避免 UE 崩溃/脚本报错时旧 JSON 被当成本轮结果（假 PASS）。"""
    for p in paths:
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError as exc:
                print("     WARN 删除旧证据失败 %s: %r" % (p, exc))


def _ue_python_errors(stdout):
    """UE 即使 Python 抛异常也返回 exit=0；从 stdout 捞错误行，供调用方判假 PASS。"""
    bad = []
    for ln in (stdout or "").splitlines():
        s = ln.strip()
        if ("LogPython: Error" in s or "Traceback (most recent call last)" in s
                or "Python script executed with errors" in s):
            bad.append(s)
    return bad


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


def _run_real_multi(args):
    """真实双树种混排：conifer + broadleaf 同一份散点 → 2 条 asset → UE 2 个 ISM 落位/读回。"""
    import make_multi_species_fixture
    import tree_to_manifest
    import validate_manifest

    expect_assets = args.expect_assets or 2
    expect_points = args.expect_placements or 1892
    fails = []
    print("[0/7] 真实双树种混排（conifer + broadleaf，真实 glb，不用占位网格）")

    fx = make_multi_species_fixture.build(REAL_MULTI_FIXTURE_JSON, seed=args.real_multi_seed,
                                          count=expect_points)
    with open(REAL_MULTI_FIXTURE_JSON, "r", encoding="utf-8-sig") as fh:
        scatter = json.load(fh)
    sp_counts = scatter.get("species") or {}
    fx_points = len((scatter.get("scatter") or {}).get("points") or [])
    print("[1/7] fixture: points=%d species=%s seed=%d -> %s"
          % (fx_points, sp_counts, fx["seed"], REAL_MULTI_FIXTURE_JSON))
    for sp in ("conifer", "broadleaf"):
        print("     %s <- %s" % (sp, fx["meshes"].get(sp)))

    manifest, summ = tree_to_manifest.build_multi(
        os.path.join(REPO, "assets", "raw", "trees", "source_assets.json"),
        REAL_MULTI_FIXTURE_JSON, REAL_MULTI_MANIFEST_JSON,
        species_pair=("conifer", "broadleaf"))
    pl_n = summ["placements"]
    print("[2/7] manifest: assets=%d placements=%d pivotZOffset=%.3fm -> %s"
          % (summ["assets"], pl_n, summ["pivotZOffsetM"], REAL_MULTI_MANIFEST_JSON))
    for sp in summ["species"]:
        print("     %s: %s glb=%s tris=%d height=%.3fm placements=%d"
              % (sp["species"], sp["asset"], sp["glb"], sp["triCount"], sp["heightM"], sp["placements"]))

    res = validate_manifest.validate(REAL_MULTI_MANIFEST_JSON)
    for w in res["warnings"]:
        print("     WARN " + w)
    for e in res["errors"]:
        print("     ERROR " + e)
    print("[3/7] validate: assets=%d errors=%d warnings=%d"
          % (res["assets"], len(res["errors"]), len(res["warnings"])))

    assets_m = manifest.get("assets") or []
    checks = [
        ("fixture.points == %d" % expect_points, fx_points == expect_points, "points=%d" % fx_points),
        ("fixture 两种树种都有实例", all(int(sp_counts.get(sp) or 0) > 0 for sp in ("conifer", "broadleaf"))
         and sum(int(v or 0) for v in sp_counts.values()) == fx_points,
         "conifer=%s broadleaf=%s" % (sp_counts.get("conifer"), sp_counts.get("broadleaf"))),
        ("manifest.assets == %d" % expect_assets, len(assets_m) == expect_assets, "assets=%d" % len(assets_m)),
        ("manifest.placements == %d" % expect_points, pl_n == expect_points, "placements=%d" % pl_n),
        ("validate errors == 0", not res["errors"], "errors=%d" % len(res["errors"])),
    ]
    bad_group = []
    want_by_asset = {sp["asset"]: int(sp["placements"]) for sp in summ["species"]}
    for a in assets_m:
        want = want_by_asset.get(a.get("id"), -1)
        got = int((a.get("placements") or {}).get("count") or -1)
        if want != got:
            bad_group.append((a.get("id"), got, want))
    checks.append(("每个 asset placements == fixture 该树种点数", not bad_group,
                   "bad=%s" % (bad_group or "none")))
    bad_src = [a.get("id") for a in assets_m
               if str((a.get("model") or {}).get("source")) != "file"
               or "BasicShapes" in str((a.get("model") or {}).get("path"))
               or "BasicShapes" in str(((a.get("engineTargets") or {}).get("UE5") or {}).get("meshPath"))]
    checks.append(("两个 asset 都是真实 glb（无 BasicShapes）", not bad_src,
                   "bad=%s" % (bad_src or "none")))

    # ---- UE 落位 / 读回（真实资产，不设 EAP_ALLOW_SYNTHETIC_MESH）----
    ue_reason = None
    if args.skip_ue:
        ue_reason = "显式 --skip-ue"
    elif not os.path.isfile(args.ue):
        ue_reason = "找不到 " + args.ue
    elif not os.path.isfile(args.project):
        ue_reason = "找不到 " + args.project
    placed = verify = compile_info = compile_run = None
    if ue_reason:
        print("[4/7][5/7][6/7] SKIP UE: %s（只跑 fixture/manifest/validate）" % ue_reason)
    else:
        _clear_evidence([REAL_MULTI_PLACE_JSON, REAL_MULTI_VERIFY_JSON, REAL_MULTI_COMPILE_JSON])
        env = {
            "EAP_PLACE_MODE": "place",
            "EAP_MANIFEST": REAL_MULTI_MANIFEST_JSON,
            "EAP_LEVEL_PKG": DEFAULT_REAL_MULTI_LEVEL_PKG,
            "EAP_PLACE_SAVE": "1",
            "EAP_PLACE_OUT": REAL_MULTI_PLACE_JSON,
            "EAP_VERIFY_OUT": REAL_MULTI_VERIFY_JSON,
        }
        print("     EAP_ALLOW_SYNTHETIC_MESH=%s（真实资产链路不依赖占位网格）"
              % ("已设置" if "EAP_ALLOW_SYNTHETIC_MESH" in env else "unset"))
        p, _ = _run("4/7 UE 双树种落位", _ue_cmd(args.ue, args.project, PLACE_SCRIPT), env=env)
        if p.returncode != 0:
            fails.append("UE 双树种落位退出码 %d" % p.returncode)
        perr = _ue_python_errors(p.stdout)
        if perr:
            fails.append("UE 双树种落位 Python 报错: " + perr[0][:240])
        if os.path.isfile(REAL_MULTI_PLACE_JSON):
            with open(REAL_MULTI_PLACE_JSON, "r", encoding="utf-8-sig") as fh:
                placed = json.load(fh)
            pa = placed.get("assets") or []
            print("[4/7] 落位: assets=%d placed=%s/%s levelSaved=%s"
                  % (len(pa), placed.get("placed"), placed.get("expected"), placed.get("levelSaved")))
            for x in pa:
                print("     ISM=%s mesh=%s height=%.3fm"
                      % (x.get("component"), x.get("mesh"), float((x.get("meshInfo") or {}).get("heightM") or 0.0)))
        else:
            fails.append("双树种落位证据未生成: " + REAL_MULTI_PLACE_JSON)

        env2 = dict(env)
        env2["EAP_PLACE_MODE"] = "verify"
        p, _ = _run("5/7 UE 双树种读回", _ue_cmd(args.ue, args.project, PLACE_SCRIPT), env=env2)
        if p.returncode != 0:
            fails.append("UE 双树种读回退出码 %d" % p.returncode)
        perr = _ue_python_errors(p.stdout)
        if perr:
            fails.append("UE 双树种读回 Python 报错: " + perr[0][:240])
        if os.path.isfile(REAL_MULTI_VERIFY_JSON):
            with open(REAL_MULTI_VERIFY_JSON, "r", encoding="utf-8-sig") as fh:
                verify = json.load(fh)
            va = verify.get("assets") or []
            print("[5/7] 读回: assets=%d count=%s ok=%s matched=%s/%s"
                  % (len(va), verify.get("count"), verify.get("ok"),
                     verify.get("assetsMatched"), len(va)))
        else:
            fails.append("双树种读回证据未生成: " + REAL_MULTI_VERIFY_JSON)

        if args.with_compile:
            cenv = dict(env)
            cenv["EAP_COMPILE_OUT"] = REAL_MULTI_COMPILE_JSON
            p, _ = _run("6/7 UE 双树种材质编译复检",
                        _ue_cmd(args.ue, args.project, COMPILE_SCRIPT, nullrhi=False),
                        env=cenv, timeout=900)
            out = (p.stdout or "") + "\n" + (p.stderr or "")
            bad_lines = [ln.strip() for ln in out.splitlines() if "Failed to compile Material" in ln]
            for ln in bad_lines[:4]:
                print("     " + ln[:200])
            compile_run = {"exit": p.returncode, "failedLines": len(bad_lines)}
            if p.returncode != 0:
                fails.append("RHI 材质编译进程退出码 %d" % p.returncode)
            if os.path.isfile(REAL_MULTI_COMPILE_JSON):
                with open(REAL_MULTI_COMPILE_JSON, "r", encoding="utf-8-sig") as fh:
                    compile_info = json.load(fh)
                mats = compile_info.get("materials") or {}
                print("[6/7] 材质编译: %d 个，日志 Failed to compile=%d，exit=%d: %s"
                      % (len(mats), len(bad_lines), p.returncode, sorted(mats.keys())))
            else:
                fails.append("材质编译复检证据未生成: " + REAL_MULTI_COMPILE_JSON)
        else:
            # 本机 RHI 进程在这台机器上会 OOM（页面文件不足），默认跳过；需要时加 --with-compile
            print("[6/7] 材质编译: SKIP（默认不跑 RHI 复检，加 --with-compile 开启）")

    tex_expect = {"basecolor": ("TC_Default", True), "normal": ("TC_Normalmap", False),
                  "data": ("TC_Masked", False)}
    if placed is not None:
        pa = placed.get("assets") or []
        checks.append(("UE 落位 2 个 ISM / 总数一致", len(pa) == len(assets_m) and placed.get("placed") == pl_n,
                       "assets=%d placed=%s vs %d" % (len(pa), placed.get("placed"), pl_n)))
        expect_meshes = [str(((a.get("engineTargets") or {}).get("UE5") or {}).get("meshPath")) for a in assets_m]
        got_meshes = [str(x.get("mesh")) for x in pa]
        checks.append(("落位网格 = manifest 的 conifer/broadleaf 两路",
                       sorted(got_meshes) == sorted(expect_meshes) and len(set(got_meshes)) == len(expect_meshes),
                       "%s" % got_meshes))
        h_bad = []
        for a, x in zip(assets_m, pa):
            size = ((a.get("model") or {}).get("boundingBoxM") or {}).get("max") or [0, 0, 0]
            mn = ((a.get("model") or {}).get("boundingBoxM") or {}).get("min") or [0, 0, 0]
            want_h = float(size[1]) - float(mn[1])
            got_h = float((x.get("meshInfo") or {}).get("heightM") or 0.0)
            if abs(want_h - got_h) > 0.05:
                h_bad.append((a.get("id"), round(got_h, 3), round(want_h, 3)))
        checks.append(("两个落位网格高度 == glTF (<5cm)", not h_bad, "bad=%s" % (h_bad or "none")))
        mat_bad = []
        for x in pa:
            for e in (x.get("materials") or []):
                if float(e.get("metallic") if e.get("metallic") is not None else -1) != 0.0:
                    mat_bad.append((x.get("id"), e.get("slot"), "metallic"))
                if not all((e.get("usage") or {}).values()):
                    mat_bad.append((x.get("id"), e.get("slot"), "usage"))
                after = str(e.get("blendModeAfter") or "").upper()
                want = "MASKED" if str(e.get("kind") or "").lower() == "masked" else "OPAQUE"
                if want not in after:
                    mat_bad.append((x.get("id"), e.get("slot"), after))
        checks.append(("两树种材质都规范化（metallic=0/usage/alpha）", not mat_bad,
                       "bad=%s" % (mat_bad or "none")))
        # ① 贴图压缩三档（数据来自 place 进程读回的 textureSettings，不是断言侧写死）
        tex_bad, tex_main, tex_total = [], 0, 0
        for x in pa:
            for e in (x.get("materials") or []):
                for t in (e.get("textureSettings") or []):
                    tex_total += 1
                    kind = str(t.get("kind") or "")
                    want_c, want_s = tex_expect.get(kind, ("?", None))
                    if kind in ("basecolor", "normal"):
                        tex_main += 1
                    if str(t.get("compression") or "") != want_c or t.get("srgb") is not want_s:
                        tex_bad.append((x.get("id"), e.get("slot"), kind,
                                        t.get("compression"), t.get("srgb")))
        checks.append(("贴图压缩三档符合表（≥4 张主贴图）", not tex_bad and tex_main >= 4,
                       "main=%d total=%d bad=%s" % (tex_main, tex_total, tex_bad or "none")))
        checks.append(("落位未使用 EAP_ALLOW_SYNTHETIC_MESH", "EAP_ALLOW_SYNTHETIC_MESH" not in env,
                       "unset"))
    if verify is not None:
        va = verify.get("assets") or []
        checks.append(("verify asset 数 == manifest", len(va) == len(assets_m),
                       "%d vs %d" % (len(va), len(assets_m))))
        bad = [(v.get("index"), v.get("count"), v.get("expected")) for v in va
               if v.get("count") != v.get("expected") or v.get("matched") != v.get("expected")]
        checks.append(("每个树种读回数量 == manifest", not bad, "bad=%s" % (bad or "none")))
        mt = max([float(v.get("maxLocErrM") or 0.0) for v in va] or [0.0])
        mr = max([float(v.get("maxRotErrDeg") or 0.0) for v in va] or [0.0])
        ms = max([float(v.get("maxScaleErr") or 0.0) for v in va] or [0.0])
        checks.append(("读回 transform 误差 (<1e-3m/<0.1deg/<1e-3)",
                       mt < 1e-3 and mr < 0.1 and ms < 1e-3,
                       "t=%.6f m r=%.4f deg s=%.6f" % (mt, mr, ms)))
        mat_bad = []
        for v in va:
            mats = v.get("materials") or []
            if not mats or any("/MI_EAP_" not in str(m) for m in mats):
                mat_bad.append((v.get("id"), mats))
        checks.append(("读回材质 override 都是 EAP MIC", not mat_bad, "bad=%s" % (mat_bad or "none")))
        # ② MIC 上的 roughness 贴图 + 标量（新进程读回 get_material_instance_* 的值）
        want_slots = sum(len(((a.get("material") or {}).get("materials") or [])) for a in assets_m)
        got_slots, rough_bad = 0, []
        for v in va:
            for p in (v.get("materialParams") or []):
                got_slots += 1
                rt = str(p.get("roughnessTex") or "")
                leaf = rt.rsplit(".", 1)[0].rsplit("/", 1)[-1]
                rs = p.get("roughnessScale")
                if not rt or not leaf.endswith("_Roughness") or rs is None or abs(float(rs) - 0.9) > 1e-6:
                    rough_bad.append((v.get("id"), p.get("material"), rt, rs))
        checks.append(("MIC roughness 贴图非空且 RoughnessScale==0.9",
                       got_slots == want_slots and not rough_bad,
                       "slots=%d/%d bad=%s" % (got_slots, want_slots, rough_bad or "none")))
        # ③ 新进程读回贴图压缩（不是 place 进程的旧证据）
        persist_bad, persist_n = [], 0
        for v in va:
            for p in (v.get("materialParams") or []):
                for t in (p.get("textureSettings") or []):
                    persist_n += 1
                    kind = str(t.get("kind") or "")
                    want_c, want_s = tex_expect.get(kind, ("?", None))
                    if str(t.get("compression") or "") != want_c or t.get("srgb") is not want_s:
                        persist_bad.append((v.get("id"), kind, t.get("compression"), t.get("srgb")))
        checks.append(("新进程读回贴图压缩仍符合三档",
                       persist_n >= 8 and not persist_bad,
                       "n=%d bad=%s" % (persist_n, persist_bad or "none")))
        checks.append(("verify.ok == true", verify.get("ok") is True, "ok=%s" % verify.get("ok")))
    if compile_run is not None:
        # 判据只看进程退出码 + 日志；证据里的 shaderCompiled 是弱信号，不单独作数
        mats = (compile_info or {}).get("materials") or {}
        mic_n = sum(1 for k in mats if "/MI_EAP_" in k)
        want_n = sum(len(((a.get("material") or {}).get("materials") or [])) for a in assets_m)
        checks.append(("材质编译 exit==0 / 无 Failed to compile / 材质数一致",
                       compile_run["exit"] == 0 and compile_run["failedLines"] == 0
                       and mic_n > 0 and mic_n == want_n,
                       "exit=%d failed=%d mic=%d/%d"
                       % (compile_run["exit"], compile_run["failedLines"], mic_n, want_n)))

    print("[7/7] 断言")
    for name, ok, detail in checks:
        print("     %-46s %s  (%s)" % (name, "PASS" if ok else "FAIL", detail))
        if not ok:
            fails.append("%s: %s" % (name, detail))

    if fails:
        print("\nCHAIN FAIL: " + " | ".join(fails))
        return 1
    if ue_reason:
        print("注意: UE 未参与本次双树种链路（%s）。" % ue_reason)
    print("\nCHAIN PASS assets=%d placements=%d" % (len(assets_m), pl_n))
    print("产物: %s" % REAL_MULTI_FIXTURE_JSON)
    print("      %s" % REAL_MULTI_MANIFEST_JSON)
    if verify is not None:
        print("      %s" % REAL_MULTI_PLACE_JSON)
        print("      %s" % REAL_MULTI_VERIFY_JSON)
    return 0


def _unity_running():
    try:
        p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Unity.exe", "/NH"],
                           capture_output=True, text=True, timeout=30)
        return "Unity.exe" in (p.stdout or "")
    except Exception:  # noqa: BLE001
        return False


def _tail(text, n=40):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:])


def _deploy_unity_script(project):
    """把仓库里的消费端脚本同步进 Unity 工程（改了脚本要重编，必须覆盖）。"""
    dst = os.path.join(project, "Assets", "Editor", "ManifestImporter.cs")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(UNITY_CS, "rb") as fh:
        src = fh.read()
    cur = None
    if os.path.isfile(dst):
        with open(dst, "rb") as fh:
            cur = fh.read()
    if cur != src:
        with open(dst, "wb") as fh:
            fh.write(src)
        return True
    return False


def _run_unity(unity, project, method, manifest, log_path, timeout=1200, extra=None):
    cmd = [unity, "-batchmode", "-quit", "-projectPath", project,
           "-executeMethod", method, "-eapManifest", manifest, "-logFile", log_path]
    if extra:
        cmd.extend(extra)
    print("[unity] %s ..." % method.split(".")[-1])
    if os.path.isfile(log_path):
        os.remove(log_path)
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    log = ""
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            log = fh.read()
    else:
        log = (p.stdout or "") + (p.stderr or "")
    print("[unity] exit=%d %.1fs log=%s" % (p.returncode, time.time() - t0, log_path))
    return p.returncode, log


def _report_summary(report_path):
    if not os.path.isfile(report_path):
        return None
    with open(report_path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if "summary:" in ln:
                toks = ln.split("summary:")[-1].strip().split()
                out = {}
                for t in toks:
                    if "=" in t:
                        k, v = t.split("=", 1)
                        out[k] = int(v)
                return out
    return None


def _run_unity_check(args):
    """Unity 端吃真模型：batchmode 导入 → 新进程读回 → 4 条断言（独立计数）。"""
    manifest = os.path.abspath(args.manifest)
    project = os.path.abspath(args.unity_project)
    fails = []
    print("[0/4] Unity 端吃真模型（batchmode 导入 + 读回）")

    if _unity_running():
        print("CHAIN FAIL: Unity.exe 正在运行，先关掉 Unity 再来")
        return 1
    if not os.path.isfile(args.unity):
        fails.append("找不到 Unity.exe: " + args.unity)
    if not os.path.isfile(manifest):
        fails.append("找不到 manifest: " + manifest)
    if not os.path.isdir(project):
        fails.append("找不到 Unity 工程: " + project)
    if fails:
        print("CHAIN FAIL: " + " | ".join(fails))
        return 1

    changed = _deploy_unity_script(project)
    print("[0/4] 消费端脚本 %s → %s" % ("已更新" if changed else "无变化", project))

    report_path = os.path.splitext(manifest)[0] + ".unity_report.md"
    verify_path = os.path.splitext(manifest)[0] + ".unity_verify.json"
    import_log = os.path.splitext(manifest)[0] + ".unity_log.txt"
    verify_log = os.path.splitext(manifest)[0] + ".unity_verify.unity_log.txt"
    for p in (report_path, verify_path):
        if os.path.isfile(p):
            os.remove(p)

    with open(manifest, "r", encoding="utf-8-sig") as fh:
        man = json.load(fh)
    assets = man.get("assets") or []

    import_rc, import_log_text = None, ""
    if args.unity_verify_only:
        print("[1/4] SKIP 导入（--unity-verify-only，反向验证用）")
    else:
        import_rc, import_log_text = _run_unity(
            args.unity, project, "EAP.ManifestImporter.ImportFromArgs", manifest, import_log)
        summ = _report_summary(report_path)
        ok1 = import_rc == 0 and summ is not None and summ.get("ok") == len(assets) and summ.get("failed") == 0
        print("[1/4] Unity batchmode 导入 exit=%s report=%s → %s"
              % (import_rc, summ, "PASS" if ok1 else "FAIL"))
        if not ok1:
            fails.append("断言1 导入失败 exit=%s report=%s | log_tail: %s"
                         % (import_rc, summ, _tail(import_log_text)))

    verify_rc, verify_log_text = _run_unity(
        args.unity, project, "EAP.ManifestImporter.VerifyFromArgs", manifest, verify_log)
    vdata = None
    if os.path.isfile(verify_path):
        with open(verify_path, "r", encoding="utf-8-sig") as fh:
            vdata = json.load(fh)
    vmap = {a.get("id"): a for a in ((vdata or {}).get("assets") or [])}
    if verify_rc != 0 or vdata is None:
        fails.append("读回失败 exit=%s verify=%s | log_tail: %s"
                     % (verify_rc, verify_path if vdata is not None else "<not written>", _tail(verify_log_text)))

    # 断言 2：两个 Prefab 真实存在
    ok2 = True
    for a in assets:
        v = vmap.get(a.get("id")) or {}
        prefab = v.get("prefab") or ""
        exists = bool(v.get("prefabExists")) and prefab.startswith("Assets/") \
            and os.path.isfile(os.path.join(project, prefab))
        if not exists:
            ok2 = False
            fails.append("断言2 %s prefab 不存在: %r" % (a.get("id"), prefab))
    print("[2/4] Prefab 存在（%d 个） → %s" % (len(assets), "PASS" if ok2 else "FAIL"))

    # 断言 3：BaseColor/Normal 路径是 Assets/ 开头且 texturesApplied 全 true
    ok3 = True
    for a in assets:
        v = vmap.get(a.get("id")) or {}
        texs = v.get("textures") or {}
        applied = v.get("texturesApplied") or {}
        for chan in ("BaseColor", "Normal"):
            p = str(texs.get(chan) or "")
            if not p.startswith("Assets/") or applied.get(chan) is not True:
                ok3 = False
                fails.append("断言3 %s %s path=%r applied=%r" % (a.get("id"), chan, p, applied.get(chan)))
    print("[3/4] 贴图读回（Assets/ 路径 + texturesApplied） → %s" % ("PASS" if ok3 else "FAIL"))

    # 断言 4：bounds 每轴 < 0.05 m，triCount 完全一致
    ok4 = True
    for a in assets:
        v = vmap.get(a.get("id")) or {}
        want_tri = ((a.get("model") or {}).get("triCount"))
        if int(v.get("meshTriCount") or 0) != int(want_tri or -1):
            ok4 = False
            fails.append("断言4 %s triCount=%r want=%r" % (a.get("id"), v.get("meshTriCount"), want_tri))
        box = (a.get("model") or {}).get("boundingBoxM") or {}
        got = v.get("boundsM") or {}
        if not got.get("min") or not got.get("max"):
            ok4 = False
            fails.append("断言4 %s bounds 缺失" % a.get("id"))
            continue
        diff = 0.0
        for axis in range(3):
            diff = max(diff, abs(float(got["min"][axis]) - float(box["min"][axis])),
                       abs(float(got["max"][axis]) - float(box["max"][axis])))
        if diff >= 0.05:
            ok4 = False
            fails.append("断言4 %s bounds 误差 %.4fm got=%r want=%r"
                         % (a.get("id"), diff, got, box))
    print("[4/4] bounds/triCount → %s" % ("PASS" if ok4 else "FAIL"))

    if fails:
        print("CHAIN FAIL: " + " | ".join(fails))
        return 1
    print("UNITY-CHECK PASS assets=%d prefabs=%d textures=%d/%d"
          % (len(assets), len(assets), len(assets) * 2, len(assets) * 2))
    return 0


def _run_unity_placements_check(args):
    """Unity 端 placements 落位：换算验证 → batchmode 落位 → 新进程读回 → 4 条断言。

    反向验证必须加 --unity-placements-verify-only：落位会重建场景，跳过落位才看得到清态后的 FAIL。
    """
    manifest = os.path.abspath(args.manifest)
    project = os.path.abspath(args.unity_project)
    fails = []
    print("[0/5] Unity 端 placements 落位（换算验证 + 落位 + 读回）")

    if _unity_running():
        print("CHAIN FAIL: Unity.exe 正在运行，先关掉 Unity 再来")
        return 1
    if not os.path.isfile(args.unity):
        fails.append("找不到 Unity.exe: " + args.unity)
    if not os.path.isfile(manifest):
        fails.append("找不到 manifest: " + manifest)
    if not os.path.isdir(project):
        fails.append("找不到 Unity 工程: " + project)
    if fails:
        print("CHAIN FAIL: " + " | ".join(fails))
        return 1

    changed = _deploy_unity_script(project)
    print("[0/5] 消费端脚本 %s → %s" % ("已更新" if changed else "无变化", project))

    scene_abs = os.path.join(project, UNITY_PLACE_SCENE)
    verify_path = os.path.splitext(manifest)[0] + ".unity_placements_verify.json"
    place_log = os.path.splitext(manifest)[0] + ".unity_placements_log.txt"
    verify_log = os.path.splitext(manifest)[0] + ".unity_placements_verify.unity_log.txt"
    if os.path.isfile(verify_path):
        os.remove(verify_path)

    with open(manifest, "r", encoding="utf-8-sig") as fh:
        man = json.load(fh)
    assets = man.get("assets") or []
    expected_counts = {a.get("id"): int(((a.get("placements") or {}).get("count")) or 0) for a in assets}
    expected_total = sum(expected_counts.values())

    # ---- [1/5] 坐标换算验证（纯 Python，权威换算 vs UE 侧落位证据）----
    expected_box = _placements_unity_bbox(man)
    ref_path = os.path.join(REPO, "docs", "placed_tree_multi_verify.json")
    ref_diff = None
    if os.path.isfile(ref_path):
        with open(ref_path, "r", encoding="utf-8-sig") as fh:
            ref = json.load(fh)
        ref_ue = ref.get("bboxPlacedM")
        if ref_ue:
            ref_unity = ue_to_unity_bounds(ref_ue)
            ref_diff = _bbox_max_diff(expected_box, ref_unity)
    if expected_box is None or (ref_diff is not None and ref_diff >= 0.05):
        fails.append("换算验证 manifest 1892 点包围盒 vs bboxPlacedM 误差 %s m（要求 < 0.05）"
                     % ("n/a" if ref_diff is None else "%.4f" % ref_diff))
    print("[1/5] 坐标换算 1892 点 → Unity bbox min=%s max=%s；vs bboxPlacedM 逐轴误差=%s → %s"
          % (["%.4f" % v for v in (expected_box or {}).get("min", [])],
             ["%.4f" % v for v in (expected_box or {}).get("max", [])],
             "n/a" if ref_diff is None else "%.4f m" % ref_diff,
             "PASS" if (expected_box is not None and (ref_diff is None or ref_diff < 0.05)) else "FAIL"))

    # ---- [2/5] 落位（verify-only 时跳过，避免自愈重建场景）----
    if args.unity_placements_verify_only:
        print("[2/5] SKIP 落位（--unity-placements-verify-only，反向验证用）；scene=%s"
              % ("存在" if os.path.isfile(scene_abs) else "缺失"))
    else:
        place_rc, place_log_text = _run_unity(
            args.unity, project, "EAP.ManifestImporter.PlaceFromArgs", manifest, place_log)
        ok_place = place_rc == 0 and os.path.isfile(scene_abs)
        print("[2/5] Unity batchmode 落位 exit=%s scene=%s → %s"
              % (place_rc, "存在" if os.path.isfile(scene_abs) else "缺失",
                 "PASS" if ok_place else "FAIL"))
        if not ok_place:
            fails.append("落位失败 exit=%s scene=%s | log_tail: %s"
                         % (place_rc, "missing" if not os.path.isfile(scene_abs) else "ok",
                            _tail(place_log_text)))

    # ---- [3/5] 新进程读回 ----
    verify_rc, verify_log_text = _run_unity(
        args.unity, project, "EAP.ManifestImporter.VerifyPlacementsFromArgs", manifest, verify_log)
    vdata = None
    if os.path.isfile(verify_path):
        with open(verify_path, "r", encoding="utf-8-sig") as fh:
            vdata = json.load(fh)
    print("[3/5] 读回 exit=%s → %s" % (verify_rc, "PASS" if (verify_rc == 0 and vdata) else "FAIL"))
    if verify_rc != 0 or vdata is None:
        fails.append("读回失败 exit=%s verify=%s | log_tail: %s"
                     % (verify_rc, verify_path if vdata is not None else "<not written>",
                        _tail(verify_log_text)))

    vdata = vdata or {}
    got_counts = vdata.get("instances") or {}
    got_total = int(vdata.get("total") or 0)

    # ---- 断言 1：实例总数 / 每棵树实例数 ----
    bad1 = ["%s=%s(期望 %d)" % (k, got_counts.get(k, 0), v)
            for k, v in expected_counts.items() if int(got_counts.get(k, 0)) != v]
    ok1 = got_total == expected_total and not bad1
    print("[1/4] 实例数 total=%d(期望 %d) %s → %s"
          % (got_total, expected_total,
             " ".join("%s=%s" % (k, got_counts.get(k, 0)) for k in expected_counts),
             "PASS" if ok1 else "FAIL"))
    if not ok1:
        fails.append("断言1 实例数 %d != %d %s" % (got_total, expected_total, "; ".join(bad1)))

    # ---- 断言 2：世界包围盒 vs manifest t（换算后），每轴 < 0.05 m ----
    got_box = vdata.get("bboxM") or {}
    box_diff = _bbox_max_diff(got_box, expected_box)
    ok2 = bool(got_box.get("min")) and bool(got_box.get("max")) and box_diff < 0.05
    print("[2/4] 落入 bbox 误差=%.5f m（gate < 0.05） → %s" % (box_diff, "PASS" if ok2 else "FAIL"))
    if not ok2:
        fails.append("断言2 bbox 误差 %.4fm got=%r want=%r"
                     % (box_diff, got_box, expected_box))

    # ---- 断言 3：每个实例都有 Collider ----
    got_colliders = int(vdata.get("colliders") or 0)
    ok3 = got_colliders == got_total == expected_total
    print("[3/4] Collider 数=%d / 实例=%d / 期望=%d → %s"
          % (got_colliders, got_total, expected_total, "PASS" if ok3 else "FAIL"))
    if not ok3:
        fails.append("断言3 colliders=%d instances=%d want=%d"
                     % (got_colliders, got_total, expected_total))

    # ---- 断言 4：抽样实例材质仍有 BaseColor 贴图（Assets/ 开头且文件存在）----
    samples = vdata.get("sampleMaterials") or []
    ok4 = True
    for a in assets:
        aid = a.get("id")
        sample = next((s for s in samples
                       if str(s.get("instance") or "").startswith(str(aid) + "[")), None)
        base = str((sample or {}).get("baseColor") or "")
        hit = base.startswith("Assets/") and os.path.isfile(os.path.join(project, base))
        if not base.startswith("Assets/") or not hit:
            ok4 = False
            fails.append("断言4 %s BaseColor=%r（必须 Assets/ 开头且存在）" % (aid, base))
    if not samples:
        ok4 = False
        fails.append("断言4 没有任何 sampleMaterials")
    print("[4/4] 材质抽样 %d 条 → %s" % (len(samples), "PASS" if ok4 else "FAIL"))

    if fails:
        print("CHAIN FAIL: " + " | ".join(fails))
        return 1
    print("UNITY-PLACEMENTS-CHECK PASS instances=%d colliders=%d bbox_diff=%.5fm"
          % (got_total, got_colliders, box_diff))
    return 0


DUAL_PARITY_JSON = os.path.join(REPO, "docs", "dual_engine_parity.json")
DUAL_PARITY_MD = os.path.join(REPO, "docs", "dual_engine_parity.md")
PARITY_TOL_POS_M = 0.01
PARITY_TOL_YAW_DEG = 0.05
PARITY_TOL_SCALE = 1e-3


def _fmt3(v):
    return "%.3f" % float(v)


def _fmt_vec(v, n=3):
    return "(" + ", ".join(("%." + str(n) + "f") % float(x) for x in (v or [])) + ")"


def _run_parity_check(args):
    """双端逐实例对账：UE 读回 vs Unity 读回（1892 条全查）+ 产物对照表。

    前置：UE 证据里 `collision{enabled/meshHasCollision/instanceCount}` 必须成立。
    断言 1 数量/分项、2 位移 <0.01m、3 yaw <0.05°、4 缩放 <1e-3。
    `--parity-inject-yaw-deg` 只在对账时给 Unity 读回 yaw 加偏移（反向验证用，默认 0）。
    """
    manifest = os.path.abspath(args.manifest)
    ue_path = REAL_MULTI_VERIFY_JSON
    unity_path = os.path.splitext(manifest)[0] + ".unity_placements_verify.json"
    inject = float(args.parity_inject_yaw_deg or 0.0)
    fails = []
    print("[0/5] 双端逐实例对账（UE 读回 vs Unity 读回）")

    missing = [p for p in (manifest, ue_path, unity_path) if not os.path.isfile(p)]
    if missing:
        print("CHAIN FAIL: 缺少证据 " + " | ".join(missing)
              + "。先跑 --real-multi（生成 UE 读回）和 --unity-placements-check（生成 Unity 读回）")
        return 1
    stale = [p for p in (ue_path, unity_path) if os.path.getmtime(p) < os.path.getmtime(manifest)]
    if stale:
        print("CHAIN FAIL: 证据早于 manifest（不新鲜）：" + " | ".join(stale)
              + "。重跑 --real-multi / --unity-placements-check")
        return 1

    with open(manifest, "r", encoding="utf-8-sig") as fh:
        man = json.load(fh)
    with open(ue_path, "r", encoding="utf-8-sig") as fh:
        ue = json.load(fh)
    with open(unity_path, "r", encoding="utf-8-sig") as fh:
        un = json.load(fh)

    assets = man.get("assets") or []
    want_counts = {a.get("id"): len(((a.get("placements") or {}).get("items")) or []) for a in assets}
    expected_total = sum(want_counts.values())

    ue_assets = {a.get("id"): a for a in (ue.get("assets") or [])}
    ue_counts = {aid: len((ue_assets.get(aid) or {}).get("items") or []) for aid in want_counts}
    un_counts = {aid: int((un.get("instances") or {}).get(aid, 0)) for aid in want_counts}
    ue_total = sum(ue_counts.values())
    un_total = int(un.get("total") or 0)

    detail = {}
    dup_detail = 0
    for d in (un.get("instancesDetail") or []):
        key = (d.get("assetId"), int(d.get("index", -1)))
        if key in detail:
            dup_detail += 1
        detail[key] = d

    # ---- 前置：UE 侧 ISM 碰撞（不是相信 place 的返回值，是 verify 进程读回）----
    col = ue.get("collision") or {}
    prof = str(col.get("profile") or "")
    front_ok = (col.get("enabled") is True and col.get("meshHasCollision") is True
                and int(col.get("instanceCount") or -1) == expected_total
                and not col.get("collisionGeometryMissing")
                and prof in ("BlockAll", "BlockAllDynamic"))
    print("[1/5] 前置 UE 碰撞 enabled=%s profile=%s instances=%s(期望 %d) meshHasCollision=%s → %s"
          % (col.get("enabled"), prof or "<缺失>", col.get("instanceCount"), expected_total,
             col.get("meshHasCollision"), "PASS" if front_ok else "FAIL"))
    if not front_ok:
        fails.append("前置 UE 碰撞未达标（重跑 --real-multi 让证据带 collision）：enabled=%s profile=%r "
                     "instanceCount=%s meshHasCollision=%s missing=%s"
                     % (col.get("enabled"), prof, col.get("instanceCount"),
                        col.get("meshHasCollision"), col.get("collisionGeometryMissing")))

    # ---- 断言 1：数量与分项一致（manifest / UE / Unity 三方 + 明细条数）----
    count_bad = [(aid, want_counts[aid], ue_counts.get(aid), un_counts.get(aid))
                 for aid in want_counts
                 if not (ue_counts.get(aid) == un_counts.get(aid) == want_counts[aid])]
    ok1 = (not count_bad and ue_total == un_total == expected_total
           and len(detail) == expected_total and dup_detail == 0)
    print("[2/5] 断言1 数量 manifest=%d UE=%d Unity=%d detail=%d dup=%d %s → %s"
          % (expected_total, ue_total, un_total, len(detail), dup_detail,
             " ".join("%s=%d/%d/%d" % (b[0], b[1], b[2] or 0, b[3] or 0) for b in count_bad)
             if count_bad else "", "PASS" if ok1 else "FAIL"))
    if not ok1:
        fails.append("断言1 数量不一致 manifest=%d UE=%d Unity=%d detail=%d dup=%d bad=%s"
                     % (expected_total, ue_total, un_total, len(detail), dup_detail, count_bad or "none"))

    # ---- 逐实例 1892 条：UE 读回 → Unity 期望，和 Unity 读回逐条比 ----
    rows = []
    missing_detail = []
    max_pos = max_yaw = max_scale = 0.0
    pos_bad, yaw_bad, scale_bad = [], [], []
    matched_n = 0
    for a in assets:
        aid = a.get("id")
        mitems = ((a.get("placements") or {}).get("items")) or []
        uitems = (ue_assets.get(aid) or {}).get("items") or []
        for i, m in enumerate(mitems):
            u = uitems[i] if i < len(uitems) else None
            d = detail.get((aid, i))
            row = {
                "assetId": aid,
                "index": i,
                "manifest": {"t": (m.get("t") or [0, 0, 0]), "r": (m.get("r") or [0, 0, 0]),
                             "s": (m.get("s") or [1, 1, 1])},
                "ue": u,
                "unity": None,
                "delta": None,
            }
            if u is None or d is None or not d.get("t") or len(d.get("t")) < 3 \
                    or not d.get("s") or len(d.get("s")) < 3:
                missing_detail.append([aid, i, u is None, d is None])
                rows.append(row)
                continue
            ep = ue_to_unity_point(u.get("t") or [0, 0, 0])
            dpos = max(abs(ep[k] - float(d["t"][k])) for k in range(3))
            eyaw = -float((u.get("r") or [0, 0, 0])[2])
            d_yaw = _ang_diff(float(d.get("yawDeg") or 0.0) + inject, eyaw)
            dscale = max(abs(float((u.get("s") or [1, 1, 1])[k]) - float(d["s"][k])) for k in range(3))
            max_pos = max(max_pos, dpos)
            max_yaw = max(max_yaw, d_yaw)
            max_scale = max(max_scale, dscale)
            if dpos >= PARITY_TOL_POS_M:
                pos_bad.append([aid, i, round(dpos, 6)])
            if d_yaw >= PARITY_TOL_YAW_DEG:
                yaw_bad.append([aid, i, round(d_yaw, 6)])
            if dscale >= PARITY_TOL_SCALE:
                scale_bad.append([aid, i, round(dscale, 9)])
            if d.get("matched"):
                matched_n += 1
            row["unity"] = {"t": d["t"], "yawDeg": d.get("yawDeg"), "s": d["s"],
                            "matched": bool(d.get("matched"))}
            row["delta"] = {"posM": round(dpos, 9), "yawDeg": round(d_yaw, 6),
                            "scale": round(dscale, 9)}
            rows.append(row)

    ok2 = not missing_detail and not pos_bad and max_pos < PARITY_TOL_POS_M
    ok3 = not missing_detail and not yaw_bad and max_yaw < PARITY_TOL_YAW_DEG
    ok4 = not missing_detail and not scale_bad and max_scale < PARITY_TOL_SCALE
    print("[3/5] 断言2 位移 max=%.9f m（gate < %.2f）bad=%d → %s"
          % (max_pos, PARITY_TOL_POS_M, len(pos_bad) + len(missing_detail), "PASS" if ok2 else "FAIL"))
    print("[4/5] 断言3 yaw  max=%.6f°（gate < %.2f，注入 %.3f°）bad=%d → %s"
          % (max_yaw, PARITY_TOL_YAW_DEG, inject, len(yaw_bad) + len(missing_detail),
             "PASS" if ok3 else "FAIL"))
    print("[5/5] 断言4 缩放 max=%.9f（gate < %.0e）bad=%d → %s"
          % (max_scale, PARITY_TOL_SCALE, len(scale_bad) + len(missing_detail), "PASS" if ok4 else "FAIL"))
    if not ok2:
        fails.append("断言2 位移 max=%.9f m bad=%s missing=%d"
                     % (max_pos, pos_bad[:5] or "none", len(missing_detail)))
    if not ok3:
        fails.append("断言3 yaw max=%.6f° bad=%s missing=%d"
                     % (max_yaw, yaw_bad[:5] or "none", len(missing_detail)))
    if not ok4:
        fails.append("断言4 缩放 max=%.9f bad=%s missing=%d"
                     % (max_scale, scale_bad[:5] or "none", len(missing_detail)))

    assertions = [
        {"id": 1, "name": "数量与分项一致", "ok": ok1,
         "detail": "manifest=%d UE=%d Unity=%d detail=%d dup=%d bad=%s"
                   % (expected_total, ue_total, un_total, len(detail), dup_detail, count_bad or "none")},
        {"id": 2, "name": "逐实例位移", "ok": ok2,
         "detail": "max=%.9f m gate<%.2f bad=%d" % (max_pos, PARITY_TOL_POS_M, len(pos_bad))},
        {"id": 3, "name": "逐实例 yaw", "ok": ok3,
         "detail": "max=%.6f deg gate<%.2f bad=%d inject=%.3f"
                   % (max_yaw, PARITY_TOL_YAW_DEG, len(yaw_bad), inject)},
        {"id": 4, "name": "逐实例缩放", "ok": ok4,
         "detail": "max=%.9f gate<%.0e bad=%d" % (max_scale, PARITY_TOL_SCALE, len(scale_bad))},
    ]
    summary = {
        "expected": expected_total,
        "ueCount": ue_total,
        "unityCount": un_total,
        "checked": len(rows),
        "detailMatched": matched_n,
        "missingDetail": len(missing_detail),
        "maxPosErrM": round(max_pos, 9),
        "maxYawErrDeg": round(max_yaw, 6),
        "maxScaleErr": round(max_scale, 9),
        "tolerance": {"posM": PARITY_TOL_POS_M, "yawDeg": PARITY_TOL_YAW_DEG, "scale": PARITY_TOL_SCALE},
        "bad": {"pos": len(pos_bad), "yaw": len(yaw_bad), "scale": len(scale_bad)},
    }
    doc = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.dual_engine_parity/1",
        "manifest": os.path.basename(manifest),
        "sources": {"ue": ue_path, "unity": unity_path},
        "injectYawDeg": inject,
        "front": {"ok": front_ok, "collision": col},
        "assertions": assertions,
        "summary": summary,
        "instances": rows,
    }
    with open(DUAL_PARITY_JSON, "w", encoding="utf-8") as fh:
        head = {k: v for k, v in doc.items() if k != "instances"}
        text = json.dumps(head, ensure_ascii=False, indent=1)
        text = (text[:-2].rstrip() + ',\n "instances": [\n'
                + ",\n".join("  " + json.dumps(r, ensure_ascii=False, separators=(",", ":"))
                             for r in rows) + "\n ]\n}\n")
        fh.write(text)
    print("[out] 全量对账 → " + DUAL_PARITY_JSON)

    lines = [
        "# 双端逐实例对账（v1.9）",
        "",
        "- 生成：%s" % doc["when"],
        "- manifest：%s" % doc["manifest"],
        "- 来源：UE `%s` / Unity `%s`"
        % (os.path.relpath(ue_path, REPO), os.path.relpath(unity_path, REPO)),
        "- 注入 yaw 偏移：%.3f°（0 = 正常对账）" % inject,
        "",
        "## 前置：UE 侧 ISM 碰撞",
        "",
        "- enabled=%s / profile=%s / instanceCount=%s（期望 %d）/ meshHasCollision=%s → **%s**"
        % (col.get("enabled"), prof or "<缺失>", col.get("instanceCount"), expected_total,
           col.get("meshHasCollision"), "PASS" if front_ok else "FAIL"),
        "",
        "## 断言汇总",
        "",
        "| # | 断言 | 结果 | 依据 |",
        "|:--:|---|---|---|",
    ]
    for it in assertions:
        lines.append("| %d | %s | %s | %s |" % (it["id"], it["name"], "PASS" if it["ok"] else "FAIL", it["detail"]))
    lines += [
        "",
        "- 对账条数：%d / %d（UE %d，Unity %d）；Unity 明细自校验 matched=%d，缺失=%d"
        % (len(rows), expected_total, ue_total, un_total, matched_n, len(missing_detail)),
        "- 最大误差：位移 %.9f m / yaw %.6f° / 缩放 %.9f（门槛 0.01 m / 0.05° / 1e-3）"
        % (max_pos, max_yaw, max_scale),
        "",
        "## 前 20 条三列对照",
        "",
        "| # | 资产 | Manifest t / yaw / s | UE 读回 t / yaw / s | Unity 读回 t / yaw / s | 最大误差 |",
        "|--:|---|---|---|---|---|",
    ]
    for n, row in enumerate(rows[:20]):
        m, u, un_r = row["manifest"], row["ue"] or {}, row["unity"] or {}
        m_yaw = (m.get("r") or [0, 0, 0])[2]
        u_yaw = (u.get("r") or [0, 0, 0])[2] if u else "?"
        delta = row.get("delta") or {}
        lines.append("| %d | %s[%d] | %s / %s / %s | %s / %s / %s | %s / %s / %s | Δpos %s m, Δyaw %s°, Δs %s |"
                     % (n, row["assetId"], row["index"],
                        _fmt_vec(m.get("t")), _fmt3(m_yaw), _fmt_vec(m.get("s"), 4),
                        _fmt_vec(u.get("t")) if u else "—", _fmt3(u_yaw) if u else "—",
                        _fmt_vec(u.get("s"), 4) if u else "—",
                        _fmt_vec(un_r.get("t")) if un_r else "—",
                        _fmt3(un_r.get("yawDeg")) if un_r else "—",
                        _fmt_vec(un_r.get("s"), 4) if un_r else "—",
                        "%.6f" % delta.get("posM", 0) if delta else "—",
                        "%.6f" % delta.get("yawDeg", 0) if delta else "—",
                        "%.8f" % delta.get("scale", 0) if delta else "—"))
    lines += ["", "> 只列前 20 条；全量 1892 条见 `docs/dual_engine_parity.json`。"]
    with open(DUAL_PARITY_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("[out] 前 20 条 + 汇总 → " + DUAL_PARITY_MD)

    if fails:
        print("\nCHAIN FAIL: " + " | ".join(fails))
        return 1
    print("\nPARITY-CHECK PASS instances=%d maxPos=%.9fm maxYaw=%.6fdeg maxScale=%.9f collision=%s/%s/%d"
          % (expected_total, max_pos, max_yaw, max_scale, col.get("enabled"), col.get("meshHasCollision"),
             int(col.get("instanceCount") or 0)))
    return 0


PERF_PROBE = os.path.join(REPO, "ue", "Content", "Python", "perf_probe.py")
PERF_GLB_SRC = os.path.join(REPO, "assets", "raw", "trees", "conifer.glb")
PERF_REPORT_MD = os.path.join(REPO, "docs", "perf_report.md")
PERF_COPIES = 20
PERF_REQUIRED = [("A_import", "cold"), ("A_import", "hot"),
                 ("B_material", "cold"), ("B_material", "hot"),
                 ("C_collision_unity", "n/a")]
PERF_REPORT_MARKERS = ("## 方法说明", "计时边界", "只对本机配置有效")


def _win_hw():
    """CPU / GPU 名称（Windows CIM）；拿不到就退回 platform，不让整条命令挂掉。"""
    import platform
    cpu = platform.processor() or "unknown"
    gpu = "unknown"
    try:
        ps = ("$c=(Get-CimInstance Win32_Processor | Select-Object -First 1).Name;"
              "$g=(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; ';"
              "Write-Output $c; Write-Output $g")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60)
        lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
        if len(lines) >= 1:
            cpu = lines[0]
        if len(lines) >= 2:
            gpu = lines[1]
    except Exception:  # noqa: BLE001
        pass
    return cpu, gpu


def _mem_total_gb():
    try:
        import ctypes

        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return round(st.ullTotalPhys / float(1024 ** 3), 1)
    except Exception:  # noqa: BLE001
        return None


def _ue_version(ue_path):
    engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(ue_path))))
    bv = os.path.join(engine, "Build", "Build.version")
    if not os.path.isfile(bv):
        return None
    try:
        with open(bv, "r", encoding="utf-8-sig") as fh:
            d = json.load(fh)
        return "UE %s.%s.%s (CL %s, %s)" % (d.get("MajorVersion"), d.get("MinorVersion"),
                                            d.get("PatchVersion"), d.get("Changelist"),
                                            d.get("BranchName"))
    except Exception:  # noqa: BLE001
        return None


def _unity_version(project):
    p = os.path.join(project, "ProjectSettings", "ProjectVersion.txt")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8-sig") as fh:
            for ln in fh:
                if ln.strip().startswith("m_EditorVersion:"):
                    return ln.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _machine_info(args):
    cpu, gpu = _win_hw()
    return {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "os": os.environ.get("OS", "Windows"),
        "cpu": cpu,
        "cores": os.cpu_count(),
        "ramGb": _mem_total_gb(),
        "gpu": gpu,
        "ue": _ue_version(args.ue),
        "unity": _unity_version(os.path.abspath(args.unity_project)),
        "python": sys.version.split()[0],
    }


def _ddc_dirs(args):
    cands = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "UnrealEngine", "Common", "DerivedDataCache"),
             r"D:\UECache",
             os.path.join(os.path.dirname(os.path.abspath(args.project)), "DerivedDataCache")]
    out = []
    for d in cands:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _ddc_stat(dirs):
    files, size = 0, 0
    for d in dirs:
        for root, _ds, fs in os.walk(d):
            for f in fs:
                files += 1
                try:
                    size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return {"dirs": list(dirs), "files": files, "bytes": size}


def _copy_perf_glbs(dst, n):
    """把同一份 conifer.glb 复制成 n 个字节级相同、文件名不同的副本（不进仓库）。

    DDC 键包含源文件名 → 20 个副本不是同一个 DDC 键，但几何/贴图内容完全一致。
    """
    if not os.path.isfile(PERF_GLB_SRC):
        raise RuntimeError("找不到 perf 源 glb: " + PERF_GLB_SRC)
    import hashlib
    with open(PERF_GLB_SRC, "rb") as fh:
        src_sha = hashlib.sha256(fh.read()).hexdigest()
    out = []
    for i in range(n):
        p = os.path.join(dst, "perf_import_%02d.glb" % i)
        shutil.copyfile(PERF_GLB_SRC, p)
        with open(p, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != src_sha:
                raise RuntimeError("副本 SHA 不一致: " + p)
        out.append(p)
    return out, src_sha


def _perf_groups(rows):
    g = {}
    for r in rows:
        g.setdefault((r.get("segment"), r.get("cold_warm")), []).append(r)
    return g


def _perf_summary(rows):
    out = []
    for (seg, cw), rs in sorted(_perf_groups(rows).items(),
                                key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        vals = sorted(float(r["duration_s"]) for r in rs if r.get("duration_s") is not None)
        if not vals:
            continue
        mid = len(vals) // 2
        p50 = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
        out.append({"segment": seg, "cold_warm": cw, "n": len(vals),
                    "min": vals[0], "p50": p50, "max": vals[-1]})
    return out


def _perf_assert(rows, report_path, rounds=3):
    """4 条断言：采样齐全 / 每次 >0 / min≤p50≤max 且非退化 / 报告有方法说明段。

    注意：这里**没有任何绝对耗时阈值**——只查记录完整、数字自洽、可复现。
    """
    fails = []
    groups = _perf_groups(rows)

    # 断言 1：A/B 冷热各 rounds 次、C 段 rounds 次（Unity 侧无 DDC，如实标 n/a）
    for seg, cw in PERF_REQUIRED:
        rs = groups.get((seg, cw), [])
        got_rounds = sorted(set(int(r.get("round") or 0) for r in rs))
        if len(rs) != rounds or got_rounds != list(range(1, rounds + 1)):
            fails.append("断言1 %s/%s 采样 %d 次 rounds=%r（要求 %d 次 1..%d）"
                         % (seg, cw, len(rs), got_rounds, rounds, rounds))
    print("[1/4] 采样齐全（A/B 冷热各 %d + C %d） → %s"
          % (rounds, rounds, "PASS" if not fails else "FAIL"))

    # 断言 2：每次采样 duration_s > 0
    bad2 = ["%s/%s r%s=%r" % (r.get("segment"), r.get("cold_warm"), r.get("round"), r.get("duration_s"))
            for r in rows if r.get("duration_s") is None or float(r.get("duration_s") or 0) <= 0]
    print("[2/4] 每次采样 duration_s > 0（%d 条） → %s" % (len(rows), "PASS" if not bad2 else "FAIL"))
    if bad2:
        fails.append("断言2 非法耗时: " + "; ".join(bad2))

    # 断言 3：min ≤ p50 ≤ max，且样本不能全都一模一样（防把采样写死）
    summary = _perf_summary(rows)
    bad3 = []
    for s in summary:
        if not (s["min"] <= s["p50"] <= s["max"]):
            bad3.append("%s/%s min=%.6f p50=%.6f max=%.6f" % (s["segment"], s["cold_warm"],
                                                              s["min"], s["p50"], s["max"]))
        elif s["n"] >= 2 and not (s["max"] > s["min"]):
            bad3.append("%s/%s %d 次采样完全相同（max==min=%.6f，疑似写死）"
                        % (s["segment"], s["cold_warm"], s["n"], s["max"]))
    print("[3/4] 汇总表 min≤p50≤max 且样本非退化（%d 组） → %s"
          % (len(summary), "PASS" if not bad3 else "FAIL"))
    if bad3:
        fails.append("断言3 " + "; ".join(bad3))

    # 断言 4：报告存在且含方法说明段（防"只贴数字不写口径"）
    text = ""
    if os.path.isfile(report_path):
        with open(report_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    missing = [m for m in PERF_REPORT_MARKERS if m not in text]
    ok4 = bool(text) and not missing
    print("[4/4] 报告方法说明段（%s） → %s"
          % (os.path.basename(report_path), "PASS" if ok4 else "FAIL"))
    if not ok4:
        fails.append("断言4 报告缺失/缺标记 %r（%s）"
                     % (missing, report_path if os.path.isfile(report_path) else "<not written>"))
    return fails, summary


def _fmt_sec(v):
    return "%.4f" % float(v)


def _rel(path):
    try:
        return os.path.relpath(path, REPO)
    except ValueError:
        return path


def _perf_sample_rows(rows):
    """按 A/B 冷热、C 分组排版原始采样（报告用）。"""
    out = []
    for (seg, cw), rs in sorted(_perf_groups(rows).items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        for r in sorted(rs, key=lambda x: int(x.get("round") or 0)):
            out.append((seg, cw, r))
    return out


def _write_perf_report(out_json, rows, summary, machine, notes, failures):
    glb_sha = notes.get("glbSha256") or "?"
    lines = []
    lines.append("# 性能表（v1.10）：导入 / 材质 / 碰撞 × 20 资产")
    lines.append("")
    lines.append("> 生成：%s｜原始采样：`%s`" % (machine.get("date"), _rel(out_json)))
    lines.append("> **这些数字只对本机配置有效，换机器需重跑 `python tools/run_pcg_chain.py --perf`。**")
    lines.append("")
    lines.append("## 机器与版本")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    for k, label in (("cpu", "CPU"), ("cores", "逻辑核"), ("ramGb", "RAM (GB)"), ("gpu", "GPU"),
                     ("ue", "UE"), ("unity", "Unity"), ("python", "Python"), ("date", "采样时间")):
        lines.append("| %s | %s |" % (label, machine.get(k)))
    lines.append("")
    lines.append("## 方法说明")
    lines.append("")
    lines.append("### 计时边界（与 `ue/Content/Python/perf_probe.py` / `unity/Editor/ManifestImporter.cs` 逐字对应）")
    lines.append("")
    lines.append("| 段 | 从哪到哪 | 20 资产定义 |")
    lines.append("|---|---|---|")
    lines.append("| **A. 导入** | `t0` 紧贴 `import_asset_tasks([task])` 之前，`t1` 紧跟其返回之后；"
                 "含 Interchange 翻译 glb、建 StaticMesh/贴图资产、`save=True` 落盘；"
                 "不含材质规范化、不含 UE 启动、不含副本复制 | 同一份 conifer.glb 的 20 个字节级相同副本"
                 "（`perf_import_00..19.glb`，SHA256 `%s`），导入到每样本唯一的 `/Game/EAP/Perf/<tag>/A/imp_XX` |" % glb_sha[:16])
    lines.append("| **B. 材质** | `t0` 紧贴 `_normalize_materials(mesh, ...)` 之前，`t1` 紧跟其返回之后；"
                 "含母材质复用（母材质已在本样本计时前预热）、MIC 创建、BaseColor/Normal 贴图参数、"
                 "三档压缩设置、roughness 灰度贴图生成与导入、`save_loaded_asset` 落盘；不含 A 段导入 | "
                 "A 段导入出来的 20 个资产，各 2 个材质槽（Bark: OPAQUE+Normal / Leaves: BLEND 无 Normal） |")
    lines.append("| **C. 碰撞（Unity）** | 每个 prefab 副本从\"实例化\"到\"加完 `MeshCollider(convex)` + "
                 "`Physics.BakeMesh`\"；含实例化/销毁副本、凸包烘焙；不含 prefab 副本复制、模型导入、材质。"
                 "Unity 2022.3 的 `BakeMesh` 返回 void（无法回读成功与否）→ 以调用未抛异常 + "
                 "`MeshCollider.convex`/`sharedMesh` 读回计数当证据 | "
                 "20 个 prefab 副本（conifer/broadleaf 各 10 份，从 v1.8 的真实 prefab 复制） |")
    lines.append("| **C. 碰撞（UE）** | 无独立可测耗时 | 1,892 实例 ISM 的碰撞是组件开关 + profile 设置"
                 "（一次性标志，亚毫秒级），没有可比的三次采样 → **不编数字**；碰撞证据见 "
                 "`docs/placed_tree_multi_verify.json`（v1.9 已验证 enabled/BlockAll/1892/meshHasCollision） |")
    lines.append("")
    lines.append("### 20 资产的真实定义（坦白）")
    lines.append("")
    lines.append("- A/B 的 20 个资产是**同一棵 conifer glb 的 20 个副本**，不是 20 个不同资产；"
                 "它们字节级相同（SHA256 一致），DDC 键含源文件名所以是 20 个不同键，但几何/贴图工作量等价。")
    lines.append("- 冷样本是**单进程 20 资产批处理**：第 1 个副本付完整翻译成本，第 2~20 个在进程内存 DDC 里"
                 "共享第 1 个的结果。这是批处理口径的固有性质，不是把冷样本偷换成热样本（进程起点没有磁盘 DDC）。")
    lines.append("- C 段 Unity 的 20 个 prefab 是 2 个真实 prefab（conifer/broadleaf）各复制 10 份；"
                 "凸包烘焙时每个副本用独立的网格实例（否则 2 个源网格的烘焙结果会被共享，耗时塌成 0）。")
    lines.append("")
    lines.append("### 冷/热 DDC 处理")
    lines.append("")
    lines.append("- DDC 目录（快照）：%s" % "、".join("`%s`" % d for d in (notes.get("ddcDirs") or [])))
    lines.append("- **冷**：UE 进程命令带 `-DDC-ForceMemoryCache`（DDC 只驻内存，磁盘/共享 DDC 不读不写）；"
                 "每个冷样本前后各拍一次 DDC 目录快照，files/bytes 未变即为生效。")
    lines.append("- **热**：不带该 flag，走磁盘 DDC；3 次热采样前先跑一次**不计时**的热启动（只做 A 段）把 DDC 填满。")
    lines.append("- 冷热差不做断言；清不掉/没生效就如实标（下表\"结论\"列）。")
    lines.append("")
    lines.append("| 样本 | 模式 | flag | DDC files 前→后 | DDC bytes 前→后 | 结论 |")
    lines.append("|---|---|---|---|---|---|")
    for p in notes.get("ddcProof") or []:
        lines.append("| %s r%s | %s | `%s` | %s → %s | %s → %s | %s |"
                     % (p.get("segment"), p.get("round"), p.get("mode"), p.get("flag"),
                        p.get("filesBefore"), p.get("filesAfter"),
                        p.get("bytesBefore"), p.get("bytesAfter"), p.get("verdict")))
    lines.append("")
    lines.append("## 汇总表")
    lines.append("")
    lines.append("| 段 | 状态 | n | min (s) | p50 (s) | max (s) |")
    lines.append("|---|---|---|---|---|---|")
    for s in summary:
        lines.append("| %s | %s | %d | %s | %s | %s |"
                     % (s["segment"], s["cold_warm"], s["n"], _fmt_sec(s["min"]),
                        _fmt_sec(s["p50"]), _fmt_sec(s["max"])))
    lines.append("")
    lines.append("## 原始采样")
    lines.append("")
    lines.append("| 段 | 状态 | round | duration_s | 备注 |")
    lines.append("|---|---|---|---|---|")
    for seg, cw, r in _perf_sample_rows(rows):
        extra = ""
        if seg == "A_import":
            pa = r.get("per_asset_s") or []
            if pa:
                extra = "20 副本；首个 %.4fs / 其余 min %.4fs" % (pa[0], min(pa[1:]) if len(pa) > 1 else pa[0])
        elif seg == "B_material":
            extra = "20 资产 × 2 槽；warmup(不计时) %.2fs" % float(r.get("warmup_s") or 0)
        elif seg == "C_collision_unity":
            extra = "prefabs=%s colliders=%s convex=%s bakeCalls=%s bakeErrors=%s" % (
                r.get("prefabs"), r.get("colliders"), r.get("convexColliders"),
                r.get("bakeCalls"), r.get("bakeErrors"))
        lines.append("| %s | %s | %s | %s | %s |"
                     % (seg, cw, r.get("round"), _fmt_sec(r.get("duration_s")), extra))
    lines.append("")
    lines.append("原始 JSON：`%s`（每次采样一行，含 per-asset 明细与机器信息）。" % _rel(out_json))
    lines.append("")
    lines.append("## 断言")
    lines.append("")
    lines.append("| # | 断言 | 结果 |")
    lines.append("|---|---|---|")
    lines.append("| 1 | 三段 × 冷热 × 3 次采样齐全（A/B 各 3 冷 + 3 热；C 3 次，无 DDC 标 n/a） | %s |"
                 % ("PASS" if not [f for f in failures if f.startswith("断言1")] else "FAIL"))
    lines.append("| 2 | 每次采样 duration_s > 0 | %s |"
                 % ("PASS" if not [f for f in failures if f.startswith("断言2")] else "FAIL"))
    lines.append("| 3 | 汇总表 min ≤ p50 ≤ max 且样本非退化（防写死） | %s |"
                 % ("PASS" if not [f for f in failures if f.startswith("断言3")] else "FAIL"))
    lines.append("| 4 | 报告含方法说明段 | %s |"
                 % ("PASS" if not [f for f in failures if f.startswith("断言4")] else "FAIL"))
    lines.append("")
    lines.append("## 已知噪音源")
    lines.append("")
    lines.append("- DDC 状态：冷样本用 `-DDC-ForceMemoryCache` 强制无磁盘缓存；热样本依赖前序热启动。")
    lines.append("- 后台进程 / Windows Defender 对 `%TEMP%` 与 UE 资产的实时扫描。")
    lines.append("- 笔记本温度墙 / 频率漂移：A/B 每样本约几十秒，长跑后半段可能降频。")
    lines.append("- B 段的大头是 `_make_roughness_texture` 的**纯 Python** PNG 解码/缩放/编码（CPU 单线程），"
                 "不是 UE 材质系统本身；换实现会显著改变 B 的数字。")
    lines.append("- Unity 侧 C 段 3 轮在同一进程内连续跑（Unity 无 DDC/冷热语义），第 1 轮含首次资产加载，后两轮更热。")
    lines.append("- `-nullrhi`：A/B 不编译渲染管线着色器；真实带 RHI 的首次导入会额外付 Nanite/着色器编译成本。")
    lines.append("")
    lines.append("**声明：以上数字只对本机配置有效，换机器/换 UE·Unity 版本需重跑 `--perf`，不要跨机器比较。**")
    lines.append("")
    d = os.path.dirname(PERF_REPORT_MD)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(PERF_REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return PERF_REPORT_MD


def _run_perf_from(args):
    """复用已有采样 JSON：重算汇总/报告/断言（反验用，不再跑 UE/Unity）。"""
    path = os.path.abspath(args.perf_from)
    if not os.path.isfile(path):
        print("CHAIN FAIL: 找不到采样 JSON: " + path)
        return 1
    with open(path, "r", encoding="utf-8-sig") as fh:
        payload = json.load(fh)
    rows = payload.get("runs") or []
    machine = payload.get("machine") or {}
    notes = payload.get("notes") or {}
    print("[0/4] 复用采样：%s（%d 条）" % (path, len(rows)))
    summary = _perf_summary(rows)
    payload["summary"] = summary
    # 先落一版报告给断言 4 读，再按断言结果重写
    _write_perf_report(path, rows, summary, machine, notes, [])
    fails, summary = _perf_assert(rows, PERF_REPORT_MD, rounds=args.perf_rounds)
    _write_perf_report(path, rows, summary, machine, notes, fails)
    for s in summary:
        print("     %-20s %-5s n=%d min=%.4f p50=%.4f max=%.4f"
              % (s["segment"], s["cold_warm"], s["n"], s["min"], s["p50"], s["max"]))
    if fails:
        print("\nCHAIN FAIL: " + " | ".join(fails))
        return 1
    print("\nPERF PASS rows=%d groups=%d（复用 %s）" % (len(rows), len(summary), os.path.basename(path)))
    return 0


def _run_perf(args):
    """三段性能表：A 导入 / B 材质（UE，冷热各 3 次）+ C 碰撞（Unity 侧 3 次，UE 侧如实不报）。"""
    manifest = os.path.abspath(args.manifest)
    project = os.path.abspath(args.unity_project)
    fails = []
    print("[0/4] 性能表：A 导入 / B 材质（UE 冷热各 %d 次）+ C 碰撞（Unity %d 次）"
          % (args.perf_rounds, args.perf_rounds))

    if _unity_running():
        print("CHAIN FAIL: Unity.exe 正在运行，先关掉 Unity 再来")
        return 1
    for p, label in ((args.ue, "UnrealEditor-Cmd"), (args.unity, "Unity.exe")):
        if not os.path.isfile(p):
            fails.append("找不到 %s: %s" % (label, p))
    if not os.path.isfile(manifest):
        fails.append("找不到 manifest: " + manifest)
    if not os.path.isdir(project):
        fails.append("找不到 Unity 工程: " + project)
    if not os.path.isfile(PERF_GLB_SRC):
        fails.append("找不到 perf 源 glb: " + PERF_GLB_SRC)
    if fails:
        print("CHAIN FAIL: " + " | ".join(fails))
        return 1

    changed = _deploy_unity_script(project)
    print("[0/4] 消费端脚本 %s → %s" % ("已更新" if changed else "无变化", project))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(REPO, "docs", "perf_%s.json" % stamp)
    temp = tempfile.mkdtemp(prefix="eap_perf_")
    glb_dir = os.path.join(temp, "glb")
    os.makedirs(glb_dir)
    rows = []
    notes = {}
    machine = _machine_info(args)
    try:
        copies, glb_sha = _copy_perf_glbs(glb_dir, PERF_COPIES)
        print("[0/4] 同源副本 %d 个 → %s（SHA256 %s…）" % (len(copies), glb_dir, glb_sha[:16]))
        print("[0/4] 机器: %s | %s | RAM %s GB | %s | %s | %s"
              % (machine.get("cpu"), machine.get("gpu"), machine.get("ramGb"),
                 machine.get("ue"), machine.get("unity"), machine.get("date")))
        ddc_dirs = _ddc_dirs(args)
        notes["ddcDirs"] = ddc_dirs
        notes["glbSha256"] = glb_sha
        notes["ddcProof"] = []

        # ---- C 段（Unity 侧）：先跑一次不计时的 Import 保证 prefab 存在，再 3 轮碰撞采样 ----
        c_json = os.path.join(temp, "perf_unity_c.json")
        c_log = os.path.join(temp, "perf_unity_c.log.txt")
        import_log = os.path.join(temp, "perf_unity_import.log.txt")
        try:
            irc, ilog = _run_unity(args.unity, project, "EAP.ManifestImporter.ImportFromArgs",
                                   manifest, import_log, timeout=1800)
            print("[1/4] Unity 预热导入（不计时）exit=%s" % irc)
            if irc != 0:
                fails.append("C 段预热导入失败 exit=%s | log_tail: %s" % (irc, _tail(ilog)))
            crc, clog = _run_unity(args.unity, project, "EAP.ManifestImporter.PerfCollisionFromArgs",
                                   manifest, c_log, timeout=1800,
                                   extra=["-eapPerfOut", c_json, "-eapPerfRounds", str(args.perf_rounds)])
            cdoc = None
            if os.path.isfile(c_json):
                with open(c_json, "r", encoding="utf-8-sig") as fh:
                    cdoc = json.load(fh)
            if crc != 0 or cdoc is None:
                fails.append("C 段采样失败 exit=%s json=%s | log_tail: %s"
                             % (crc, c_json if cdoc is not None else "<not written>", _tail(clog)))
            for s in (cdoc or {}).get("samples") or []:
                rows.append({
                    "segment": "C_collision_unity",
                    "round": int(s.get("round") or 0),
                    "cold_warm": s.get("coldWarm") or "n/a",
                    "duration_s": float(s.get("durationS") or 0),
                    "prefabs": s.get("prefabs"), "colliders": s.get("colliders"),
                    "convexColliders": s.get("convexColliders"),
                    "bakeCalls": s.get("bakeCalls"), "bakeErrors": s.get("bakeErrors"),
                    "perPrefab_s": s.get("perPrefab") or [],
                    "note": s.get("note"),
                })
                print("[1/4] C round=%s %.4fs prefabs=%s colliders=%s convex=%s bakeCalls=%s bakeErrors=%s"
                      % (s.get("round"), float(s.get("durationS") or 0), s.get("prefabs"),
                         s.get("colliders"), s.get("convexColliders"),
                         s.get("bakeCalls"), s.get("bakeErrors")))
        except subprocess.TimeoutExpired:
            fails.append("C 段 Unity 调用超时（未补插值，缺格如实报）")

        def _ue_sample(mode, rnd, tag, segments="import,material", flag=None):
            """跑一次 UE 探针；冷样本带 -DDC-ForceMemoryCache 并前后快照 DDC 证明生效。"""
            probe_out = os.path.join(temp, "perf_ue_%s_r%d.json" % (mode, rnd))
            env = {
                "EAP_PERF_OUT": probe_out,
                "EAP_PERF_GLB_DIR": glb_dir,
                "EAP_PERF_TAG": tag,
                "EAP_PERF_ROUND": str(rnd),
                "EAP_PERF_COLD_WARM": mode,
                "EAP_PERF_SEGMENTS": segments,
                "EAP_PERF_COPIES": str(PERF_COPIES),
                "EAP_PERF_MANIFEST": manifest,
            }
            cmd = _ue_cmd(args.ue, args.project, PERF_PROBE)
            if flag:
                cmd.append(flag)
            before = _ddc_stat(ddc_dirs)
            try:
                p, _ = _run("%s %s r%d" % ("UE 冷" if mode == "cold" else "UE 热", segments, rnd),
                            cmd, timeout=1800, env=env)
            except subprocess.TimeoutExpired:
                fails.append("%s 样本 %s r%d 超时（缺格如实报，不插值）" % (mode, segments, rnd))
                return None
            after = _ddc_stat(ddc_dirs)
            verdict = "生效：DDC 目录无变化" if before["files"] == after["files"] and before["bytes"] == after["bytes"] \
                else "DDC 目录有变化（见数字）"
            if mode == "hot":
                verdict = "热：走磁盘 DDC（目录变化可有可无）"
            notes["ddcProof"].append({
                "segment": segments, "round": rnd, "mode": mode,
                "flag": flag or "(none)",
                "filesBefore": before["files"], "filesAfter": after["files"],
                "bytesBefore": before["bytes"], "bytesAfter": after["bytes"],
                "verdict": verdict,
            })
            if p.returncode != 0:
                fails.append("%s r%d UE 退出码 %d | tail: %s"
                             % (segments, rnd, p.returncode, _tail(p.stdout or "")))
                return None
            if not os.path.isfile(probe_out):
                fails.append("%s r%d 采样 JSON 未生成: %s" % (segments, rnd, probe_out))
                return None
            with open(probe_out, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)

        # ---- 冷样本：每条样本一个独立进程 + 强制内存 DDC（真冷）----
        for rnd in range(1, args.perf_rounds + 1):
            data = _ue_sample("cold", rnd, "pc%s_c%d" % (stamp.replace("_", ""), rnd),
                              flag="-DDC-ForceMemoryCache")
            for s in (data or {}).get("samples") or []:
                rows.append({
                    "segment": s.get("segment"), "round": rnd, "cold_warm": "cold",
                    "duration_s": float(s.get("duration_s") or 0),
                    "per_asset_s": s.get("per_asset_s") or [],
                    "assets": s.get("assets"), "warmup_s": s.get("warmup_s"),
                    "detail": s.get("detail") or [],
                })
                print("[2/4] 冷 r%d %s %.4fs" % (rnd, s.get("segment"), float(s.get("duration_s") or 0)))

        # ---- 热启动（不计时，只做 A 段）把磁盘 DDC 填满，再采 3 次热样本 ----
        _ue_sample("hot", 0, "pc%s_hw" % stamp.replace("_", ""), segments="import")
        for rnd in range(1, args.perf_rounds + 1):
            data = _ue_sample("hot", rnd, "pc%s_h%d" % (stamp.replace("_", ""), rnd))
            for s in (data or {}).get("samples") or []:
                rows.append({
                    "segment": s.get("segment"), "round": rnd, "cold_warm": "hot",
                    "duration_s": float(s.get("duration_s") or 0),
                    "per_asset_s": s.get("per_asset_s") or [],
                    "assets": s.get("assets"), "warmup_s": s.get("warmup_s"),
                    "detail": s.get("detail") or [],
                })
                print("[3/4] 热 r%d %s %.4fs" % (rnd, s.get("segment"), float(s.get("duration_s") or 0)))
    except Exception as exc:  # noqa: BLE001
        print("CHAIN FAIL: 性能采样异常: %r" % (exc,))
        return 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    for r in rows:
        r["machine"] = machine
    summary = _perf_summary(rows)
    payload = {
        "schema": "eap.perf/1",
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": machine,
        "definitions": {
            "A_import": "同一份 conifer.glb 的 20 个字节级相同副本 → Interchange 导入返回 StaticMesh",
            "B_material": "上述 20 个资产 × 2 槽：母材质复用 + MIC + 贴图参数 + 三档压缩 + roughness 生成",
            "C_collision_unity": "20 个 prefab 副本各加 MeshCollider(convex) + Physics.BakeMesh",
            "C_collision_ue": "ISM 碰撞启用是组件标志（亚毫秒，无独立可测耗时）→ 不报数字",
        },
        "notes": notes,
        "runs": rows,
        "summary": summary,
        "failures": fails,
    }
    print("[3/4] 原始采样 %d 条（A/B 冷热 + C）" % len(rows))

    # 先落一版报告（断言 4 要读它），跑断言，再按最终失败集重写报告与 JSON
    _write_perf_report(out_json, rows, summary, machine, notes, fails)
    assertion_fails, summary = _perf_assert(rows, PERF_REPORT_MD, rounds=args.perf_rounds)
    fails.extend(assertion_fails)
    payload["summary"] = summary
    payload["failures"] = fails
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    _write_perf_report(out_json, rows, summary, machine, notes, fails)
    for s in summary:
        print("     %-20s %-5s n=%d min=%.4f p50=%.4f max=%.4f"
              % (s["segment"], s["cold_warm"], s["n"], s["min"], s["p50"], s["max"]))
    print("[4/4] 原始采样 → %s" % out_json)
    print("      汇总报告 → %s" % PERF_REPORT_MD)
    if fails:
        print("\nCHAIN FAIL: " + " | ".join(fails))
        return 1
    print("\nPERF PASS rows=%d groups=%d → %s" % (len(rows), len(summary), out_json))
    print("产物: %s" % out_json)
    print("      %s" % PERF_REPORT_MD)
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
    ap.add_argument("--real-multi", action="store_true",
                    help="真实双树种混排：conifer + broadleaf 同一份散点 → 2 asset → UE 2 个 ISM 落位/读回")
    ap.add_argument("--real-multi-seed", type=int, default=20261123,
                    help="--real-multi fixture 的固定随机 seed")
    ap.add_argument("--expect-placements", type=int, default=None,
                    help="--real-multi 期望总实例数（默认 1892，200×200 m @ 0.05/m²）")
    ap.add_argument("--with-compile", action="store_true",
                    help="--real-multi 额外跑带 RHI 的材质编译复检（本机会 OOM，默认关闭）")
    ap.add_argument("--unity-check", action="store_true",
                    help="Unity 端吃真模型：batchmode 导入 + 读回 + 4 条断言（独立的第 5 条自验命令）")
    ap.add_argument("--unity-verify-only", action="store_true",
                    help="--unity-check 时跳过导入，只跑读回（反向验证清态用）")
    ap.add_argument("--unity-placements-check", action="store_true",
                    help="Unity 端 placements 落位：换算验证 + batchmode 落位/读回 + 4 条断言（独立命令）")
    ap.add_argument("--unity-placements-verify-only", action="store_true",
                    help="--unity-placements-check 时跳过落位，只读回（反向验证清态用，绕开场景自愈）")
    ap.add_argument("--parity-check", action="store_true",
                    help="双端逐实例对账：UE 读回 vs Unity 读回 + 4 条断言 + 三列对照表（独立命令）")
    ap.add_argument("--parity-inject-yaw-deg", type=float, default=0.0,
                    help="反向验证：读 Unity 证据时给 yaw 加偏移（默认 0；0.1 应当只挂断言 3）")
    ap.add_argument("--perf", action="store_true",
                    help="三段性能表：A 导入 / B 材质（UE 冷热各 3 次）+ C 碰撞（Unity 3 次）")
    ap.add_argument("--perf-from", default=None,
                    help="复用已有 perf_*.json，只重算汇总/报告/4 条断言（反验用，不再采样）")
    ap.add_argument("--perf-rounds", type=int, default=3, help="每段每状态采样次数（任务单要求 3）")
    ap.add_argument("--unity", default=DEFAULT_UNITY, help="Unity.exe 路径")
    ap.add_argument("--unity-project", default=DEFAULT_UNITY_PROJECT, help="Unity 工程路径")
    ap.add_argument("--manifest", default=REAL_MULTI_MANIFEST_JSON,
                    help="--unity-check 读取的 manifest（默认 schemas/tree_manifest_multi.json）")
    args = ap.parse_args(argv[1:])

    if args.perf_from:
        return _run_perf_from(args)
    if args.perf:
        return _run_perf(args)
    if args.parity_check:
        return _run_parity_check(args)
    if args.unity_placements_check:
        return _run_unity_placements_check(args)
    if args.unity_check:
        return _run_unity_check(args)
    if args.multi and args.real_multi:
        print("--multi 与 --real-multi 只能选一个")
        return 2
    if args.real_multi:
        return _run_real_multi(args)
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
