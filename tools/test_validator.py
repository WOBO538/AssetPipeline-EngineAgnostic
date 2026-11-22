"""EAP 校验器的负例测试：用 Python 真改 JSON，确认每一种坏数据都会被拦住。"""
import copy
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(REPO, "schemas", "manifest.example.json")
VALIDATOR = os.path.join(REPO, "tools", "validate_manifest.py")


def run_case(name, mutate):
    data = json.load(open(BASE, encoding="utf-8-sig"))
    mutate(data)
    path = os.path.join(REPO, "schemas", "_neg_" + name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    proc = subprocess.run([sys.executable, VALIDATOR, path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    os.remove(path)
    first = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith(("ERROR", "WARN"))][:1]
    ok = (proc.returncode != 0)
    print("%-18s exit=%d %s | %s" % (name, proc.returncode, "PASS" if ok else "FAIL(未拦住)",
                                     (first[0][:90] if first else "(no msg)")))
    return ok


def m_normal_srgb(d):
    d["assets"][0]["textures"]["Normal"]["srgb"] = True


def m_npot(d):
    d["assets"][0]["textures"]["BaseColor"]["size"] = [1000, 1000]


def m_units(d):
    d["units"] = "mm"


def m_no_textures(d):
    d["assets"][1]["textures"] = {}


def m_bad_id(d):
    d["assets"][0]["id"] = "SM-Rock A01"


def m_dup_id(d):
    d["assets"][1]["id"] = d["assets"][0]["id"]


def m_bbox(d):
    d["assets"][0]["model"]["boundingBoxM"]["min"] = [1, 1, 1]


def m_unity_path(d):
    d["assets"][0]["engineTargets"]["Unity"]["destPath"] = "Content/EAP/Rock"


def m_version(d):
    d["manifestVersion"] = "2.0"


CASES = [
    ("normal_srgb_true", m_normal_srgb),
    ("texture_not_pot", m_npot),
    ("units_invalid", m_units),
    ("no_textures", m_no_textures),
    ("bad_asset_id", m_bad_id),
    ("duplicate_id", m_dup_id),
    ("bbox_min_gt_max", m_bbox),
    ("unity_path_wrong", m_unity_path),
    ("manifest_version_v2", m_version),
]

if __name__ == "__main__":
    results = [run_case(name, fn) for name, fn in CASES]
    passed = sum(1 for r in results if r)
    print("\n拦截成功 %d/%d" % (passed, len(results)))
    sys.exit(0 if passed == len(results) else 1)
