"""EAP · Layer 1 数据出口链路（一条命令跑完整条链）

    python tools/run_pcg_chain.py
    python tools/run_pcg_chain.py --ue "C:\\Program Files\\Epic Games\\UE_5.7\\Engine\\Binaries\\Win64\\UnrealEditor-Cmd.exe" \
                                  --project "D:\\UE_Projects\\VegetationPCG\\VegetationPCG.uproject"

链路（两次无头 UE + 两次纯 Python）：
  1) UE 无头跑 ue/Content/Python/pcg_export.py → docs/pcg_forest_pilot.json（cm）
  2) tools/pcg_to_manifest.py → schemas/pcg_manifest.json（manifest v1.1，m）
  3) tools/validate_manifest.py → 必须 errors=0
  4) UE 无头跑 consume_manifest.run(..., dry_run=True) → 必须 assets 数、placements 数一致
  5) 断言：散点数 == 实例数 == manifest placements == 消费端报告 placements

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
SCATTER_JSON = os.path.join(REPO, "docs", "pcg_forest_pilot.json")
MANIFEST_JSON = os.path.join(REPO, "schemas", "pcg_manifest.json")
DEFAULT_UE = r"C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
DEFAULT_PROJECT = r"D:\UE_Projects\VegetationPCG\VegetationPCG.uproject"

sys.path.insert(0, os.path.join(REPO, "tools"))


def _run(label, cmd, timeout=900):
    print("[%s] %s" % (label, " ".join(cmd[:2]) + " ..."))
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
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
    args = ap.parse_args(argv[1:])

    fails = []
    if not os.path.isfile(args.ue):
        fails.append("找不到 UnrealEditor-Cmd: " + args.ue)
    if not os.path.isfile(args.project):
        fails.append("找不到 UE 工程: " + args.project)
    if fails:
        print("FAIL " + " | ".join(fails))
        return 1

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
    print("[1/5] 散点: points=%d instanceCount=%s ism_sum=%d" % (points_n, instance_n, ism_n))

    # ---- 2) 生成 manifest ----
    import pcg_to_manifest
    manifest, summ = pcg_to_manifest.build(SCATTER_JSON, MANIFEST_JSON)
    pl_n = summ["placements"]
    print("[2/5] manifest: assets=%d placements=%d -> %s" % (summ["assets"], pl_n, MANIFEST_JSON))

    # ---- 3) 校验 ----
    import validate_manifest
    res = validate_manifest.validate(MANIFEST_JSON)
    for w in res["warnings"]:
        print("     WARN " + w)
    for e in res["errors"]:
        print("     ERROR " + e)
    print("[3/5] validate: assets=%d errors=%d warnings=%d" % (res["assets"], len(res["errors"]), len(res["warnings"])))
    if res["errors"]:
        fails.append("manifest 校验有 %d 个 error" % len(res["errors"]))

    # ---- 4) UE dry-run 消费（读 placements + 统计） ----
    report_path = os.path.splitext(MANIFEST_JSON)[0] + ".ue_report.json"
    ue_placements = None
    if not args.skip_ue_consume:
        tmpdir = tempfile.mkdtemp(prefix="eap_chain_")
        tmp_script = os.path.join(tmpdir, "ue_consume_dryrun.py")
        with open(tmp_script, "w", encoding="utf-8") as fh:
            fh.write("import sys\n")
            fh.write("sys.path.append(r'%s')\n" % os.path.join(REPO, "ue", "Content", "Python"))
            fh.write("import consume_manifest\n")
            fh.write("consume_manifest.run(r'%s', dry_run=True)\n" % MANIFEST_JSON)
        p, _ = _run("4/5 UE dry-run", _ue_cmd(args.ue, args.project, tmp_script))
        if p.returncode != 0:
            fails.append("UE dry-run 退出码 %d" % p.returncode)
        if os.path.isfile(report_path):
            with open(report_path, "r", encoding="utf-8-sig") as fh:
                rep = json.load(fh)
            ue_placements = sum((rep.get("placements") or {}).values())
            print("[4/5] 报告: ok=%d failed=%d placements=%s warnings=%d"
                  % (len(rep.get("ok") or []), len(rep.get("failed") or []), ue_placements,
                     len(rep.get("warnings") or [])))
            if rep.get("failed"):
                fails.append("UE 消费端有失败资产: %s" % [f.get("id") for f in rep["failed"]])
        else:
            fails.append("UE 报告未生成: " + report_path)
    else:
        print("[4/5] 跳过 UE dry-run")

    # ---- 5) 断言 ----
    checks = [
        ("points == instanceCount", points_n == instance_n, "%d vs %s" % (points_n, instance_n)),
        ("points == ism_sum", points_n == ism_n, "%d vs %d" % (points_n, ism_n)),
        ("points == manifest.placements", points_n == pl_n, "%d vs %d" % (points_n, pl_n)),
    ]
    if ue_placements is not None:
        checks.append(("manifest.placements == ue_report.placements", pl_n == ue_placements,
                       "%d vs %s" % (pl_n, ue_placements)))
    print("[5/5] 断言")
    for name, ok, detail in checks:
        print("     %-42s %s  (%s)" % (name, "PASS" if ok else "FAIL", detail))
        if not ok:
            fails.append("%s: %s" % (name, detail))

    if fails:
        print("\nCHAIN FAIL: " + " | ".join(fails))
        return 1
    print("\nCHAIN PASS points=%d assets=%d placements=%d" % (points_n, summ["assets"], pl_n))
    print("产物: %s" % SCATTER_JSON)
    print("      %s" % MANIFEST_JSON)
    if not args.skip_ue_consume:
        print("      %s" % report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
