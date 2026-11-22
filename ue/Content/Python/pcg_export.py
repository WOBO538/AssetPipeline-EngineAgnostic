"""EAP · Layer 1 数据出口：UE PCG → 结构化散点 JSON（只读）

在 UE 编辑器内或 `UnrealEditor-Cmd.exe -ExecutePythonScript=本文件` 下运行：

    import sys; sys.path.append(r"<repo>/ue/Content/Python")
    import pcg_export
    pcg_export.run()                     # 默认 /Game/Untitled + /Game/PCG_ForestScatter
    pcg_export.run(map_path="/Game/Untitled", graph_path="/Game/PCG_ForestScatter",
                   out_path=r"<repo>/docs/pcg_forest_pilot.json")

产出契约 `eap.pcg_scatter/1`：
  graph   —— 图里每个节点的类与参数（可复现性证据：密度/边界/随机范围/种子）
  scatter —— 生成出来的实例：网格、实例数、包围盒、每条的（平移/欧拉角/缩放）
  output  —— 生成输出探针：PCGDataCollection 是否可读、tagged 数据条数

硬约束：
- **只读**：只 load + 读取，不 save、不改资产、不触发重新生成。
- **单位**：平移是 UE 世界单位（uu = 厘米），JSON 里显式写 `translationUnit: "cm"`；
  米制换算在中台 `tools/pcg_to_manifest.py` 里做，导出端不猜。
- 读不到的东西写 `null` + `warning`，不编数字。
"""
import json
import os
import time

import unreal

SCHEMA = "eap.pcg_scatter/1"
DEFAULT_MAP = "/Game/Untitled"
DEFAULT_GRAPH = "/Game/PCG_ForestScatter"
DEFAULT_OUT = r"D:\Projects\AssetPipeline-EngineAgnostic\docs\pcg_forest_pilot.json"

_SKIP_PREFIX = ("get_", "set_", "is_", "remove_", "add_", "acquire_", "rename", "call_", "cast",
                "static_", "modify", "reset_", "export_", "import_", "copy", "assign", "to_")
# 基类噪声字段：与"这个图怎么生成"无关，写进证据只会淹没关键参数
_NOISY = {
    "enabled", "debug", "break_debugger", "execution_dependency_required", "expose_to_library",
    "force_object_and_struct_extraction", "force_op_to_double", "force_rounding_op_to_int",
    "sanitize_output_attribute_name", "silence_override_attribute_not_found_errors",
    "silence_reserved_attribute_name_warnings", "silence_sanitized_attribute_name_warnings",
    "use_legacy_grid_creation_method", "warn_on_identical_spawn",
}


def _log(msg):
    unreal.log("[EAP] " + str(msg))


def _warn(msg):
    unreal.log_warning("[EAP] " + str(msg))


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - 探针：把不可用 API 记下来，不中断
        return "ERR: %r" % (exc,) if default is None else default


def _pub(obj):
    return _safe(lambda: [a for a in dir(obj) if isinstance(a, str) and not a.startswith("_")], [])


def _round(v, n=6):
    try:
        return round(float(v), n)
    except Exception:  # noqa: BLE001
        return None


def _vec(v):
    return [_round(v.x), _round(v.y), _round(v.z)]


def _euler(rot):
    """FRotator.euler() 在 5.7 返回 Vector（roll/pitch/yaw 三轴），不是 Rotator。"""
    e = _safe(lambda: rot.euler())
    if e is None or isinstance(e, str):
        return None
    return [_round(getattr(e, "x", None), 3), _round(getattr(e, "y", None), 3), _round(getattr(e, "z", None), 3)]


def _props(obj, allow_complex=True):
    """尽力导出可读的 editor 属性：标量原样，向量/旋转转三元组，其余只留类名。"""
    out = {}
    for name in _pub(obj):
        if name.startswith(_SKIP_PREFIX) or name in _NOISY:
            continue
        try:
            val = obj.get_editor_property(name)
        except Exception:  # noqa: BLE001 - 属性不可读就跳过
            continue
        if isinstance(val, bool):
            out[name] = val
        elif isinstance(val, (int, float)):
            out[name] = _round(val) if isinstance(val, float) else val
        elif isinstance(val, str):
            out[name] = val
        elif allow_complex:
            if hasattr(val, "x") and hasattr(val, "y"):
                out[name] = _vec(val) if hasattr(val, "z") else [_round(val.x), _round(val.y)]
            elif hasattr(val, "get_path_name"):
                out[name] = _safe(lambda v=val: str(v.get_path_name()))
    return out


def _graph_report(graph_path):
    graph = _safe(lambda: unreal.EditorAssetLibrary.load_asset(graph_path))
    if graph is None or isinstance(graph, str):
        return {"path": graph_path, "error": graph}
    nodes = _safe(lambda: list(graph.get_editor_property("nodes")), [])
    rep = {
        "path": graph_path,
        "assetClass": _safe(lambda: str(graph.get_class().get_name())),
        "nodesCount": len(nodes) if isinstance(nodes, list) else nodes,
        "nodes": [],
        "userParameterNodes": 0,
    }
    for n in nodes if isinstance(nodes, list) else []:
        settings = _safe(lambda n=n: n.get_editor_property("settings_interface"))
        if settings is None or isinstance(settings, str):
            settings = _safe(lambda n=n: n.get_settings())
        entry = {
            "name": _safe(lambda n=n: str(n.get_name())),
            "settingsClass": _safe(lambda s=settings: str(s.get_class().get_name())) if settings is not None else None,
            "props": _props(settings) if settings is not None and not isinstance(settings, str) else {},
        }
        if "UserParameter" in (entry["settingsClass"] or ""):
            rep["userParameterNodes"] += 1
        rep["nodes"].append(entry)
    return rep


def _mesh_info(mesh):
    pkg = _safe(lambda: str(mesh.get_path_name()))
    info = {
        "packagePath": pkg.split(".")[0] if isinstance(pkg, str) else pkg,
        "objectPath": pkg,
        "boundsCm": None,
        "triCount": None,
    }
    box = _safe(lambda: mesh.get_bounding_box())
    if box is not None and not isinstance(box, str):
        mn = _safe(lambda: box.get_editor_property("min"))
        mx = _safe(lambda: box.get_editor_property("max"))
        if mn is not None and mx is not None and not isinstance(mn, str):
            info["boundsCm"] = {"min": _vec(mn), "max": _vec(mx)}
    for meth in ("get_num_triangles", "get_number_of_triangles"):
        fn = getattr(mesh, meth, None)
        if fn is None:
            continue
        val = _safe(lambda fn=fn: int(fn(0)))
        if isinstance(val, int):
            info["triCount"] = val
            break
    return info


def run(map_path=DEFAULT_MAP, graph_path=DEFAULT_GRAPH, out_path=DEFAULT_OUT):
    t0 = time.time()
    warnings = []
    report = {
        "schema": SCHEMA,
        "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "engine": {
            "version": _safe(lambda: str(unreal.SystemLibrary.get_engine_version())),
            "project": _safe(lambda: str(unreal.Paths.get_project_file_path())),
            "map": map_path,
        },
        "translationUnit": "cm",
        "rotationUnit": "deg",
        "graph": _graph_report(graph_path),
        "scatter": {},
        "output": {},
        "warnings": warnings,
    }

    loaded = _safe(lambda: unreal.EditorLoadingAndSavingUtils.load_map(map_path))
    report["engine"]["mapLoad"] = loaded if isinstance(loaded, bool) else str(loaded)

    subsys = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem))
    actors = _safe(lambda: list(subsys.get_all_level_actors()), [])
    if not isinstance(actors, list):
        actors = []
        warnings.append("拿不到关卡 actor 列表")

    scatter = {
        "actor": None,
        "pcgComponents": [],
        "instancedMeshes": [],
        "instanceCount": 0,
        "points": [],
        "pilot": True,
        "note": "2026-10 试点：用 /Engine/BasicShapes/Cube 方盒子代替树，19 个实例；规模小是当时刻意试点，不是缺陷。",
    }

    for a in actors:
        pcs = _safe(lambda a=a: list(a.get_components_by_class(unreal.PCGComponent)), [])
        if isinstance(pcs, list) and pcs:
            scatter["actor"] = _safe(lambda a=a: str(a.get_actor_label()))
            for c in pcs:
                ggo = _safe(lambda c=c: c.get_generated_graph_output())
                tagged = _safe(lambda g=ggo: list(g.get_editor_property("tagged_data")), [])
                scatter["pcgComponents"].append({
                    "name": _safe(lambda c=c: str(c.get_name())),
                    "seed": _safe(lambda c=c: int(c.get_editor_property("seed"))),
                    "generationTrigger": _safe(lambda c=c: str(c.get_editor_property("generation_trigger"))),
                    "generatedGraphOutput": {
                        "class": str(type(ggo)),
                        "readable": not isinstance(ggo, str),
                        "taggedCount": len(tagged) if isinstance(tagged, list) else tagged,
                    },
                })
                report["output"] = {
                    "collectionClass": str(type(ggo)),
                    "taggedCount": len(tagged) if isinstance(tagged, list) else tagged,
                    "note": "PCGDataCollection 可读；点级 metadata 直接访问 API 未暴露（见 docs/manifest-spec.md v1.1 未覆盖项）",
                }

        isms = _safe(lambda a=a: list(a.get_components_by_class(unreal.InstancedStaticMeshComponent)), [])
        for c in isms if isinstance(isms, list) else []:
            mesh = _safe(lambda c=c: c.get_editor_property("static_mesh"))
            count = _safe(lambda c=c: int(c.get_instance_count()))
            entry = {
                "component": _safe(lambda c=c: str(c.get_name())),
                "actor": _safe(lambda a=a: str(a.get_actor_label())),
                "instanceCount": count,
                "mesh": _mesh_info(mesh) if mesh is not None and not isinstance(mesh, str) else None,
                "points": [],
            }
            if isinstance(count, int):
                for i in range(count):
                    xf = _safe(lambda i=i, c=c: c.get_instance_transform(i, True))
                    if xf is None or isinstance(xf, str):
                        continue
                    row = {
                        "t": _vec(_safe(lambda xf=xf: xf.translation)),
                        "r": _euler(_safe(lambda xf=xf: xf.rotation)),
                        "s": _vec(_safe(lambda xf=xf: xf.scale3d)),
                    }
                    if None in row["t"] or None in row["s"] or row["r"] is None:
                        warnings.append("%s 第 %d 个实例 transform 读到 None" % (entry["component"], i))
                        continue
                    entry["points"].append(row)
            if entry["points"]:
                xs = [p["t"][0] for p in entry["points"]]
                ys = [p["t"][1] for p in entry["points"]]
                zs = [p["t"][2] for p in entry["points"]]
                entry["boundsCm"] = {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}
            scatter["instancedMeshes"].append(entry)

    for e in scatter["instancedMeshes"]:
        scatter["points"].extend(e["points"])
        if isinstance(e["instanceCount"], int):
            scatter["instanceCount"] += e["instanceCount"]
    if len(scatter["points"]) != scatter["instanceCount"]:
        warnings.append("points 条数 %d != instanceCount %d（有实例读不出 transform）"
                        % (len(scatter["points"]), scatter["instanceCount"]))
    report["scatter"] = scatter

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    _log("graph=%s nodes=%s userParamNodes=%s" % (report["graph"].get("assetClass"),
                                                 report["graph"].get("nodesCount"),
                                                 report["graph"].get("userParameterNodes")))
    for n in report["graph"].get("nodes", []):
        _log("  node %-28s %s" % (n["name"], n["settingsClass"]))
    _log("scatter actor=%s instanceCount=%s points=%s meshes=%s" % (
        scatter["actor"], scatter["instanceCount"], len(scatter["points"]),
        len(scatter["instancedMeshes"])))
    for e in scatter["instancedMeshes"]:
        _log("  ism %s mesh=%s count=%s bounds=%s" % (e["component"], (e.get("mesh") or {}).get("packagePath"),
                                                      e["instanceCount"], e.get("boundsCm")))
    if scatter["points"]:
        _log("  first point t=%s r=%s s=%s" % (scatter["points"][0]["t"], scatter["points"][0]["r"],
                                               scatter["points"][0]["s"]))
    _log("warnings=%d -> %s (%.2fs)" % (len(warnings), out_path, time.time() - t0))
    return report


def measure(map_path=DEFAULT_MAP):
    """只读量尺：PCG 体积 / 地形范围 / 实例总数 —— 决定"规模化"要调多少密度。

    不生成、不保存。返回 dict；也打 [EAP] 日志。
    """
    unreal.EditorLoadingAndSavingUtils.load_map(map_path)
    subsys = _safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem))
    actors = _safe(lambda: list(subsys.get_all_level_actors()), [])
    rep = {"map": map_path, "volumes": [], "landscape": {}, "instances": 0, "actorCount": 0}
    if not isinstance(actors, list):
        return rep
    rep["actorCount"] = len(actors)
    for a in actors:
        cls = _safe(lambda a=a: str(a.get_class().get_name()))
        if cls not in ("PCGVolume", "Landscape", "LandscapeStreamingProxy"):
            continue
        o, e = _safe(lambda a=a: a.get_actor_bounds(False), (None, None))
        if o is None or e is None or isinstance(o, str) or isinstance(e, str):
            continue
        entry = {
            "actor": _safe(lambda a=a: str(a.get_actor_label())),
            "class": cls,
            "originCm": _vec(o),
            "sizeCm": [_round(2 * e.x), _round(2 * e.y), _round(2 * e.z)],
        }
        if cls == "PCGVolume":
            rep["volumes"].append(entry)
        else:
            ls = rep["landscape"]
            if not ls:
                ls.update(entry)
            else:
                # 累加地形总范围（min/max）
                mn, mx = ls.setdefault("minCm", [1e18] * 3), ls.setdefault("maxCm", [-1e18] * 3)
                c, s = entry["originCm"], entry["sizeCm"]
                for i in range(3):
                    mn[i] = min(mn[i], c[i] - s[i] / 2)
                    mx[i] = max(mx[i], c[i] + s[i] / 2)
                ls["tiles"] = ls.get("tiles", 1) + 1

        isms = _safe(lambda a=a: list(a.get_components_by_class(unreal.InstancedStaticMeshComponent)), [])
        for c in isms if isinstance(isms, list) else []:
            n = _safe(lambda c=c: int(c.get_instance_count()))
            if isinstance(n, int):
                rep["instances"] += n

    for v in rep["volumes"]:
        _log("volume %s origin=%s size=%s (m: %s)" % (
            v["actor"], v["originCm"], v["sizeCm"],
            [round(x * 0.01, 1) for x in v["sizeCm"]]))
    if rep["landscape"]:
        ls = rep["landscape"]
        _log("landscape tiles=%s minCm=%s maxCm=%s" % (ls.get("tiles"), ls.get("minCm"), ls.get("maxCm")))
    _log("instances=%d actorCount=%d" % (rep["instances"], rep["actorCount"]))
    return rep


if __name__ == "__main__":
    # -ExecutePythonScript 以 __main__ 执行本文件；输出路径可用环境变量 EAP_PCG_OUT 覆盖
    run(out_path=os.environ.get("EAP_PCG_OUT", DEFAULT_OUT))
