# EAP 验收清单（阶段一 + Layer1 链路）

> 规矩：**没截图、没日志、没退出码 = 没过。**
> 记录日期：2026-11-22。本文件只写"可复现的判据"，不写"我觉得可以了"。

## A. 阶段一（manifest + 双端消费端 + 校验器）

| # | 判据 | 判法（命令） | 当前状态 |
|:--:|---|---|---|
| 1 | 同一份 manifest 双端都能读，日志资产数一致 | UE：`consume_manifest.run(schemas/manifest.example.json, dry_run=True)`；Unity：`Tools → EAP → Import Manifest…` | UE dry-run 已过；**Unity 端待用户实跑**（需 Unity 工程） |
| 2 | UE 端自动建母材质 + MIC 并连好贴图 | 跑到 `assets/models`、`assets/textures` 补齐后真跑，查 `/Game/EAP/.../MI_*` | 待真实贴图（assets/ 仍是占位） |
| 3 | Unity 端自动建 Material 并映射贴图 | 同 1 的 Unity 侧 | 待 Unity 实跑 |
| 4 | 法线贴图不吃 sRGB | UE `srgb=False`；Unity `TextureImporterType.NormalMap`，各留 Inspector 截图 | 校验器已强拦（负例 `normal_srgb_true`）；截图待补 |
| 5 | 2 的幂/通道不通过时被拦住 | `python tools/test_validator.py` | ✅ **9/9 拦截成功**（2026-11-22） |
| 6 | 两端资产名/路径/尺寸一致 | 对照两端日志三列 | 待 Unity 实跑 |

**证据**：`python tools/test_validator.py` 末行 `拦截成功 9/9`；
正例 `python tools/validate_manifest.py schemas/manifest.example.json` → `assets=2 errors=0 ... OK`。

## B. Layer 1 数据出口链路（v1.1，规模化后 233 实例）

复现（一条命令，不需要手工点编辑器）：

```bash
python tools/run_pcg_chain.py
# 需要本机：C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe
#          D:\UE_Projects\VegetationPCG\VegetationPCG.uproject
# 可用 --ue / --project 覆盖；--skip-ue-export 复用已有散点
```

| # | 判据 | 实测（2026-11-22 09:28） |
|:--:|---|---|
| 1 | 导出散点数 == 图生成实例数 | 233 == 233 |
| 2 | 导出散点数 == ISM 实例数之和 | 233 == 233 |
| 3 | 导出散点数 == `manifest.placements.count` | 233 == 233 |
| 4 | `manifest.placements` == UE 消费端 dry_run 报告 placements | 233 == 233 |
| 5 | `manifest.placements` == 落位实例数（`place_manifest.place`） | 233 == 233 |
| 6 | 新进程重载关卡读回实例数 == placements（持久化） | 233 == 233 |
| 7 | 落位实例 bbox == manifest items bbox | 误差 0.0000 m（阈值 0.01 m） |
| 8 | 落位实例逐条 transform == manifest items | 0/233 不匹配（1 cm / 0.05°） |

末行输出：`CHAIN PASS points=233 assets=1 placements=233`（退出码 0）。

产物与证据：

| 文件 | 说明 |
|---|---|
| `docs/pcg_forest_pilot.json` | 导出证据：UE 5.7 / `/Game/Untitled` / 图 10 节点参数（密度 0.1）/ 233 条散点（cm），155 KB |
| `schemas/pcg_manifest.json` | 中台产物：manifest v1.1，1 资产 + placements[233]（m），81 KB |
| `schemas/pcg_manifest.ue_report.json` | UE 消费端 dry_run 回执（`*_report.json` 按 .gitignore 不入库，本地留证） |
| `docs/placed_pilot.json` | 落位证据：目标关卡 `/Game/EAP/PCG/PlacedPilot`、Actor `EAP_Placements`、ISM 233 条、读回 transform |
| `docs/placed_verify.json` | 新进程重载关卡读回证据（证明实例真存进关卡，不是内存对象） |
| `Content/EAP/PCG/PlacedPilot.umap`（UE 工程内） | 落位关卡（42 KB，含 233 实例） |
| `Saved/EAP/pcg_scale_run.json`（UE 工程内） | 规模化执行状态：关卡 / actor 数 76 / 面积 2500 m² / 19 → 233 |
| `Saved/EAP/backup/PCG_ForestScatter_*.uasset`（UE 工程内） | 改密度前的图资产备份，可回退 |

**已知限制（不算过的地方，别在面试里含糊）**：
1. 规模刻意压在试点体积：PCG 体积 50×50 m（2500 m²），密度 `0.1 点/m²` → 名义 250 个点、实际 233（93%）；
   换真实树资产 + 扩到 200×200 m 是下一单元。
2. `placements` 已能落位（第 5–8 条），但只落 **transform**：manifest 里的材质、碰撞、LOD 字段本阶段不落；
   落位只支持单资产 manifest（当前 1 个 Cube 网格）。
3. 散点里没有 PCG 点级 metadata（密度/seed attribute），只有最终实例 transform。
4. Unity 端不参与（`/Engine/BasicShapes/Cube` 是引擎内置网格，没有 glb）。
5. **无头编辑器不能重新生成 PCG**：`PCGComponent.generate()` 在 `-unattended -nullrhi` 下报
   `Internal Error - ObjectInstance is null!`（2026-11-22 实测），所以"改参数 + 重生成"必须在真实编辑器里做；
   导出/校验/消费 dry_run 可以无头。
6. **生成是异步的**：编辑器里 `generate()` 调用后 10 秒内实例没变（当时引擎在编译 shader），
   要等 30~60 秒，或直接选中卷在 Details 面板点 Generate 兜底。
7. **脚本必须先确认关卡**：首跑时编辑器当前关卡是空白 `/Temp/Untitled_1`，`get_all_level_actors()` 为空，
   读到"体积 None / 实例 0"；`pcg_scale.py` 现在会先比对并加载 `/Game/Untitled` 再扫 actor。
8. **给关卡 Actor 加组件不能用 `unreal.new_object`**：组件能建出来但不在 `OwnedComponents` 里，存关卡后被丢弃
   （实测：落 3 条 → 存关卡 → 新进程读回 0 条）。必须走 `SubobjectDataSubsystem.add_new_subobject`，
   对应编辑器 Details 面板的 "Add Component"。
9. **`Transform.rotation` 是 Quat**：读回旋转要 `quat.rotator()`；`unreal.Rotator(pitch=, yaw=, roll=)` 用关键字，
   位置参数顺序是 `(roll, pitch, yaw)`（`Rotator(10,20,30)` 实际 roll=10/pitch=20/yaw=30）。
