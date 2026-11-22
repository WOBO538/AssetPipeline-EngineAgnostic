# 多引擎资产管线系统 · Engine-Agnostic Asset Pipeline

> 项目代号：`Engine-Agnostic Pipeline`（下称 EAP）
> 定位：前端 PCG + AIGC 生成 → Python 中台（格式中转 + 合规审查）→ UE5 / Unity 双端消费。
> **当前进度：阶段一（manifest 标准 + 双端消费端模板）**。

## 一、这个项目在作品集里的位置（先读这段）

| 问题 | 结论 |
|---|---|
| 和 NO.01（V3 资产智能管线）什么关系 | **同一条线的下一代**，不是新项目：NO.01 是"单引擎 + 规则/AI 分类"，EAP 是"跨引擎 + PCG 生成 + 合规拦截"。网站**不新增第 10 张卡**（基线审刚要求精简到 5 卡 + 1 横排），改为 NO.01 卡内的版本演进叙事 |
| 补的是哪个缺口 | 四家 AI 基线审一致排第 1 的 **PCG 资格级缺口**（Layer 1），顺带补 ①引擎内工具（Layer 4）与 ⑤取舍沟通（跨引擎架构决策） |
| 不做什么 | 不做 Layer 5 的 VFX/HLOD（加分项，不是资格项）；Unity 端只做最小可验证（材质 + Prefab + Collider），不做 Unity 全套工具 |

## 二、目录

```
Engine-Agnostic Pipeline/
├─ README.md                      ← 本文件
├─ docs/
│  ├─ manifest-spec.md            ← manifest 字段规范（v1.1）+ 引擎映射表 + 坑
│  ├─ phase1-acceptance.md        ← 验收：阶段一 6 条 + Layer1 链路 4 条
│  └─ pcg_forest_pilot.json       ← Layer1 导出证据：10 节点图参数 + 19 条散点（cm）
├─ schemas/
│  ├─ manifest.schema.json        ← JSON Schema（v1.1：placements / engine_native）
│  ├─ manifest.example.json       ← 两个资产的完整示例（v1.0 结构，v1.1 向后兼容）
│  └─ pcg_manifest.json           ← 由散点生成的 manifest v1.1（1 资产 + 19 实例）
├─ ue/Content/Python/
│  ├─ consume_manifest.py         ← UE5 消费端（读 manifest → 母材质/MIC → 贴图 → 碰撞 → placements 统计）
│  └─ pcg_export.py               ← Layer1 出口：PCG 图参数 + 生成实例 → 散点 JSON（只读）
├─ unity/Editor/
│  └─ ManifestImporter.cs         ← Unity 编辑端（读同一份 manifest → Material → Prefab → Collider）
├─ tools/
│  ├─ validate_manifest.py        ← 中台侧轻量校验（结构 + 2 的幂 + 通道 + 单位 + v1.1 placements）
│  ├─ pcg_to_manifest.py          ← 散点 JSON → manifest v1.1（cm→m，1 网格 = 1 asset + N placements）
│  ├─ run_pcg_chain.py            ← 一条命令跑完整条 Layer1 链 + 4 条断言
│  ├─ test_validator.py           ← 校验器 9 条负例（拦截 9/9）
│  └─ test_gui_smoke.py           ← GUI 冒烟（12/12）
└─ assets/                        ← 占位：放示例 glb / 贴图（阶段二接入真实产物）
```

## 三、阶段一怎么跑

**1. 造一份 manifest**

```bash
python tools/validate_manifest.py schemas/manifest.example.json
# 期望：OK assets=2 warnings=0 errors=0
```

**2. UE5 端（编辑器内 Python，先 dry-run）**

```python
# 编辑器菜单 Tools → Execute Python Script，或在 Output Log 的 Cmd 里：
import sys; sys.path.append(r"<repo>/ue/Content/Python")
import consume_manifest
consume_manifest.run(r"<repo>/schemas/manifest.example.json", dry_run=True)   # 只打印将做什么
consume_manifest.run(r"<repo>/schemas/manifest.example.json", dry_run=False)  # 真跑
```

**3. Unity 端**

```
菜单 Tools → EAP → Import Manifest…  → 选同一份 manifest.example.json
（或命令行：Unity -batchmode -executeMethod EAP.ManifestImporter.ImportFromArgs -eapManifest <path>）
```

**4. 界面（阶段一 GUI 壳）**

```bash
python run.py        # 或 python -m gui.app；暗色三栏 + 拖放区 + Toast
```

## 三之二、Layer 1 数据出口（v1.1 试点，一条命令）

**背景**：Layer 1 原先是 2026-10 的 UE PCG 手工试点（`D:\UE_Projects\VegetationPCG`，UE 5.7，
`/Game/PCG_ForestScatter`，用 `/Engine/BasicShapes/Cube` 方盒子替树，**19 个实例**）。
试点只证明"Graph 能出结果"，缺的是**数据出口**——生成结果进不了中台。v1.1 把这条线接通：

```
UE PCG 图（10 节点：GetLandscapeData → SurfaceSampler → TransformPoints → StaticMeshSpawner
           + 5 个用户参数节点 + 1 个 AttributeMathsOp）
   ↓ pcg_export.py           只读：图参数 + 19 条实例 transform（uu = cm）
docs/pcg_forest_pilot.json
   ↓ pcg_to_manifest.py      cm→m；1 个网格 = 1 个 asset + placements[19]（不为每实例造资产）
schemas/pcg_manifest.json    （manifest v1.1）
   ↓ validate_manifest.py    errors=0
   ↓ consume_manifest.run(dry_run=True)   读到 placements，统计 19
```

```bash
python tools/run_pcg_chain.py     # 两次无头 UE（各约 15s）+ 两次纯 Python
# 期望末行：CHAIN PASS points=19 assets=1 placements=19
```

导出文件 `docs/pcg_forest_pilot.json` 同时是**图参数的原始证据**（`graph.nodes[*].props`）：
`points_per_squared_meter=0.01`、`point_extents=[50,50,50]`、`point_steepness=1.0`、
`absolute_rotation=True`、`offset_min/max=±[100,100,50]`、`scale_min/max=[0.8]/[1.2]`、
`uniform_scale=True`、`seed=-1506168781`、`StaticMeshSpawner` 网格 `/Engine/BasicShapes/Cube`。
这比截图更可核对：面试被问"你图里到底怎么配的"，直接翻 JSON。

> **关于 19 个实例**：PCG 体积实测 **50×50 m（2500 m²）**，密度 `0.01 点/m²` 名义上是 25 个点，
> 实际 19 个（网格量化 + `looseness 0.8` 的正常偏差，不是丢数据）。
> 要"看得出是树林"得把密度提到 `0.1` 左右（约 250 棵），那是 Layer1 **规模化**这一格，下一单元做。

## 四、阶段一验收（6 条，逐条可判）

| # | 判据 | 判法 |
|:--:|---|---|
| 1 | 同一份 manifest 双端都能读 | UE 与 Unity 各跑一次，均无报错，日志里打印资产数一致 |
| 2 | UE 端能自动建出母材质 + 材质实例并连好贴图 | 材质实例里 BaseColor/Normal/ORM 三个参数有值，指向正确贴图 |
| 3 | Unity 端能自动建出 Material 并映射贴图 | `_BaseMap` / `_BumpMap` / `_MetallicGlossMap` 三个槽有值 |
| 4 | 法线贴图不吃 sRGB | UE 里 `srgb=False`、Unity 里 `TextureImporterType.NormalMap`，各留一张 Inspector 截图 |
| 5 | 2 的幂 / 通道完整性不通过时**被拦住** | 故意把一张贴图改成 1000×1000，跑校验脚本必须报 error 并非零退出 |
| 6 | 同一份 manifest 在两端产出的资产名、路径、尺寸一致 | 对照日志：资产名 / 目标路径 / 包围盒三列完全一致 |

> 没截图、没日志、没退出码 = 没过。这条照搬项目验收标准。

### 四之二、Layer 1 链路验收（4 条，`python tools/run_pcg_chain.py` 自动判）

| # | 判据 | 实测 |
|:--:|---|---|
| 1 | 导出散点数 == 图生成实例数 | 19 == 19 |
| 2 | 导出散点数 == ISM 实例数之和 | 19 == 19 |
| 3 | 导出散点数 == `manifest.placements.count` | 19 == 19 |
| 4 | `manifest.placements` == UE 消费端 dry_run 报告里的 placements | 19 == 19 |

产物：`docs/pcg_forest_pilot.json`（20 KB，图参数 + 19 条散点）、`schemas/pcg_manifest.json`（8.6 KB）。

## 五、阶段一未覆盖（写在明处，别在面试里含糊）

- **轴心（Pivot）**：UE Python API **没有**直接改 StaticMesh 轴心的接口。本仓库的做法是把 `pivot.mode` + `pivot.offsetM` 写进 manifest，在**导出端**（DCC/glTF 节点变换）执行；消费端只做校验与告警。Unity 端同理由 Prefab 根节点补偿。
- **Unity 的粗糙度通道**：URP 的 `_MetallicGlossMap` 是 R=Metallic、A=Smoothness，**Smoothness = 1 − Roughness**。manifest 里显式写 `channelPacking`，转换在消费端完成（不在导出端预乘），避免两端各算一遍。
- **贴图压缩格式**：UE 的 `TC_Normalmap` / Unity 的 crunch 等属于平台设置，本阶段只写进 manifest 的期望值，不保证跨机器一致。
- **LOD/HLOD**：manifest 里记录 `lods[]`，本阶段只校验不生成。
- **placements 落位未实现（v1.1）**：消费端现在只**读取并统计** `placements`（dry_run 报告 19），真正在 UE 里落成 ISM 实例留阶段二。原因：UE Python 没有"给 PCGVolume 稳定加 ISM"的接口，且盲落位会污染原关卡。
- **PCG 点级 metadata 读不到（v1.1）**：`get_generated_graph_output()` 能拿到 `PCGDataCollection`（tagged=1），但点级 attribute（密度/seed）没有暴露的 Python 访问器。所以出口以**实例 transform** 为准——实例是最终结果，反而更可信。
- **Unity 端跳过引擎内置网格（v1.1）**：`model.source=engine`（如 `/Engine/BasicShapes/Cube`）没有 glb，Unity 不适用；只有文件型资产走双端。
- **无头导出依赖本机路径（v1.1）**：`run_pcg_chain.py --ue/--project` 可覆盖，默认 `C:\Program Files\Epic Games\UE_5.7` + `D:\UE_Projects\VegetationPCG`。

## 六、下一步（阶段二/三）

- 阶段二 · Layer 1 规模化：把试点（19 个方盒子）扩到可看地块（先 20×20 m，再 200×200 m），
  参数走 Graph 用户参数而不是改节点常量；同时接 ComfyUI 客户端（GUI 阶段二的 QThread 异步）
- 阶段二 · placements 落位：在 UE 里把 `placements` 落成 ISM 实例（需要一个小 C++/Blueprint 接收器，或改用 PCG 的 Data Table 回灌）
- 阶段三：`trimesh` + `numpy` 坐标转换（Y-up↔Z-up、cm↔m）与合规拦截（与 `tools/validate_manifest.py` 合并）
- 阶段四（作品集包装）：量化三段时间——**导入耗时 / 材质实例化耗时 / 碰撞生成耗时**，各自单测 20 个资产取中位数；把"10 分钟/个 → X 秒/个"写成三段可复现的表
