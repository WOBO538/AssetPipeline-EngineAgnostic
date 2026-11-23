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


def _run_unity(unity, project, method, manifest, log_path, timeout=1200):
    cmd = [unity, "-batchmode", "-quit", "-projectPath", project,
           "-executeMethod", method, "-eapManifest", manifest, "-logFile", log_path]
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
    ap.add_argument("--unity", default=DEFAULT_UNITY, help="Unity.exe 路径")
    ap.add_argument("--unity-project", default=DEFAULT_UNITY_PROJECT, help="Unity 工程路径")
    ap.add_argument("--manifest", default=REAL_MULTI_MANIFEST_JSON,
                    help="--unity-check 读取的 manifest（默认 schemas/tree_manifest_multi.json）")
    args = ap.parse_args(argv[1:])

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
