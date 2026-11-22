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

安全策略：先备份？本脚本只动 Graph 资产的 3 个参数 + 关卡里的生成结果；**不删节点、不新建资产**。
回退办法：`git -C <repo> ...` 不适用（UE 工程不在 git），所以在 `Saved/EAP/backup/` 里留一份
`PCG_ForestScatter.uasset` 副本即可（脚本 save 前自动拷一份，见 `_backup_graph`）。
"""
import os
import shutil
import time

import unreal

GRAPH_PATH = "/Game/PCG_ForestScatter"
MAP_PATH = "/Game/Untitled"
BACKUP_DIR = r"D:\UE_Projects\VegetationPCG\Saved\EAP\backup"


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return "ERR: %r" % (exc,) if default is None else default


def _settings_of(graph, class_name):
    for n in _safe(lambda: list(graph.get_editor_property("nodes")), []):
        s = _safe(lambda n=n: n.get_settings())
        if s is not None and not isinstance(s, str) and _safe(lambda s=s: str(s.get_class().get_name())) == class_name:
            return s
    return None


def _volume_area_m2():
    """只读量尺：PCGVolume 的 XY 面积（m²）。"""
    subsys = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem))
    for a in _safe(lambda: list(subsys.get_all_level_actors()), []):
        if _safe(lambda a=a: str(a.get_class().get_name())) != "PCGVolume":
            continue
        o, e = _safe(lambda a=a: a.get_actor_bounds(False), (None, None))
        if o is None or e is None or isinstance(e, str):
            continue
        return round((2 * e.x) * (2 * e.y) * 0.0001, 1), [round(2 * e.x * 0.01, 1), round(2 * e.y * 0.01, 1)]
    return None, None


def _count_instances():
    subsys = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem))
    total = 0
    for a in _safe(lambda: list(subsys.get_all_level_actors()), []):
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
    subsys = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem))
    for a in _safe(lambda: list(subsys.get_all_level_actors()), []):
        pcs = _safe(lambda a=a: list(a.get_components_by_class(unreal.PCGComponent)), [])
        if isinstance(pcs, list) and pcs:
            return a, pcs[0]
    return None, None


def run(density=0.1, scale_min=None, scale_max=None, seed=None, save=False, regenerate=True, dry_run=True):
    dry_run = dry_run if save else True
    area, size_m = _volume_area_m2()
    before = _count_instances()
    _log("体积 XY = %s m（面积 %s m²）；当前实例 %d 个" % (size_m, area, before))
    if isinstance(area, (int, float)):
        _log("密度 %.3f 点/m² → 名义 %.0f 个点（网格量化后一般 7 成左右）" % (density, area * density))

    graph = _safe(lambda: unreal.EditorAssetLibrary.load_asset(GRAPH_PATH))
    if graph is None or isinstance(graph, str):
        raise RuntimeError("加载不到图: %s (%r)" % (GRAPH_PATH, graph))

    sampler = _settings_of(graph, "PCGSurfaceSamplerSettings")
    transform = _settings_of(graph, "PCGTransformPointsSettings")
    if sampler is None:
        raise RuntimeError("图里没找到 PCGSurfaceSamplerSettings")
    _log("sampler.points_per_squared_meter: %r → %r"
         % (_safe(lambda: sampler.get_editor_property("points_per_squared_meter")), density))
    if transform is not None and (scale_min is not None or scale_max is not None):
        _log("transform.scale_min/max: %r/%r → %r/%r" % (
            _safe(lambda: transform.get_editor_property("scale_min")),
            _safe(lambda: transform.get_editor_property("scale_max")), scale_min, scale_max))
    if transform is not None and seed is not None:
        _log("transform.seed: %r → %r" % (_safe(lambda: transform.get_editor_property("seed")), seed))

    if not save:
        _log("空跑结束（要真改请传 save=True, regenerate=True）")
        return {"dry_run": True, "estimated": area * density if isinstance(area, (int, float)) else None,
                "instances_before": before}

    _backup_graph()
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
    if comp is None:
        _warn("关卡里没找到 PCGComponent，跳过了重生成")
    elif regenerate:
        ok = False
        for call in ("generate(True)", "generate(False)"):
            try:
                _safe(lambda call=call: eval("comp." + call))
                ok = True
                _log("重生成: comp.%s 已调用" % call)
                break
            except Exception as exc:  # noqa: BLE001
                _warn("comp.%s 失败: %r" % (call, exc))
        if not ok:
            _warn("自动重生成失败 → 请手动选中 PCG_ForestScatter 卷，Details 面板点 Generate")
        time.sleep(2)

    after = _count_instances()
    _log("实例数: %d → %d" % (before, after))

    if after != before:
        saved = _safe(lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level())
        _log("关卡已保存: %r" % (saved,))
    else:
        _warn("实例数没变 → 可能生成没生效；确认 Details 面板点过 Generate 后再存关卡")
    return {"dry_run": False, "instances_before": before, "instances_after": after}
