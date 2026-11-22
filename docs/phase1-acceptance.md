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

## B. Layer 1 数据出口链路（v1.1 试点）

复现（一条命令，不需要手工点编辑器）：

```bash
python tools/run_pcg_chain.py
# 需要本机：C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe
#          D:\UE_Projects\VegetationPCG\VegetationPCG.uproject
# 可用 --ue / --project 覆盖；--skip-ue-export 复用已有散点
```

| # | 判据 | 实测（2026-11-22 08:52） |
|:--:|---|---|
| 1 | 导出散点数 == 图生成实例数 | 19 == 19 |
| 2 | 导出散点数 == ISM 实例数之和 | 19 == 19 |
| 3 | 导出散点数 == `manifest.placements.count` | 19 == 19 |
| 4 | `manifest.placements` == UE 消费端 dry_run 报告 placements | 19 == 19 |

末行输出：`CHAIN PASS points=19 assets=1 placements=19`（退出码 0）。

产物与证据：

| 文件 | 说明 |
|---|---|
| `docs/pcg_forest_pilot.json` | 导出证据：UE 5.7 / `/Game/Untitled` / 图 10 节点参数 / 19 条散点（cm） |
| `schemas/pcg_manifest.json` | 中台产物：manifest v1.1，1 资产 + placements[19]（m） |
| `schemas/pcg_manifest.ue_report.json` | UE 消费端 dry_run 回执（`*_report.json` 按 .gitignore 不入库，本地留证） |

**已知限制（不算过的地方，别在面试里含糊）**：
1. 19 个实例是 2026-10 的**试点规模**，不是成品密度（按 `0.01 点/m²` 放到 15 km² 应为几万个点）。
2. `placements` 只被读取和统计，**没有落位**成 UE ISM 实例。
3. 散点里没有 PCG 点级 metadata（密度/seed attribute），只有最终实例 transform。
4. Unity 端不参与（`/Engine/BasicShapes/Cube` 是引擎内置网格，没有 glb）。
