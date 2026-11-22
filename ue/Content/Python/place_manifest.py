"""EAP · placements 落位（回灌）：manifest v1.1 的 placements[*] → UE ISM 实例

这是把 Layer 1 从"单向导出"变成"可回灌"的那一格：
manifest 里 1 个资产 + N 条 `placements.items`（世界坐标、米、度），本脚本在 UE 里
建 1 个 Actor + 1 个 InstancedStaticMeshComponent，把 N 条落成 ISM 实例，然后存关卡。

用法（无头也能跑，不需要 -nullrhi 之外的东西）：

    # 落位（建/清 Actor + ISM，落 233 条，存关卡）
    python -c "..."   # 或 UE 里 -ExecutePythonScript=place_manifest.py
    # 验证（新进程重新加载关卡，读回实例数/transform，写 verify 证据）
    set EAP_PLACE_MODE=verify

环境变量：
- EAP_PLACE_MODE   place（默认）| verify
- EAP_MANIFEST     manifest 路径，默认仓库 schemas/pcg_manifest.json
- EAP_LEVEL_PKG    目标关卡，默认 /Game/EAP/PCG/PlacedPilot
- EAP_PLACE_SAVE   0 = 不存关卡（只测不落盘）
- EAP_PLACE_OUT    落位证据输出路径，默认仓库 docs/placed_pilot.json
- EAP_VERIFY_OUT   验证证据输出路径，默认仓库 docs/placed_verify.json

**只动一个专用关卡**（默认 PlacedPilot），不碰用户的 `/Game/Untitled` 试点现场。
幂等：每次先 `clear_instances()` 再重落，跑 10 次仍是 N 条。
"""
import json
import os
import time

import unreal

CM_PER_M = 100.0
DEFAULT_MANIFEST = r"D:\Projects\AssetPipeline-EngineAgnostic\schemas\pcg_manifest.json"
DEFAULT_LEVEL = "/Game/EAP/PCG/PlacedPilot"
DEFAULT_OUT = r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_pilot.json"
DEFAULT_VERIFY_OUT = r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_verify.json"
ACTOR_LABEL = "EAP_Placements"


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


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
    _log("证据已写入 " + path)


def _read(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _mesh_asset_path(model_path):
    """manifest 里引擎网格写 `ue:/Engine/BasicShapes/Cube`，去掉 `ue:` 前缀。"""
    p = str(model_path or "")
    if p.startswith("ue:"):
        p = p[3:]
    return p


def _ensure_level(level_pkg):
    """关卡存在就加载；不存在就新建空关卡。返回 (created, world/path)。"""
    alb = unreal.EditorAssetLibrary
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    exists = bool(_safe(lambda: alb.does_asset_exist(level_pkg), False))
    if exists:
        _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(level_pkg))
        _log("已加载已有落位关卡 " + level_pkg)
        return False, _safe(lambda: str(les.get_current_level().get_path_name()), None)
    d = level_pkg.rsplit("/", 1)[0]
    _safe(lambda: alb.make_directory(d))
    created = bool(_safe(lambda: les.new_level(level_pkg), False))
    _log("新建落位关卡 %s -> %r（注意：会先关掉当前关卡，不保存它的未存改动）" % (level_pkg, created))
    return created, _safe(lambda: str(les.get_current_level().get_path_name()), None)


def _find_actor(label):
    eas = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem), None)
    if eas is None or isinstance(eas, str):
        return None
    for a in _safe(lambda: list(eas.get_all_level_actors()), []):
        if str(_safe(lambda a=a: a.get_actor_label(), "")) == label:
            return a
    return None


def _spawn_actor():
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = eas.spawn_actor_from_class(unreal.Actor, unreal.Vector(0, 0, 0), unreal.Rotator())
    actor.set_actor_label(ACTOR_LABEL)
    _log("已生成落位 Actor: %s" % actor.get_name())
    return actor


def _add_ism(actor):
    """给关卡里的 Actor 实例加一个 InstancedStaticMeshComponent（Details→Add Component 的 Python 路径）。

    注意：`unreal.new_object(ISM, outer=actor)` 能建组件但**不在 OwnedComponents 里**，
    存关卡后组件会被丢掉（2026-11-22 实测）。必须走 SubobjectDataSubsystem.add_new_subobject。
    """
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = list(sds.k2_gather_subobject_data_for_instance(actor))
    if not handles:
        raise RuntimeError("Actor 没有可用的 subobject handle")
    params = unreal.AddNewSubobjectParams()
    params.set_editor_property("parent_handle", handles[0])
    params.set_editor_property("new_class", unreal.InstancedStaticMeshComponent)
    params.set_editor_property("blueprint_context", None)
    out = sds.add_new_subobject(params)
    handle = out[0] if isinstance(out, (tuple, list)) else out
    data = sds.k2_find_subobject_data_from_handle(handle)
    comp = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
    _log("已给 Actor 加 ISM 组件: %s" % _safe(lambda: comp.get_name()))
    return comp


def _ism_of(actor):
    comps = _safe(lambda: list(actor.get_components_by_class(unreal.InstancedStaticMeshComponent)), [])
    return comps[0] if comps else None


def _transform_of(item):
    t = item.get("t") or [0, 0, 0]
    r = item.get("r") or [0, 0, 0]
    s = item.get("s") or [1, 1, 1]
    loc = unreal.Vector(float(t[0]) * CM_PER_M, float(t[1]) * CM_PER_M, float(t[2]) * CM_PER_M)
    rot = unreal.Rotator(roll=float(r[0]), pitch=float(r[1]), yaw=float(r[2]))
    scale = unreal.Vector(float(s[0]), float(s[1]), float(s[2]))
    return unreal.Transform(loc, rot, scale)


def _bbox(items_m):
    xs = [p[0] for p in items_m]
    ys = [p[1] for p in items_m]
    zs = [p[2] for p in items_m]
    return {"min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
            "max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)]}


def _instance_items(comp):
    """把 ISM 实例读回成 manifest 同格式（m / deg roll,pitch,yaw / scale）。"""
    n = int(comp.get_instance_count())
    out = []
    for i in range(n):
        xf = comp.get_instance_transform(i, True)
        rot = xf.rotation.rotator()
        out.append({
            "t": [round(xf.translation.x / CM_PER_M, 4),
                  round(xf.translation.y / CM_PER_M, 4),
                  round(xf.translation.z / CM_PER_M, 4)],
            "r": [round(rot.roll, 3), round(rot.pitch, 3), round(rot.yaw, 3)],
            "s": [round(xf.scale3d.x, 4), round(xf.scale3d.y, 4), round(xf.scale3d.z, 4)],
        })
    return out


def place(manifest_path=None, level_pkg=None, save=True, out_path=None):
    manifest_path = manifest_path or os.environ.get("EAP_MANIFEST") or DEFAULT_MANIFEST
    level_pkg = level_pkg or os.environ.get("EAP_LEVEL_PKG") or DEFAULT_LEVEL
    out_path = out_path or os.environ.get("EAP_PLACE_OUT") or DEFAULT_OUT
    if os.environ.get("EAP_PLACE_SAVE") == "0":
        save = False

    manifest = _read(manifest_path)
    assets = manifest.get("assets") or []
    if len(assets) != 1:
        _warn("本试点只处理单资产 manifest，实际 assets=%d（只用第一个）" % len(assets))
    asset = assets[0]
    pl = asset.get("placements") or {}
    items = pl.get("items") or []
    if not items:
        raise RuntimeError("manifest 里没有 placements.items：%s" % manifest_path)

    mesh_path = _mesh_asset_path((asset.get("model") or {}).get("path"))
    mesh = unreal.EditorAssetLibrary.load_asset(mesh_path)
    if mesh is None:
        raise RuntimeError("加载不到网格: " + mesh_path)
    _log("目标关卡=%s 网格=%s 条数=%d" % (level_pkg, mesh_path, len(items)))

    created, level = _ensure_level(level_pkg)
    actor = _find_actor(ACTOR_LABEL)
    if actor is None:
        actor = _spawn_actor()
    comp = _ism_of(actor)
    if comp is None:
        comp = _add_ism(actor)
    comp.set_static_mesh(mesh)
    _safe(lambda: comp.clear_instances())

    transforms = [_transform_of(it) for it in items]
    try:
        comp.add_instances(transforms, True)
    except Exception as exc:  # noqa: BLE001
        _warn("批量 add_instances 失败，逐条 add_instance: %r" % (exc,))
        for xf in transforms:
            comp.add_instance(xf, True)
    placed = int(_safe(lambda: comp.get_instance_count(), -1))
    _log("落位完成: %d / %d" % (placed, len(items)))

    readback = _instance_items(comp)
    manifest_bbox = _bbox([it.get("t") or [0, 0, 0] for it in items])
    placed_bbox = _bbox([it["t"] for it in readback]) if readback else None
    saved = None
    if save:
        saved = _safe(lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
        _log("关卡已保存: %r" % (saved,))

    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.run/1",
        "manifest": manifest_path,
        "levelPackage": level_pkg,
        "level": level,
        "levelCreated": created,
        "actor": _safe(lambda: str(actor.get_actor_label())),
        "component": _safe(lambda: str(comp.get_name())),
        "mesh": mesh_path,
        "expected": len(items),
        "placed": placed,
        "levelSaved": saved,
        "bboxManifestM": manifest_bbox,
        "bboxPlacedM": placed_bbox,
        "items": readback,
    }
    _dump(out_path, state)
    return state


def verify(level_pkg=None, out_path=None):
    level_pkg = level_pkg or os.environ.get("EAP_LEVEL_PKG") or DEFAULT_LEVEL
    out_path = out_path or os.environ.get("EAP_VERIFY_OUT") or DEFAULT_VERIFY_OUT
    loaded = _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(level_pkg))
    _log("加载落位关卡: %r" % (loaded,))
    actor = _find_actor(ACTOR_LABEL)
    comp = _ism_of(actor) if actor is not None else None
    items = _instance_items(comp) if comp is not None else []
    mesh = _safe(lambda: comp.get_editor_property("static_mesh")) if comp is not None else None
    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.verify/1",
        "levelPackage": level_pkg,
        "actor": _safe(lambda: str(actor.get_actor_label())) if actor is not None else None,
        "component": _safe(lambda: str(comp.get_name())) if comp is not None else None,
        "mesh": str(mesh.get_path_name()) if mesh else None,
        "count": len(items),
        "bboxPlacedM": _bbox([it["t"] for it in items]) if items else None,
        "items": items,
    }
    _log("验证读回: count=%d" % len(items))
    _dump(out_path, state)
    return state


def _level_actors():
    eas = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem), None)
    if eas is None or isinstance(eas, str):
        return []
    return _safe(lambda: list(eas.get_all_level_actors()), []) or []


def _find_by_class(cls):
    for a in _level_actors():
        if _safe(lambda a=a: str(a.get_class().get_name()), "") == cls.__name__:
            return a
    return None


def _spawn(cls, label=None):
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    a = eas.spawn_actor_from_class(cls, unreal.Vector(0, 0, 0), unreal.Rotator())
    if label:
        _safe(lambda: a.set_actor_label(label))
    _log("新增 Actor: %s %s" % (cls.__name__, label or ""))
    return a


def stage(level_pkg=None, out_path=None, save=True):
    """给空关卡补灯光/天空/地面，让视口截图能用（不是落位逻辑的一部分）。

    **只能在真实编辑器里跑**：2026-11-22 无头（-unattended -nullrhi）跑这个会
    EXCEPTION_INT_DIVIDE_BY_ZERO 崩溃（SkyAtmosphere/SkyLight 需要渲染管线）。
    真实编辑器里可以用一行 Python 等价实现（或直接跑本函数）。
    """
    level_pkg = level_pkg or os.environ.get("EAP_LEVEL_PKG") or DEFAULT_LEVEL
    out_path = out_path or os.environ.get("EAP_STAGE_OUT") or r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_level_stage.json"
    if os.environ.get("EAP_PLACE_SAVE") == "0":
        save = False
    loaded = _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(level_pkg))
    _log("加载落位关卡: %r" % (loaded,))
    created = []

    if _find_by_class(unreal.DirectionalLight) is None:
        dl = _spawn(unreal.DirectionalLight, "EAP_Sun")
        _safe(lambda: dl.set_actor_rotation(unreal.Rotator(pitch=-42.0, yaw=35.0, roll=0.0), False))
        created.append("DirectionalLight")
    if _find_by_class(unreal.SkyLight) is None:
        _spawn(unreal.SkyLight, "EAP_SkyLight")
        created.append("SkyLight")
    if _find_by_class(unreal.SkyAtmosphere) is None:
        _spawn(unreal.SkyAtmosphere, "EAP_SkyAtmosphere")
        created.append("SkyAtmosphere")
    if _find_by_class(unreal.ExponentialHeightFog) is None:
        _spawn(unreal.ExponentialHeightFog, "EAP_Fog")
        created.append("ExponentialHeightFog")

    ground = None
    for a in _level_actors():
        if _safe(lambda a=a: str(a.get_actor_label()), "") == "EAP_Ground":
            ground = a
            break
    actor = _find_actor(ACTOR_LABEL)
    comp = _ism_of(actor) if actor is not None else None
    items = _instance_items(comp) if comp is not None else []
    if ground is None and items:
        bx = _bbox([it["t"] for it in items])
        cx = (bx["min"][0] + bx["max"][0]) / 2.0 * CM_PER_M
        cy = (bx["min"][1] + bx["max"][1]) / 2.0 * CM_PER_M
        ex = (bx["max"][0] - bx["min"][0]) / 2.0 + 25.0
        ey = (bx["max"][1] - bx["min"][1]) / 2.0 + 25.0
        ground = _spawn(unreal.StaticMeshActor, "EAP_Ground")
        plane = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Plane")
        _safe(lambda: ground.static_mesh_component.set_static_mesh(plane))
        ground.set_actor_scale3d(unreal.Vector(ex, ey, 1.0))
        ground.set_actor_location(unreal.Vector(cx, cy, -50.0), False)
        created.append("EAP_Ground")

    saved = None
    if save:
        saved = _safe(lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
        _log("关卡已保存: %r" % (saved,))
    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema": "eap.placement.stage/1",
        "levelPackage": level_pkg,
        "created": created,
        "levelSaved": saved,
        "actorCount": len(_level_actors()),
    }
    _dump(out_path, state)
    return state


def main():
    mode = (os.environ.get("EAP_PLACE_MODE") or "place").lower()
    if mode == "verify":
        return verify()
    if mode == "stage":
        return stage()
    return place()


if __name__ == "__main__":
    main()
