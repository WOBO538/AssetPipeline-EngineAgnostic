"""触发 UE 材质编译（材质编译失败时 UE 会退回默认白材质，日志里打 `Failed to compile Material`）。

为什么要单独跑一次：`-nullrhi` 下不会真正编译 shader/材质，只有带 RHI 的进程加载材质才会暴露
"Failed to compile Material for platform PCD3D_SM6" 这类错误（2026-11-22 实测，正是它让我们看到
"树全白"的根因）。

**判据说明**：每条材质里的 `shaderCompiled` 只是**弱信号**（`get_statistics` 的像素 shader 指令数 > 0 就记为
True，拿不到或该 API 不可用就记 False），用来看"这组材质有没有编译产物"，**不能单独当 PASS 依据**；
最终判据在 `run_pcg_chain.py`：编译子进程 `exit == 0` **且**日志无 `Failed to compile Material`。
崩溃（非零退出）就是 CHAIN FAIL，不做 SKIP/PASS 兜底。

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


def _blend_of(node):
    """沿父链找到 Material 再读 blend_mode（MIC 上没有 blend_mode 属性，直接读会报噪音）。"""
    for _ in range(6):
        if node is None or isinstance(node, str):
            return None
        if safe(lambda node=node: str(node.get_class().get_name()), "") == "Material":
            return safe(lambda node=node: str(node.get_editor_property("blend_mode")))
        node = safe(lambda node=node: node.get_editor_property("parent"))
    return None


def _shader_compiled(material_like):
    """弱信号：像素 shader 指令数 > 0 视为有编译产物；取不到就 False（不参与 PASS/FAIL 判定）。"""
    stats = safe(lambda: unreal.MaterialEditingLibrary.get_statistics(material_like))
    if stats is None or isinstance(stats, str):
        return False
    try:
        return int(stats.get_editor_property("num_pixel_shader_instructions")) > 0
    except Exception:  # noqa: BLE001
        return False


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
            "blend": safe(lambda mi=mi: _blend_of(mi)),
            "shaderCompiled": _shader_compiled(mi),
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
                    "blend": safe(lambda parent=parent: _blend_of(parent)),
                    "shaderCompiled": _shader_compiled(parent),
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
