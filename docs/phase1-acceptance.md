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

## B. Layer 1 数据出口链路（v1.1，扩地块到 200×200 m 后 1892 实例）

复现（一条命令，不需要手工点编辑器）：

```bash
python tools/run_pcg_chain.py
# 需要本机：C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe
#          D:\UE_Projects\VegetationPCG\VegetationPCG.uproject
# 可用 --ue / --project 覆盖；--skip-ue-export 复用已有散点
```

| # | 判据 | 实测（2026-11-22 17:33） |
|:--:|---|---|
| 1 | 导出散点数 == 图生成实例数 | 1892 == 1892 |
| 2 | 导出散点数 == ISM 实例数之和 | 1892 == 1892 |
| 3 | 导出散点数 == `manifest.placements.count` | 1892 == 1892 |
| 4 | `manifest.placements` == UE 消费端 dry_run 报告 placements | 1892 == 1892 |
| 5 | `manifest.placements` == 落位实例数（`place_manifest.place`） | 1892 == 1892 |
| 6 | 新进程重载关卡读回实例数 == placements（持久化） | 1892 == 1892 |
| 7 | 落位实例 bbox == manifest items bbox | 误差 0.0000 m（阈值 0.01 m） |
| 8 | 落位实例逐条 transform == manifest items | 0/1892 不匹配（1 cm / 0.05°） |
| 9 | 落位地块跨度 == 200 m（±15%，`--expect-span-m 200`） | X=196.6 m / Y=191.6 m |

末行输出：`CHAIN PASS points=1892 assets=1 placements=1892`（退出码 0）。

产物与证据：

| 文件 | 说明 |
|---|---|
| `docs/pcg_forest_pilot.json` | 导出证据：UE 5.7 / `/Game/Untitled` / 图 10 节点参数（密度 0.05）/ 1892 条散点（cm），1189 KB |
| `schemas/pcg_manifest.json` | 中台产物：manifest v1.1，1 资产 + placements[1892]（m），626 KB |
| `schemas/pcg_manifest.ue_report.json` | UE 消费端 dry_run 回执（`*_report.json` 按 .gitignore 不入库，本地留证） |
| `docs/placed_pilot.json` | 落位证据：目标关卡 `/Game/EAP/PCG/PlacedPilot`、Actor `EAP_Placements`、ISM 1892 条、读回 transform（436 KB） |
| `docs/placed_verify.json` | 新进程重载关卡读回证据（证明实例真存进关卡，不是内存对象，435 KB） |
| `docs/placed_level_actors.json` | 关卡 Actor 清单（5 个：EAP_Placements + 地面/太阳/天空大气/天空光，太阳 Movable） |
| `docs/ue_placed_pilot_20261122.png` | 引擎内实拍：PlacedPilot 关卡（拍的是 233 那一版）+ 灯光/地面/天空 |
| `Content/EAP/PCG/PlacedPilot.umap`（UE 工程内） | 落位关卡（含 1892 实例，扩地块后由链自动重建） |
| `Saved/EAP/pcg_scale_run.json`（UE 工程内） | 体积/密度执行状态：关卡 / actor 数 76 / 200×200 m / 39997.7 m² |
| `Saved/EAP/backup/PCG_ForestScatter_*.uasset`（UE 工程内） | 改参数前的图资产备份，可回退 |

**已知限制（不算过的地方，别在面试里含糊）**：
1. 地块已扩到 200×200 m（39997.7 m²），密度 `0.05 点/m²` → 名义 2000、实际 1892（94.6%）；
   网格还是引擎 Cube，换真实树资产是下一单元（视觉不像森林是资产问题，不是管线问题）。
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
10. **旧 PCG 组件可能和 actor 脱钩**（本工程实测）：原 `PCG Component` 是 `generated=False`、Details 里
    `图 None / PCG组件 None` 的坏状态，`generate()` 与 Details 的生成按钮都无效（点了没反应）。
    解法：新建 PCGVolume → `set_graph(/Game/PCG_ForestScatter)` → `generate(True)`，一次生成 1892 个实例。
11. **PCG 体积不能用非均匀缩放放大 Z**：把体积 Z 从 20 m 拉到 300 m（actor scale z 10→150）后，
    旧实例被拉长、整体沉到地面以下（2026-11-22 截图确认）。放大只动 XY，Z 保持 20 m。
12. **带 RHI 的无人值守也生成不了 PCG**：`-unattended` 去掉 `-nullrhi` 后 `generate()` 依然 233 → 233，
    必须在人交互的编辑器会话里做生成。
10. **新建空关卡没有灯光/地面**：`new_level` 造出来的关卡只有空世界，视口里实例是黑的。补灯必须在**真实编辑器**里做——
    无头跑 `place_manifest.stage()` 会 `EXCEPTION_INT_DIVIDE_BY_ZERO` 崩溃（SkyAtmosphere/SkyLight 需要渲染管线，实测 2026-11-22）；
    而且 DirectionalLight 默认 Static，新关卡没烘焙时对物体无贡献，必须改成 Movable。
