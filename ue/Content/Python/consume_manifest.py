"""EAP · UE5 消费端（阶段一模板）

读 manifest.json → 建/复用母材质 M_EAP_StandardPBR → 建材质实例(MIC)并连贴图
→ 挂简单碰撞 → 写回执报告。**不负责**轴心（UE Python 无该接口，见 README §五）。

用法（UE 编辑器内）：
    import sys; sys.path.append(r"<repo>/ue/Content/Python")
    import consume_manifest
    consume_manifest.run(r"<repo>/schemas/manifest.example.json", dry_run=True)
    consume_manifest.run(r"<repo>/schemas/manifest.example.json")

约定：
- manifest 里 engineTargets.UE5.destPath 是 /Game/... 资产路径
- 贴图若已存在则不重复导入；不存在则尝试从 model/texture 的磁盘相对路径导入
- 任何单个资产失败不中断整批，失败写进报告
"""
import json
import os
import time

import unreal

MATERIAL_TEMPLATE_NAME = "M_EAP_StandardPBR"
SUPPORTED_MAJOR = "1"
TEXTURE_PARAM_MAP = {
    "BaseColor": "BaseColor",
    "Normal": "Normal",
    "ORM": "ORM",
    "Roughness": "Roughness",
    "Metallic": "Metallic",
    "AO": "AO",
    "Height": "Height",
}


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


def _repo_root(manifest_path):
    """manifest 在 <repo>/schemas/ 下，据此推仓库根，供相对路径解析。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(manifest_path)))


def load_manifest(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    major = str(data.get("manifestVersion", "0")).split(".")[0]
    if major != SUPPORTED_MAJOR:
        raise RuntimeError("不支持的 manifestVersion: %s（支持主版本 %s）" % (data.get("manifestVersion"), SUPPORTED_MAJOR))
    return data


def ensure_package_dir(package_path):
    if not unreal.EditorAssetLibrary.does_directory_exist(package_path):
        unreal.EditorAssetLibrary.make_directory(package_path)
        _log("建目录 " + package_path)


def _asset_path(package_path, name):
    return package_path.rstrip("/") + "/" + name


def ensure_parent_material(dest_dir):
    """母材质：4 个纹理参数 + 2 个标量参数；已存在直接复用。"""
    pkg = dest_dir.rstrip("/") + "/" + MATERIAL_TEMPLATE_NAME
    if unreal.EditorAssetLibrary.does_asset_exist(pkg):
        return unreal.EditorAssetLibrary.load_asset(pkg)
    tool = unreal.AssetToolsHelpers.get_asset_tools()
    mat = tool.create_asset(MATERIAL_TEMPLATE_NAME, dest_dir, unreal.Material, unreal.MaterialFactoryNew())
    lib = unreal.MaterialEditingLibrary
    for idx, (key, param) in enumerate(TEXTURE_PARAM_MAP.items()):
        node = lib.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -600, idx * 220)
        node.set_editor_property("parameter_name", param)
        if key == "BaseColor":
            lib.connect_material_property(node, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        elif key == "Normal":
            lib.connect_material_property(node, "RGB", unreal.MaterialProperty.MP_NORMAL)
        elif key == "ORM":
            lib.connect_material_property(node, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
            lib.connect_material_property(node, "G", unreal.MaterialProperty.MP_ROUGHNESS)
            lib.connect_material_property(node, "B", unreal.MaterialProperty.MP_METALLIC)
    for i, (name, default) in enumerate((("NormalStrength", 1.0), ("RoughnessScale", 1.0))):
        sc = lib.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -600, 1600 + i * 120)
        sc.set_editor_property("parameter_name", name)
        sc.set_editor_property("default_value", default)
    lib.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    _log("建母材质 " + pkg)
    return mat


def try_import_texture(dest_dir, tex_name, disk_path):
    """按磁盘相对路径导入贴图（已存在则跳过）。返回 packgage path 或 None。"""
    pkg = _asset_path(dest_dir, tex_name)
    if unreal.EditorAssetLibrary.does_asset_exist(pkg):
        return pkg
    if not disk_path or not os.path.isfile(disk_path):
        _warn("贴图文件不存在，跳过导入: " + str(disk_path))
        return None
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", disk_path)
    task.set_editor_property("destination_path", dest_dir)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", False)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    return pkg if unreal.EditorAssetLibrary.does_asset_exist(pkg) else None


def build_material_instance(asset, dest_dir, repo_root, report, dry_run):
    aid = asset["id"]
    # v1.1：template == "none" 表示用引擎默认材质（如 PCG 散布的引擎内置网格），不建 MIC
    if (asset.get("material") or {}).get("template") == "none":
        _log("%s材质模板 none → 用引擎默认材质，跳过 MIC" % ("[dry] " if dry_run else ""))
        return None
    mi_name = asset.get("engineTargets", {}).get("UE5", {}).get("materialInstance") or ("MI_" + aid)
    mi_pkg = _asset_path(dest_dir, mi_name)
    parent_pkg = _asset_path(dest_dir, MATERIAL_TEMPLATE_NAME)
    if dry_run:
        _log("[dry] 将建 MIC %s ← 母材质 %s" % (mi_pkg, parent_pkg))
        return mi_pkg
    parent = ensure_parent_material(dest_dir)
    if unreal.EditorAssetLibrary.does_asset_exist(mi_pkg):
        mi = unreal.EditorAssetLibrary.load_asset(mi_pkg)
    else:
        tool = unreal.AssetToolsHelpers.get_asset_tools()
        mi = tool.create_asset(mi_name, dest_dir, unreal.MaterialInstanceConstant,
                               unreal.MaterialInstanceConstantFactoryNew())
        mi.set_editor_property("parent", parent)
    lib = unreal.MaterialEditingLibrary
    for key, tex in asset.get("textures", {}).items():
        param = TEXTURE_PARAM_MAP.get(key)
        if not param:
            continue
        disk = os.path.join(repo_root, tex["path"])
        tex_pkg = try_import_texture(dest_dir, "T_" + aid + "_" + key, disk)
        if not tex_pkg:
            report["warnings"].append("%s: 贴图 %s 未导入" % (aid, key))
            continue
        tex_asset = unreal.EditorAssetLibrary.load_asset(tex_pkg)
        lib.set_material_instance_texture_parameter_value(mi, param, tex_asset)
        if key == "Normal" and tex.get("srgb"):
            report["warnings"].append("%s: Normal 贴图 srgb=true（应 false）" % aid)
    for pname, pvalue in (asset.get("material", {}).get("params") or {}).items():
        if isinstance(pvalue, bool):
            continue
        try:
            lib.set_material_instance_scalar_parameter_value(mi, pname, float(pvalue))
        except Exception as exc:  # 参数名在母材质里不存在
            report["warnings"].append("%s: 标量参数 %s 设置失败 (%s)" % (aid, pname, exc))
    unreal.EditorAssetLibrary.save_loaded_asset(mi)
    return mi_pkg


def apply_collision(asset, static_mesh_path, report):
    mode = (asset.get("collision") or {}).get("mode", "box")
    if mode == "none":
        return
    mesh = unreal.EditorAssetLibrary.load_asset(static_mesh_path)
    if not mesh:
        return
    try:
        kind = unreal.SimpleCollisionType.BOX if mode == "box" else unreal.SimpleCollisionType.SPHERE
        unreal.EditorStaticMeshLibrary.add_simple_collisions(mesh, kind)
        unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    except Exception as exc:
        report["warnings"].append("%s: 碰撞生成失败 (%s)" % (asset["id"], exc))


def run(manifest_path, dry_run=False):
    t0 = time.time()
    data = load_manifest(manifest_path)
    repo = _repo_root(manifest_path)
    report = {"manifest": manifest_path, "mode": "dry_run" if dry_run else "real",
              "ok": [], "failed": [], "warnings": [], "startedAt": data.get("generatedAt")}
    for asset in data["assets"]:
        aid = asset["id"]
        try:
            ue = asset.get("engineTargets", {}).get("UE5")
            if not ue:
                report["failed"].append({"id": aid, "reason": "skipped_no_target"})
                continue
            dest_dir = ue["destPath"]
            if not dry_run:
                ensure_package_dir(dest_dir)
            mi_pkg = build_material_instance(asset, dest_dir, repo, report, dry_run)
            model_source = (asset.get("model") or {}).get("source", "file")
            if model_source == "engine":
                if dry_run:
                    _log("[dry] 网格来自引擎内置: %s（跳过导入）" % asset["model"].get("path"))
            elif not dry_run and asset.get("model", {}).get("path"):
                task = unreal.AssetImportTask()
                task.set_editor_property("filename", os.path.join(repo, asset["model"]["path"]))
                task.set_editor_property("destination_path", dest_dir)
                task.set_editor_property("automated", True)
                task.set_editor_property("save", True)
                unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
                mesh_pkg = _asset_path(dest_dir, aid)
                apply_collision(asset, mesh_pkg, report)
            if (asset.get("pivot") or {}).get("mode") not in (None, "none"):
                report["warnings"].append("%s: pivot 需在导出端处理（UE Python 无轴心接口）" % aid)
            # v1.1：placements —— 同一网格的 N 个实例（PCG 散点）。阶段一只读取+统计，落位留阶段二
            placements = asset.get("placements") or {}
            pcount = placements.get("count") or 0
            if pcount:
                if dry_run:
                    _log("[dry] 将放置 %d 个实例 ← %s（space=%s；落位实现见 README 未覆盖项）"
                         % (pcount, aid, placements.get("space")))
                else:
                    _warn("%s: placements 落位未实现（阶段一只读取统计，%d 个）" % (aid, pcount))
                report.setdefault("placements", {})[aid] = pcount
            report["ok"].append({"id": aid, "mic": mi_pkg, "placements": pcount})
        except Exception as exc:
            report["failed"].append({"id": aid, "reason": str(exc)})
            _warn("资产失败 %s: %s" % (aid, exc))
    report["seconds"] = round(time.time() - t0, 2)
    out = os.path.splitext(manifest_path)[0] + ".ue_report.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    _log("完成 ok=%d failed=%d warn=%d placements=%d %.2fs → %s" % (
        len(report["ok"]), len(report["failed"]), len(report["warnings"]),
        sum((report.get("placements") or {}).values()), report["seconds"], out))
    return report
