"""EAP · Layer1 规模化：改 PCG 图参数（密度/缩放范围）并重生成、保存

**必须在真实编辑器里跑**（无头 `-unattended -nullrhi` 下 `PCGComponent.generate()` 会报
`Internal Error - ObjectInstance is null!`，已实测）。

在 UE 编辑器打开 `D:\\UE_Projects\\VegetationPCG` → Output Log 切 Python：

    import sys; sys.path.append(r"D:\\Projects\\AssetPipeline-EngineAgnostic\\ue\\Content\\Python")
    import pcg_scale
    pcg_scale.run(density=0.1)                                  # 空跑：只打印将改什么 + 预计数量
    pcg_scale.run(density=0.1, save=True, regenerate=True)      # 真跑：改图参数 → 存档 → 重生成 → 存关卡

参数含义：
- density：Surface Sampler 的 points_per_squared_meter（点/m²）。体积 50×50 m = 2500 m²，
  0.01 → 名义 25 个点（当前 19）；0.1 → 名义 250 个点；0.2 → 名义 500 个点。
- scale_min/scale_max：Transform Points 的缩放范围（现在 0.8~1.2，放大范围视觉更"林"）。
- seed：Transform Points 的随机种子；换种子 = 同一密度下换一套摆位（可复现性演示用）。

安全策略：只动 Graph 资产的参数 + 关卡里的生成结果；**不删节点、不新建资产**。
回退办法：UE 工程不在 git，所以 save 前把 `Content/PCG_ForestScatter.uasset` 自动拷到
`Saved/EAP/backup/PCG_ForestScatter_<时间戳>.uasset`（见 `_backup_graph`）。

v2（2026-11-22）修正：首跑发现编辑器里 `GetAllLevelActors()` 为空 → 体积/实例读到 None/0、
找不到 PCGComponent。原因：编辑器当前关卡不是 `/Game/Untitled`。现在 `_ensure_map()` 会先对比
当前关卡路径、不一致就显式 load_map，再扫 actor；生成结果轮询最多 10 秒；每次运行把状态写到
`Saved/EAP/pcg_scale_run.json` 供外部核对。
"""
import json
import os
import shutil
import time

import unreal

GRAPH_PATH = "/Game/PCG_ForestScatter"
MAP_PATH = "/Game/Untitled"
BACKUP_DIR = r"D:\UE_Projects\VegetationPCG\Saved\EAP\backup"
STATE_PATH = r"D:\UE_Projects\VegetationPCG\Saved\EAP\pcg_scale_run.json"


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return "ERR: %r" % (exc,) if default is None else default


def _norm_map(p):
    return str(p or "").split(".")[0].replace("\\", "/").lower()


def _current_level_path():
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    return str(les.get_current_level().get_path_name())


def _ensure_map(map_path=MAP_PATH):
    """保证编辑器里打开的是目标关卡，否则 actor 列表会是空的（首跑踩过）。"""
    cur = _safe(_current_level_path, None)
    _log("当前关卡: %r（目标 %r）" % (cur, map_path))
    if isinstance(cur, str) and _norm_map(cur) == _norm_map(map_path):
        return cur
    loaded = _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(map_path))
    _log("已请求加载关卡 %r → %r" % (map_path, loaded))
    time.sleep(1.0)
    cur = _safe(_current_level_path, None)
    _log("加载后当前关卡: %r" % (cur,))
    return cur


def _all_actors():
    subsys = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem), None)
    if subsys is None or isinstance(subsys, str):
        return []
    actors = _safe(lambda: list(subsys.get_all_level_actors()), [])
    return actors if isinstance(actors, list) else []


def _settings_of(graph, class_name):
    for n in _safe(lambda: list(graph.get_editor_property("nodes")), []):
        s = _safe(lambda n=n: n.get_settings())
        if s is not None and not isinstance(s, str) and _safe(lambda s=s: str(s.get_class().get_name())) == class_name:
            return s
    return None


def _volume_area_m2():
    """只读量尺：PCGVolume 的 XY 面积（m²）。"""
    for a in _all_actors():
        if _safe(lambda a=a: str(a.get_class().get_name())) != "PCGVolume":
            continue
        o, e = _safe(lambda a=a: a.get_actor_bounds(False), (None, None))
        if o is None or e is None or isinstance(e, str):
            continue
        if not all(isinstance(v, float) for v in (e.x, e.y, e.z)):
            continue
        return round((2 * e.x) * (2 * e.y) * 0.0001, 1), [round(2 * e.x * 0.01, 1), round(2 * e.y * 0.01, 1)]
    return None, None


def _count_instances():
    total = 0
    for a in _all_actors():
        for c in _safe(lambda a=a: list(a.get_components_by_class(unreal.InstancedStaticMeshComponent)), []):
            n = _safe(lambda c=c: int(c.get_instance_count()))
            if isinstance(n, int):
                total += n
    return total


def _backup_graph():
    """把 .uasset 源文件拷一份（资产已在内存里，磁盘上的是旧版）。"""
    src = r"D:\UE_Projects\VegetationPCG\Content\PCG_ForestScatter.uasset"
    if not os.path.isfile(src):
        _warn("找不到源文件，跳过备份: " + src)
        return None
    if not os.path.isdir(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    dst = os.path.join(BACKUP_DIR, "PCG_ForestScatter_%s.uasset" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(src, dst)
    _log("已备份图资产 → " + dst)
    return dst


def _pcg_component():
    """返回 (actor, component)；找不到就返回 (None, None)。"""
    for a in _all_actors():
        pcs = _safe(lambda a=a: list(a.get_components_by_class(unreal.PCGComponent)), [])
        if isinstance(pcs, list) and pcs:
            return a, pcs[0]
    return None, None


def _write_state(payload):
    try:
        if not os.path.isdir(os.path.dirname(STATE_PATH)):
            os.makedirs(os.path.dirname(STATE_PATH))
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        _log("状态已写入 " + STATE_PATH)
    except Exception as exc:  # noqa: BLE001
        _warn("写状态失败: %r" % (exc,))


def run(density=0.1, scale_min=None, scale_max=None, seed=None, save=False, regenerate=True,
        dry_run=True, map_path=MAP_PATH):
    dry_run = dry_run if save else True
    level = _ensure_map(map_path)
    actors = _all_actors()
    area, size_m = _volume_area_m2()
    before = _count_instances()
    _log("关卡 actor 数 = %d；体积 XY = %s m（面积 %s m²）；当前实例 %d 个"
         % (len(actors), size_m, area, before))
    if isinstance(area, (int, float)):
        _log("密度 %.3f 点/m² → 名义 %.0f 个点（网格量化后一般 7 成左右）" % (density, area * density))
    else:
        _warn("没找到 PCGVolume（关卡路径 = %r）。请确认工程里 /Game/Untitled 已打开；"
              "若体积确实是空的，先手动把 PCG 卷摆好再跑本脚本。" % (level,))

    graph = _safe(lambda: unreal.EditorAssetLibrary.load_asset(GRAPH_PATH))
    if graph is None or isinstance(graph, str):
        raise RuntimeError("加载不到图: %s (%r)" % (GRAPH_PATH, graph))

    sampler = _settings_of(graph, "PCGSurfaceSamplerSettings")
    transform = _settings_of(graph, "PCGTransformPointsSettings")
    if sampler is None:
        raise RuntimeError("图里没找到 PCGSurfaceSamplerSettings")
    old_density = _safe(lambda: sampler.get_editor_property("points_per_squared_meter"))
    _log("sampler.points_per_squared_meter: %r → %r" % (old_density, density))
    if transform is not None and (scale_min is not None or scale_max is not None):
        _log("transform.scale_min/max: %r/%r → %r/%r" % (
            _safe(lambda: transform.get_editor_property("scale_min")),
            _safe(lambda: transform.get_editor_property("scale_max")), scale_min, scale_max))
    if transform is not None and seed is not None:
        _log("transform.seed: %r → %r" % (_safe(lambda: transform.get_editor_property("seed")), seed))

    state = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "level": level, "actorCount": len(actors),
        "volumeSizeM": size_m, "areaM2": area,
        "densityRequested": density, "densityBefore": old_density,
        "instancesBefore": before,
    }

    if not save:
        _log("空跑结束（要真改请传 save=True, regenerate=True）")
        state.update({"dryRun": True, "instancesAfter": before})
        _write_state(state)
        return {"dry_run": True, "estimated": area * density if isinstance(area, (int, float)) else None,
                "instances_before": before, "level": level, "actor_count": len(actors)}

    backup = _backup_graph()
    state["backup"] = backup
    sampler.set_editor_property("points_per_squared_meter", float(density))
    if transform is not None and scale_min is not None:
        transform.set_editor_property("scale_min", unreal.Vector(float(scale_min), float(scale_min), float(scale_min)))
    if transform is not None and scale_max is not None:
        transform.set_editor_property("scale_max", unreal.Vector(float(scale_max), float(scale_max), float(scale_max)))
    if transform is not None and seed is not None:
        transform.set_editor_property("seed", int(seed))
    unreal.EditorAssetLibrary.save_loaded_asset(graph)
    _log("图资产已保存")

    actor, comp = _pcg_component()
    state["pcgActor"] = _safe(lambda: str(actor.get_actor_label())) if actor is not None else None
    if comp is None:
        _warn("关卡里没找到 PCGComponent（actor 数 %d）→ 跳过了重生成；"
              "请确认 /Game/Untitled 是当前关卡，或手动选中 PCG 卷点 Details → Generate" % len(actors))
    elif regenerate:
        state["pcgComponent"] = _safe(lambda: str(comp.get_name()))
        try:
            result = comp.generate(True)
            _log("comp.generate(True) 已调用 → %r" % (result,))
            state["generateCall"] = "ok: %r" % (result,)
        except Exception as exc:  # noqa: BLE001
            _warn("comp.generate(True) 失败: %r" % (exc,))
            state["generateCall"] = "ERR: %r" % (exc,)
        after = before
        for _ in range(10):
            time.sleep(1.0)
            after = _count_instances()
            if after != before:
                break
        _log("实例数: %d → %d（轮询 10 秒内）" % (before, after))
        state["instancesAfter"] = after
        if after == before:
            _warn("实例数没变 → 可能生成没生效；请手动选中 PCG_ForestScatter 卷，Details 面板点 Generate 后再存关卡")
    else:
        after = before
        state["instancesAfter"] = after

    saved = None
    if after != before:
        saved = _safe(lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
        _log("关卡已保存: %r" % (saved,))
    else:
        _warn("实例数没变 → 没存关卡；确认 Details 面板点过 Generate 后再存关卡")
    state["levelSaved"] = saved
    _write_state(state)
    return {"dry_run": False, "instances_before": before, "instances_after": after,
            "level": level, "actor_count": len(actors)}
