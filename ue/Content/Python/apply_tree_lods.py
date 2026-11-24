"""EAP · 给两棵树装 4 级 LOD（UE 侧，v1.12）

首选用 `StaticMeshEditorSubsystem.import_lod` 把 Blender Decimate 产物装进 LOD1/2/3，
再设 screen size（1.0 / 0.5 / 0.25 / 0.1），读回证据写 `docs/tree_lods_ue.json`。

**不用"复制 LOD0 三份"或"只改 screen size"冒充**：每一级都必须来自
`assets/raw/trees/lod/{species}_lod{1,2,3}.fbx`（真几何，见 docs/tree_lods.json 的 SHA）。

Nanite 注意：`StaticMesh.get_num_triangles(0)` 只读渲染数据；conifer/broadleaf 当前开着
Nanite，LOD0 读到的是 fallback（1414 / 2354）。所以本脚本读 LOD0 时会**在内存里临时**
把 nanite_settings.enabled 置 False（不 save、读完立即恢复 True）再读，读到的
3,370 / 3,505 才是源网格的真实三角面数。证据里同时记两个值（naniteOn / naniteOff）。

环境变量：
- EAP_LOD_OUT       证据输出，默认 <repo>/docs/tree_lods_ue.json
- EAP_LOD_FBX_DIR   LOD fbx 目录，默认 <repo>/assets/raw/trees/lod
- EAP_LOD_SPECIES   逗号分隔，默认 conifer,broadleaf
- EAP_LOD_SCREENS   逗号分隔，默认 1.0,0.5,0.25,0.1
- EAP_LOD_RESET     1（默认）= 装之前先删掉已有 custom LOD，保证重复跑结果一致
"""
import json
import os

import unreal

REPO = r"D:\Projects\AssetPipeline-EngineAgnostic"
OUT_JSON = os.environ.get("EAP_LOD_OUT") or os.path.join(REPO, "docs", "tree_lods_ue.json")
FBX_DIR = os.environ.get("EAP_LOD_FBX_DIR") or os.path.join(REPO, "assets", "raw", "trees", "lod")
SPECIES = [s.strip() for s in (os.environ.get("EAP_LOD_SPECIES") or "conifer,broadleaf").split(",") if s.strip()]
SCREENS = [float(x) for x in (os.environ.get("EAP_LOD_SCREENS") or "1.0,0.5,0.25,0.1").split(",") if x.strip()]
RESET = (os.environ.get("EAP_LOD_RESET") or "1") != "0"
LEVELS = ("lod1", "lod2", "lod3")

MESH_PATHS = {
    "conifer": "/Game/EAP/Trees/conifer/StaticMeshes/conifer.conifer",
    "broadleaf": "/Game/EAP/Trees/broadleaf/StaticMeshes/broadleaf.broadleaf",
}


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        _warn("safe() 失败: %r" % (exc,))
        return default


def _nanite_enabled(mesh):
    return bool(_safe(lambda: mesh.get_editor_property("nanite_settings").get_editor_property("enabled"), False))


def _set_nanite(mesh, enabled):
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", bool(enabled))
    mesh.set_editor_property("nanite_settings", ns)


def _read_counts(mesh, num_lods):
    """读每一级的 triCount / vertexCount。

    Nanite 开着时 LOD0 的 get_num_triangles 是 fallback，所以在内存里临时关掉
    （不 save，读完恢复），拿源数据真值。LOD1+ 是 import_lod 装进来的普通渲染数据，
    开关 Nanite 不影响它们，但为口径统一也在同一状态下读。
    """
    was = _nanite_enabled(mesh)
    on_counts = [_safe(lambda i=i: int(mesh.get_num_triangles(i)), -1) for i in range(num_lods)]
    if not was:
        return on_counts, on_counts, False
    _set_nanite(mesh, False)
    off_counts = [_safe(lambda i=i: int(mesh.get_num_triangles(i)), -1) for i in range(num_lods)]
    off_verts = [_safe(lambda i=i: int(mesh.get_num_vertices(i)), -1) for i in range(num_lods)]
    _set_nanite(mesh, True)
    return off_counts, on_counts, True, off_verts


def _try(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _lod_material_slots(sub, mesh, lod):
    """每级 section 数、各 section 用的材质槽下标、UV 通道数（证明两材质槽与 UV 没丢）。"""
    nsec = _safe(lambda: int(mesh.get_num_sections(lod)), 0) or 0
    slots = []
    for s in range(nsec):
        slots.append(_safe(lambda s=s: int(sub.get_lod_material_slot(mesh, lod, s)), -1))
    uv = _try(lambda: int(sub.get_num_uv_channels(mesh, lod)), -1)
    if uv < 0:
        uv = _try(lambda: int(sub.get_num_uv_channels(mesh)), -1)
    return nsec, slots, uv


def apply_species(sub, species):
    path = MESH_PATHS[species]
    mesh = _safe(lambda: unreal.load_asset(path))
    if mesh is None:
        return {"ok": False, "problems": ["load_asset 失败: %s" % path], "assetPath": path}

    problems = []
    before = int(_safe(lambda: mesh.get_num_lods(), 0) or 0)
    _log("%s: 装前 numLods=%d" % (species, before))

    removed = None
    if RESET and before > 1:
        removed = _safe(lambda: bool(sub.remove_lods(mesh)), None)
        after_reset = int(_safe(lambda: mesh.get_num_lods(), 0) or 0)
        _log("%s: remove_lods() -> %r, numLods=%d" % (species, removed, after_reset))
        if after_reset != 1:
            problems.append("remove_lods 后 numLods=%d（期望 1）" % after_reset)

    import_results = []
    for idx, level in enumerate(LEVELS, start=1):
        fbx = os.path.join(FBX_DIR, "%s_%s.fbx" % (species, level))
        if not os.path.isfile(fbx):
            problems.append("缺 LOD 源文件: %s" % fbx)
            continue
        # import_lod 返回 int32：装/换成功返回 lod_index，失败返回 INDEX_NONE(-1)
        res = _safe(lambda idx=idx, fbx=fbx: int(sub.import_lod(mesh, idx, fbx)), -1)
        import_results.append({"lodIndex": idx, "sourceFbx": fbx, "returnedIndex": res})
        _log("%s: import_lod(%d, %s) -> %d" % (species, idx, os.path.basename(fbx), res))
        if res != idx:
            problems.append("import_lod 失败: LOD%d <- %s（返回 %d）" % (idx, fbx, res))

    screen_ok = _safe(lambda: bool(sub.set_lod_screen_sizes(mesh, SCREENS)), None)
    _log("%s: set_lod_screen_sizes(%r) -> %r" % (species, SCREENS, screen_ok))
    if not screen_ok:
        problems.append("set_lod_screen_sizes 失败")

    _safe(lambda: unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=True))

    num_lods = int(_safe(lambda: mesh.get_num_lods(), 0) or 0)
    read_screens = [round(float(x), 6)
                    for x in (_safe(lambda: list(sub.get_lod_screen_sizes(mesh)), []) or [])]
    counts, on_counts, nanite_was, off_verts = _read_counts(mesh, num_lods)

    if num_lods != 4:
        problems.append("numLods=%d（期望 4）" % num_lods)
    if read_screens != [round(x, 6) for x in SCREENS]:
        problems.append("读回 screenSizes=%r（期望 %r）" % (read_screens, SCREENS))
    lods = []
    for i in range(num_lods):
        nsec, slots, uv = _lod_material_slots(sub, mesh, i)
        lods.append({
            "index": i,
            "triCount": counts[i],
            "triCountNaniteOn": on_counts[i],
            "vertexCount": off_verts[i],
            "screenSize": read_screens[i] if i < len(read_screens) else None,
            "sectionCount": nsec,
            "materialSlotIndices": slots,
            "uvChannelCount": uv,
        })
    if len(counts) >= 2:
        for i in range(1, len(counts)):
            if not (counts[i] > 0 and counts[i] < counts[i - 1]):
                problems.append("triCount 未严格递减: LOD%d=%s, LOD%d=%s"
                                % (i - 1, counts[i - 1], i, counts[i]))
    for lv in lods:
        if lv["sectionCount"] != 2:
            problems.append("LOD%d sectionCount=%d（期望 2，两材质槽）" % (lv["index"], lv["sectionCount"]))
        if not (lv["uvChannelCount"] and lv["uvChannelCount"] > 0):
            problems.append("LOD%d uvChannelCount=%r（期望 >0）" % (lv["index"], lv["uvChannelCount"]))

    return {
        "ok": not problems,
        "assetPath": path,
        "numLodsBefore": before,
        "removeLodsReturned": removed,
        "importResults": import_results,
        "numLods": num_lods,
        "screenSizes": read_screens,
        "naniteToggleForLod0Read": nanite_was,
        "materialNamesLod0": [str(_safe(lambda s=s: mesh.get_material(s).get_name(), None))
                               for s in range(_safe(lambda: int(mesh.get_num_sections(0)), 0) or 0)],
        "lods": lods,
        "problems": problems,
    }


def main():
    data = {
        "schema": "eap.tree_lods_ue/1",
        "generator": "apply_tree_lods.py",
        "ueVersion": _safe(lambda: str(unreal.SystemLibrary.get_engine_version()), None),
        "pathUsed": None,
        "apiProbe": {},
        "screenSizesRequested": SCREENS,
        "naniteNote": ("StaticMesh.get_num_triangles(0) 读的是渲染数据；Nanite 开启时为 fallback。"
                       "LOD0 真值 = 内存里临时关 Nanite（不保存）读到的值，读完恢复，见 triCount / triCountNaniteOn。"),
        "species": {},
        "problems": [],
    }

    has_sub = hasattr(unreal, "StaticMeshEditorSubsystem")
    data["apiProbe"]["hasStaticMeshEditorSubsystem"] = has_sub
    if not has_sub:
        data["problems"].append("unreal.StaticMeshEditorSubsystem 不存在")
        data["pathUsed"] = "unavailable"
    else:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        methods = ("import_lod", "set_lod_screen_sizes", "get_lod_screen_sizes",
                   "remove_lods", "get_lod_material_slot")
        for m in methods:
            data["apiProbe"]["has_" + m] = bool(hasattr(sub, m))
        if not hasattr(sub, "import_lod"):
            data["problems"].append("StaticMeshEditorSubsystem.import_lod 不存在（按任务单应如实报阻塞）")
            data["pathUsed"] = "unavailable"
        else:
            data["pathUsed"] = "StaticMeshEditorSubsystem.import_lod"
            for species in SPECIES:
                data["species"][species] = apply_species(sub, species)
                data["problems"].extend(["%s: %s" % (species, p)
                                         for p in data["species"][species].get("problems") or []])

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    _log("树 LOD 路径 = %s，证据 -> %s" % (data["pathUsed"], OUT_JSON))
    if data["problems"]:
        for p in data["problems"]:
            _log("PROBLEM " + p)
        _log("LOD_APPLY_FAIL")
    else:
        _log("LOD_APPLY_OK")


main()
