"""触发 UE 材质编译（材质编译失败时 UE 会退回默认白材质，日志里打 `Failed to compile Material`）。

为什么要单独跑一次：`-nullrhi` 下不会真正编译 shader/材质，只有带 RHI 的进程加载材质才会暴露
"Failed to compile Material for platform PCD3D_SM6" 这类错误（2026-11-22 实测，正是它让我们看到
"树全白"的根因）。判断通过与否由 `run_pcg_chain.py` 读本进程 stdout 决定。

环境变量：EAP_LEVEL_PKG（默认 /Game/EAP/PCG/PlacedTrees）、EAP_COMPILE_OUT（证据 JSON 路径）
"""
import json
import os
import time

import unreal

LEVEL = os.environ.get("EAP_LEVEL_PKG") or "/Game/EAP/PCG/PlacedTrees"
OUT = os.environ.get("EAP_COMPILE_OUT") or r"D:\UE_Projects\VegetationPCG\Saved\EAP\material_compile_check.json"
MAT_DIR = "/Game/EAP/Materials"


def safe(fn, default=None):
    try:
        return fn() if fn else default
    except Exception as exc:  # noqa: BLE001
        return "ERR: %r" % (exc,) if default is None else default


def main():
    t0 = time.time()
    rep = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": LEVEL, "materials": {}}
    rep["loaded"] = safe(lambda: str(unreal.EditorLoadingAndSavingUtils.load_map(LEVEL)))
    mel = unreal.MaterialEditingLibrary
    alb = unreal.EditorAssetLibrary

    # 只复检**关卡里实际用到的材质**（组件覆盖 + 网格默认槽），避免被素材库里没用的旧资产干扰
    used = []
    eas = safe(lambda: unreal.get_editor_subsystem(unreal.EditorActorSubsystem))
    for actor in (safe(lambda: list(eas.get_all_level_actors()), []) or []) if eas else []:
        comps = safe(lambda actor=actor: list(actor.get_components_by_class(unreal.InstancedStaticMeshComponent)), []) or []
        for comp in comps:
            for i in range(int(safe(lambda comp=comp: comp.get_num_materials(), 0) or 0)):
                mi = safe(lambda comp=comp, i=i: comp.get_material(i))
                if mi is not None and not isinstance(mi, str):
                    used.append(mi)
    rep["levelMaterialCount"] = len(used)
    for mi in used:
        name = safe(lambda mi=mi: str(mi.get_path_name()))
        rep["materials"][name] = {
            "class": safe(lambda mi=mi: str(mi.get_class().get_name())),
            "blend": safe(lambda mi=mi: str(mi.get_editor_property("blend_mode"))),
            "recompile": safe(lambda mi=mi: mel.recompile_material(mi)) if safe(
                lambda mi=mi: str(mi.get_class().get_name())) == "Material" else "MIC(继承父材质)",
            "parent": safe(lambda mi=mi: str(mi.get_editor_property("parent").get_path_name()))
            if safe(lambda mi=mi: str(mi.get_class().get_name())) == "MaterialInstanceConstant" else None,
        }
        # MIC 的父材质才是真正要编译的
        if safe(lambda mi=mi: str(mi.get_class().get_name())) == "MaterialInstanceConstant":
            parent = safe(lambda mi=mi: mi.get_editor_property("parent"))
            if parent is not None and not isinstance(parent, str):
                pname = safe(lambda parent=parent: str(parent.get_path_name()))
                rep["materials"][pname] = {
                    "class": "Material(父)",
                    "blend": safe(lambda parent=parent: str(parent.get_editor_property("blend_mode"))),
                    "recompile": safe(lambda parent=parent: mel.recompile_material(parent)),
                }
    time.sleep(2.0)  # 给材质编译线程一点时间把错误打进日志
    rep["seconds"] = round(time.time() - t0, 2)
    d = os.path.dirname(OUT)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2, default=str)
    unreal.log("[EAP] material_compile_check -> %s (%d mats)" % (OUT, len(rep["materials"])))


main()
