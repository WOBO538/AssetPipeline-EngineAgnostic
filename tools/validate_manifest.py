"""EAP · manifest 中台校验（阶段一）

不依赖第三方库（有 jsonschema 就顺手用它，没有就跑内置规则）。
退出码：0 = 无 error（可有 warning）；1 = 有 error（消费端不应继续）。

用法：
    python tools/validate_manifest.py schemas/manifest.example.json
    python tools/validate_manifest.py schemas/manifest.example.json --check-files
"""
import json
import os
import re
import sys

REQUIRED_TOP = ["manifestVersion", "pipelineId", "generatedAt", "units", "assets"]
REQUIRED_ASSET = ["id", "name", "category", "type", "model", "textures", "material", "engineTargets"]
ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
CAT_RE = re.compile(r"^[a-z0-9_]+(/[a-z0-9_]+)*$")
TEX_KEYS = {"BaseColor", "Normal", "Roughness", "Metallic", "AO", "ORM", "Height"}


def pot(n):
    return n > 0 and (n & (n - 1)) == 0


def validate(path, check_files=False, allow_npot=False):
    errors, warnings = [], []
    repo = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    try:
        with open(path, encoding="utf-8-sig") as fh:   # 容忍 Windows 编辑器/脚本写入的 BOM
            data = json.load(fh)
    except Exception as exc:
        return {"errors": ["manifest 读不了: %s" % exc], "warnings": [], "assets": 0}

    for key in REQUIRED_TOP:
        if key not in data:
            errors.append("顶层缺字段: " + key)
    if errors:
        return {"errors": errors, "warnings": warnings, "assets": 0}

    ver_parts = str(data["manifestVersion"]).split(".")
    major = ver_parts[0]
    if major != "1":
        errors.append("manifestVersion 主版本不支持: %s" % data["manifestVersion"])
    elif len(ver_parts) > 1 and ver_parts[1].isdigit() and int(ver_parts[1]) > 1:
        warnings.append("manifestVersion 次版本 %s 高于本校验器已知的 1.1（可能有不认识的字段）" % data["manifestVersion"])
    units = data.get("units")
    if units not in ("m", "cm"):
        errors.append("units 必须是 m 或 cm，实为 %r" % units)

    assets = data.get("assets") or []
    if not assets:
        errors.append("assets 为空")
    seen = set()
    for i, a in enumerate(assets):
        tag = "assets[%d]%s" % (i, ("(" + a.get("id", "?")) + ")" if isinstance(a, dict) else "")
        if not isinstance(a, dict):
            errors.append(tag + " 不是对象")
            continue
        for key in REQUIRED_ASSET:
            if key not in a:
                errors.append("%s 缺字段 %s" % (tag, key))
        aid = a.get("id", "")
        if not ID_RE.match(str(aid)):
            errors.append("%s id 不合法（只允许字母数字下划线）: %r" % (tag, aid))
        if aid in seen:
            errors.append("%s id 重复: %s" % (tag, aid))
        seen.add(aid)
        if not CAT_RE.match(str(a.get("category", ""))):
            errors.append("%s category 不合法: %r" % (tag, a.get("category")))
        if a.get("type") not in ("static_mesh", "skinned_mesh", "material_only"):
            errors.append("%s type 不合法: %r" % (tag, a.get("type")))

        model = a.get("model") or {}
        if model.get("unit") != units:
            warnings.append("%s model.unit=%r 与顶层 units=%r 不一致（消费端以 boundingBoxM 为准）" % (tag, model.get("unit"), units))
        # v1.1：模型来源（文件 / 引擎内置）
        msource = model.get("source", "file")
        if msource not in ("file", "engine"):
            errors.append("%s model.source 不合法: %r" % (tag, msource))
        fmt = model.get("format")
        if msource == "engine":
            if not str(model.get("path", "")).startswith("ue:/"):
                errors.append("%s 引擎内置模型 model.path 必须以 ue:/ 开头: %r" % (tag, model.get("path")))
            if fmt != "engine_native":
                errors.append("%s 引擎内置模型 model.format 必须是 engine_native: %r" % (tag, fmt))
        elif fmt not in ("gltf2.0", "fbx", "obj"):
            errors.append("%s model.format 不合法: %r" % (tag, fmt))
        bbox = model.get("boundingBoxM") or {}
        mn, mx = bbox.get("min"), bbox.get("max")
        if not (isinstance(mn, list) and isinstance(mx, list) and len(mn) == 3 and len(mx) == 3):
            errors.append("%s boundingBoxM 必须是 3 元素 min/max" % tag)
        else:
            if any(b <= a0 for a0, b in zip(mn, mx)):
                errors.append("%s boundingBoxM min 必须小于 max" % tag)
        if model.get("triCount", 0) <= 0 and a.get("type") == "static_mesh":
            warnings.append("%s triCount=%r（静态网格应为正数）" % (tag, model.get("triCount")))
        if check_files and model.get("path"):
            if not os.path.isfile(os.path.join(repo, model["path"])):
                warnings.append("%s 模型文件不存在: %s" % (tag, model["path"]))

        textures = a.get("textures") or {}
        # v1.2：template == "none"（引擎默认材质）或 "embedded"（贴图内嵌在 glb 里）才允许空贴图表
        if not textures and (a.get("material") or {}).get("template") not in ("none", "embedded"):
            errors.append("%s textures 为空（material.template 不是 none/embedded）" % tag)
        for chan, t in textures.items():
            if chan not in TEX_KEYS:
                errors.append("%s 未知贴图通道: %s" % (tag, chan))
                continue
            if "path" not in t:
                errors.append("%s 贴图 %s 缺 path" % (tag, chan))
            if chan == "Normal" and t.get("srgb") is not False:
                errors.append("%s Normal 贴图 srgb 必须为 false（当前 %r）" % (tag, t.get("srgb")))
            if chan == "BaseColor" and t.get("srgb") is not True:
                warnings.append("%s BaseColor 贴图 srgb 建议为 true（当前 %r）" % (tag, t.get("srgb")))
            size = t.get("size")
            if size:
                if len(size) != 2 or not all(pot(int(v)) for v in size):
                    msg = "%s %s 贴图尺寸非 2 的幂: %s" % (tag, chan, size)
                    # 默认按硬规则拦住（验收第 5 条）；确需 NPOT 时用 --allow-npot 降级为警告
                    if allow_npot:
                        warnings.append(msg)
                    else:
                        errors.append(msg + "（如确有需要请加 --allow-npot）")
            if check_files and t.get("path"):
                if not os.path.isfile(os.path.join(repo, t["path"])):
                    warnings.append("%s 贴图文件不存在: %s" % (tag, t["path"]))

        targets = a.get("engineTargets") or {}
        if not targets:
            errors.append("%s engineTargets 为空" % tag)
        if "UE5" in targets:
            dest = (targets["UE5"] or {}).get("destPath", "")
            if not str(dest).startswith("/Game/"):
                errors.append("%s UE5.destPath 必须以 /Game/ 开头: %r" % (tag, dest))
        if "Unity" in targets:
            dest = (targets["Unity"] or {}).get("destPath", "")
            if not str(dest).startswith("Assets/"):
                errors.append("%s Unity.destPath 必须以 Assets/ 开头: %r" % (tag, dest))
        if msource == "engine" and "Unity" in targets:
            warnings.append("%s 引擎内置网格没有 glb，Unity 目标端通常应省略" % tag)

        # v1.1：placements —— 同一网格的 N 个实例（PCG 散点）
        placements = a.get("placements")
        if placements is not None:
            if not isinstance(placements, dict):
                errors.append("%s placements 必须是对象" % tag)
            else:
                if placements.get("space") not in ("world", "local"):
                    errors.append("%s placements.space 必须是 world/local: %r" % (tag, placements.get("space")))
                if placements.get("unit") not in (None, "m"):
                    errors.append("%s placements.unit 必须是 m: %r" % (tag, placements.get("unit")))
                count = placements.get("count")
                items = placements.get("items")
                if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                    errors.append("%s placements.count 必须是正整数: %r" % (tag, count))
                if not isinstance(items, list):
                    errors.append("%s placements.items 必须是数组" % tag)
                    items = []
                if isinstance(count, int) and not isinstance(count, bool) and count != len(items):
                    errors.append("%s placements.count=%s 与 items 条数 %d 不一致" % (tag, count, len(items)))
                for j, it in enumerate(items):
                    if not isinstance(it, dict):
                        errors.append("%s placements.items[%d] 不是对象" % (tag, j))
                        continue
                    for key in ("t", "r", "s"):
                        vec = it.get(key)
                        ok = (isinstance(vec, list) and len(vec) == 3 and all(
                            isinstance(x, (int, float)) and not isinstance(x, bool)
                            and x == x and abs(float(x)) != float("inf") for x in vec))
                        if not ok:
                            errors.append("%s placements.items[%d].%s 必须是 3 个有限数字" % (tag, j, key))
                if count and len(items) and not (a.get("engineTargets") or {}).get("UE5"):
                    warnings.append("%s 有 placements 但缺 UE5 目标端，实例无处落位" % tag)

    return {"errors": errors, "warnings": warnings, "assets": len(assets)}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    check_files = "--check-files" in argv[2:]
    allow_npot = "--allow-npot" in argv[2:]
    result = validate(path, check_files=check_files, allow_npot=allow_npot)
    for w in result["warnings"]:
        print("WARN  " + w)
    for e in result["errors"]:
        print("ERROR " + e)
    print("assets=%d errors=%d warnings=%d" % (result["assets"], len(result["errors"]), len(result["warnings"])))
    if result["errors"]:
        print("FAIL")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
