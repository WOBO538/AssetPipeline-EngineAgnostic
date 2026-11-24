"""EAP · 性能探针（A 导入 / B 材质）：UE 无头进程内计时，写原始采样 JSON。

由 tools/run_pcg_chain.py 的 `--perf` 调度，一个进程只采一个样本（冷或热），
避免"同一进程内存 DDC 变热"把冷样本悄悄变成热样本。

计时边界（与 docs/perf_report.md 的表逐字对应，改这里必须同步报告）：
- A_import   ：`t0` 紧贴 `import_asset_tasks([task])` 之前，`t1` 紧跟其返回之后。
              含 Interchange 翻译 glb、建 StaticMesh/贴图资产、`save=True` 落盘；
              不含材质规范化（B 段单独计）、不含 UE 启动、不含副本复制。
- B_material ：`t0` 紧贴 `_normalize_materials(mesh, ...)` 之前，`t1` 紧跟其返回之后。
              含：母材质复用（母材质已在本样本计时前预热）、MIC 创建/复用、
              BaseColor/Normal 贴图参数、三档压缩设置、roughness 灰度贴图生成与导入、
              `save_loaded_asset` 落盘、旧 MIC 清理。不含 A 段导入。

环境变量：
- EAP_PERF_OUT        采样 JSON 输出路径（必填）
- EAP_PERF_GLB_DIR    20 个同源 glb 副本所在目录（必填）
- EAP_PERF_TAG        本样本的资产命名前缀（必填；不同样本必须不同，防止复用旧资产）
- EAP_PERF_ROUND      轮次（1..3）
- EAP_PERF_COLD_WARM  cold | hot（只用于写进证据，冷却手段在调用方）
- EAP_PERF_SEGMENTS   import,material（默认两段都跑；热启动预热只传 import）
- EAP_PERF_COPIES     副本数（默认 20）
- EAP_PERF_MANIFEST   真实资产 manifest；给了就在样本清理后用真实贴图恢复母材质默认贴图
"""
import json
import os
import struct
import sys
import time

import unreal

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import place_manifest as pm  # noqa: E402

PERF_ROOT = "/Game/EAP/Perf"


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


def _env(name, default=None):
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return "ERR: %r" % (exc,) if default is None else default


def _dump(path, payload):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    _log("性能采样已写入 " + path)


def _import_one(glb, dest):
    """Interchange 导入单个 glb，返回 (耗时秒, 导入的 StaticMesh 路径列表, 全部导入路径)。

    计时只包住 import_asset_tasks 这一次调用（含 save=True 落盘）。
    """
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", glb)
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", True)
    t0 = time.perf_counter()
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    dt = time.perf_counter() - t0
    imported = _safe(lambda: [str(p) for p in task.get_editor_property("imported_object_paths")], [])
    meshes = []
    for p in imported:
        obj = _safe(lambda p=p: unreal.EditorAssetLibrary.load_asset(p), None)
        if obj is not None and str(_safe(lambda o=obj: o.get_class().get_name(), "")) == "StaticMesh":
            meshes.append(p)
    return dt, meshes, imported


def _glb_alpha_modes(glb_path):
    """按 glTF material 顺序读 alphaMode（OPAQUE/BLEND/MASK），给 `_normalize_materials` 判叶子槽。

    与真实 manifest 的 `material.materials[].alphaMode` 同源；不传的话 Interchange 的
    材质默认 OPAQUE，叶子槽会被误判成 opaque，B 段就少跑一条 FOLIAGE 母材质分支。
    """
    with open(glb_path, "rb") as fh:
        blob = fh.read()
    magic, _ver, total = struct.unpack_from("<III", blob, 0)
    if magic != 0x46546C67:
        return []
    off, gltf = 12, None
    while off < total:
        ln, ty = struct.unpack_from("<II", blob, off)
        off += 8
        if ty == 0x4E4F534A:
            gltf = json.loads(blob[off:off + ln].decode("utf-8"))
        off += ln
    return [{"alphaMode": str(m.get("alphaMode") or "OPAQUE").upper()}
            for m in ((gltf or {}).get("materials") or [])]


def _normalize_one(mesh_path, tag, glb_path):
    """对一个已导入的网格跑真实的 `_normalize_materials` 路径，返回 (耗时秒, 槽数, MIC 列表)。

    计时只包住 `_normalize_materials` 的函数体（母材质预热在本样本计时前完成）。
    """
    mesh = unreal.EditorAssetLibrary.load_asset(mesh_path)
    if mesh is None:
        raise RuntimeError("加载不到网格: " + mesh_path)
    modes = _glb_alpha_modes(glb_path)
    t0 = time.perf_counter()
    info = pm._normalize_materials(mesh, tag, expected_alpha_modes=modes, glb_path=glb_path)
    dt = time.perf_counter() - t0
    return dt, len(info.get("evidence") or []), [e.get("material") for e in (info.get("evidence") or [])]


def _cleanup(tag_prefix):
    """删掉本样本导入的资产 / MIC / roughness 贴图，避免工程无限膨胀（不计时）。

    只删 `<tag前缀>` 命名的东西：/Game/EAP/Perf/<tag> 整目录、MAT_DIR 下 MI_EAP_<tag>_*、
    TEX_DIR 下 <tag>_*_Roughness。
    """
    alb = unreal.EditorAssetLibrary
    root = PERF_ROOT + "/" + tag_prefix
    if bool(_safe(lambda: alb.does_directory_exist(root), False)):
        _safe(lambda: alb.delete_directory(root))
    mi_prefix = pm.MAT_DIR + "/MI_EAP_" + tag_prefix + "_"
    for p in _safe(lambda: list(alb.list_assets(pm.MAT_DIR, recursive=False, include_folder=False)), []) or []:
        p = str(p).split(".")[0]
        if p.startswith(mi_prefix):
            _safe(lambda p=p: alb.delete_asset(p))
    tex_prefix = tag_prefix + "_"
    for p in _safe(lambda: list(alb.list_assets(pm.TEX_DIR, recursive=False, include_folder=False)), []) or []:
        p = str(p).split(".")[0]
        name = p.rsplit("/", 1)[-1]
        if name.startswith(tex_prefix) and name.endswith("_Roughness"):
            _safe(lambda p=p: alb.delete_asset(p))
    _log("已清理本样本资产 tag=%s" % tag_prefix)


def _restore_parent_materials(manifest_path):
    """把被性能样本改写的母材质默认贴图恢复成真实资产的贴图（样本临时资产已删）。

    `_normalize_materials` 首次会原地重建母材质，并把 BaseColor/Normal 默认贴图指向
    当前样本的 Interchange 贴图；样本结束清理临时资产后，母材质默认贴图会悬空
    （日志 `LoadErrors: 依赖包 ... 不可用`）。这里按 manifest 的真实资产再跑一遍
    `_normalize_materials`（不计时），把母材质默认贴图与真实 MIC 指回真实贴图。
    """
    with open(manifest_path, "r", encoding="utf-8-sig") as fh:
        manifest = json.load(fh)
    assets = manifest.get("assets") or []
    alb = unreal.EditorAssetLibrary
    restored = 0
    for asset in assets:
        model = asset.get("model") or {}
        if str(model.get("source", "file")) != "file":
            continue
        mesh_path = ((asset.get("engineTargets") or {}).get("UE5") or {}).get("meshPath")
        mesh = _safe(lambda p=mesh_path: alb.load_asset(p), None) if mesh_path else None
        if mesh is None:
            _warn("恢复母材质：加载不到真实网格 %r" % (mesh_path,))
            continue
        if len(assets) > 1:
            tag = "".join(ch for ch in str(asset.get("id") or "asset") if ch.isalnum() or ch == "_")
        else:
            tag = os.path.splitext(os.path.basename(str(model.get("path") or "asset")))[0]
            tag = "".join(ch for ch in tag if ch.isalnum() or ch == "_")
        glb_path = pm._glb_path_of(asset, manifest_path)
        pm._normalize_materials(mesh, tag,
                                expected_alpha_modes=(asset.get("material") or {}).get("materials"),
                                glb_path=glb_path)
        restored += 1
    _log("母材质默认贴图已恢复（真实资产 %d 个）" % restored)


def run():
    out = _env("EAP_PERF_OUT")
    glb_dir = _env("EAP_PERF_GLB_DIR")
    tag = _env("EAP_PERF_TAG")
    round_no = int(_env("EAP_PERF_ROUND", "1"))
    cold_warm = _env("EAP_PERF_COLD_WARM", "hot")
    segments = [s.strip() for s in _env("EAP_PERF_SEGMENTS", "import,material").split(",") if s.strip()]
    copies = int(_env("EAP_PERF_COPIES", "20"))
    if not out or not glb_dir or not tag:
        raise RuntimeError("缺环境变量 EAP_PERF_OUT / EAP_PERF_GLB_DIR / EAP_PERF_TAG")

    glbs = sorted(os.path.join(glb_dir, f) for f in os.listdir(glb_dir) if f.lower().endswith(".glb"))
    if len(glbs) < copies:
        raise RuntimeError("同源副本不足：%d < %d（%s）" % (len(glbs), copies, glb_dir))
    glbs = glbs[:copies]

    rows = []
    meshes_for_b = []
    try:
        if "import" in segments:
            per_asset = []
            detail = []
            for i, glb in enumerate(glbs):
                dest = "%s/%s/A/imp_%02d" % (PERF_ROOT, tag, i)
                dt, meshes, imported = _import_one(glb, dest)
                per_asset.append(dt)
                detail.append({"i": i, "glb": os.path.basename(glb), "dest": dest,
                               "duration_s": round(dt, 6), "meshes": meshes,
                               "imported": len(imported)})
                if not meshes:
                    _warn("第 %d 个副本没有导入出 StaticMesh（imported=%d）" % (i, len(imported)))
                meshes_for_b.append((meshes[0] if meshes else None, glb))
                _log("A 导入 %d/%d %.3fs → %s" % (i + 1, copies, dt, meshes[0] if meshes else "<none>"))
            rows.append({
                "segment": "A_import", "duration_s": round(sum(per_asset), 6),
                "assets": copies, "per_asset_s": [round(v, 6) for v in per_asset],
                "detail": detail,
            })

        if "material" in segments:
            usable = [(m, g) for m, g in meshes_for_b if m]
            if not usable:
                raise RuntimeError("没有可规范化的网格（A 段没导入出 StaticMesh）")
            # 母材质预热：真实路径会"首次建/重建母材质"，这属于一次性的工程准备，
            # 不计入任何一次 B 采样；预热后 `_REBUILT_MATS` 命中，20 个资产都走"复用"。
            warm_tag = tag + "_warm"
            tw = time.perf_counter()
            pm._normalize_materials(unreal.EditorAssetLibrary.load_asset(usable[0][0]), warm_tag,
                                    expected_alpha_modes=_glb_alpha_modes(usable[0][1]),
                                    glb_path=usable[0][1])
            warm_dt = time.perf_counter() - tw
            _log("B 母材质预热（不计入采样）%.2fs" % warm_dt)

            per_asset = []
            detail = []
            for i, (mesh_path, glb) in enumerate(usable):
                mat_tag = "%s_s%02d" % (tag, i)
                dt, slots, mis = _normalize_one(mesh_path, mat_tag, glb)
                per_asset.append(dt)
                detail.append({"i": i, "mesh": mesh_path, "duration_s": round(dt, 6),
                               "slots": slots, "materials": mis})
                _log("B 材质 %d/%d %.3fs slots=%d" % (i + 1, len(usable), dt, slots))
            rows.append({
                "segment": "B_material", "duration_s": round(sum(per_asset), 6),
                "assets": len(usable), "per_asset_s": [round(v, 6) for v in per_asset],
                "warmup_s": round(warm_dt, 6), "detail": detail,
            })
    finally:
        _cleanup(tag)
        _cleanup(tag + "_warm")
        manifest = _env("EAP_PERF_MANIFEST")
        if manifest and os.path.isfile(manifest):
            try:
                _restore_parent_materials(manifest)
            except Exception as exc:  # noqa: BLE001
                _warn("恢复母材质默认贴图失败: %r" % (exc,))

    payload = {
        "schema": "eap.perf.ue/1",
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tag": tag,
        "round": round_no,
        "cold_warm": cold_warm,
        "segments": segments,
        "copies": copies,
        "glb_source": os.path.join(glb_dir, glbs[0]) if glbs else None,
        "samples": rows,
        "notes": {
            "copiesDefinition": "同一份 conifer.glb 的 20 个字节级相同副本，文件名不同，"
                                "导入目标路径每样本唯一；DDC 键含源文件名，20 个副本不是同一个 DDC 键。",
            "importBoundary": "t0/t1 只包 import_asset_tasks（含 Interchange 翻译与 save=True 落盘）",
            "materialBoundary": "t0/t1 只包 _normalize_materials（母材质已预热；含 MIC/贴图参数/"
                                "压缩设置/roughness 贴图生成与落盘）",
        },
    }
    _dump(out, payload)
    _log("性能样本完成 cold_warm=%s round=%d segments=%s: %s"
         % (cold_warm, round_no, segments,
            ", ".join("%s=%.3fs" % (r["segment"], r["duration_s"]) for r in rows)))
    return payload


if __name__ == "__main__":
    run()
