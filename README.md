# 多引擎资产管线系统 · Engine-Agnostic Asset Pipeline

> 项目代号：`Engine-Agnostic Pipeline`（下称 EAP）
> 定位：前端 PCG + AIGC 生成 → Python 中台（格式中转 + 合规审查）→ UE5 / Unity 双端消费。
> **当前进度：阶段一（manifest 标准 + 双端消费端模板）+ Layer1 数据出口 + 地块扩到 200×200 m（1892 实例）+ placements 落位（回灌成 UE ISM）+ 换真实树 glb（1892 棵树端到端跑通）**。

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
│  ├─ phase1-acceptance.md        ← 验收：阶段一 6 条 + Layer1 链路 11 条
│  ├─ tree_asset_requirements.md   ← 树资产采集要求（给采集会话的硬门槛）
│  ├─ pcg_forest_pilot.json       ← Layer1 导出证据：10 节点图参数 + 1892 条散点（cm）
│  ├─ placed_pilot.json           ← 引擎 Cube 落位证据：1892 条落成 UE ISM + 读回 transform
│  ├─ placed_tree.json            ← 真实树落位证据（同上格式，网格换成 conifer.glb）
│  └─ placed_verify.json          ← 新进程重载关卡读回证据（证明持久化）
├─ schemas/
│  ├─ manifest.schema.json        ← JSON Schema（v1.1：placements / engine_native）
│  ├─ manifest.example.json       ← 两个资产的完整示例（v1.0 结构，v1.1 向后兼容）
│  └─ pcg_manifest.json           ← 由散点生成的 manifest v1.1（1 资产 + 1892 实例）
├─ ue/Content/Python/
│  ├─ consume_manifest.py         ← UE5 消费端（读 manifest → 母材质/MIC → 贴图 → 碰撞 → placements 统计）
│  ├─ pcg_export.py               ← Layer1 出口：PCG 图参数 + 生成实例 → 散点 JSON（只读）
│  └─ place_manifest.py           ← Layer1 回灌：placements → UE ISM 实例（落位；glb 走无头 Interchange 导入）
├─ unity/Editor/
│  └─ ManifestImporter.cs         ← Unity 编辑端（读同一份 manifest → Material → Prefab → Collider）
├─ tools/
│  ├─ validate_manifest.py        ← 中台侧轻量校验（结构 + 2 的幂 + 通道 + 单位 + v1.1 placements）
│  ├─ pcg_to_manifest.py          ← 散点 JSON → manifest v1.1（cm→m，1 网格 = 1 asset + N placements）
│  ├─ tree_to_manifest.py         ← 真实树 glb + 散点 → manifest v1.1（双端；不信任 sidecar，自己解析 GLB 复算）
│  ├─ run_pcg_chain.py            ← 一条命令跑完整条 Layer1 链（7 步 / 4 次无头 UE）+ 9 条断言；--multi 13 条 / --real-multi 17 条（--with-compile 18 条）；--tree 16 条为历史链路
│  ├─ draw_placements.py          ← 从读回 transform 画俯视图（方块 / 树冠圆两种标记）
│  ├─ test_validator.py           ← 校验器 9 条负例（拦截 9/9）
│  └─ test_gui_smoke.py           ← GUI 冒烟（12/12）
└─ assets/raw/trees/              ← 真实树资产（conifer / broadleaf .glb + source_assets.json 许可证凭证；模型不入库）
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

## 三之二、Layer 1 数据出口 + 扩地块（v1.1，一条命令）

**背景**：Layer 1 原先是 2026-10 的 UE PCG 手工试点（`D:\UE_Projects\VegetationPCG`，UE 5.7，
`/Game/PCG_ForestScatter`，用 `/Engine/BasicShapes/Cube` 方盒子替树，19 个实例）。
2026-11-22 分两步：① 数据出口接到中台（密度 `0.01 → 0.1`，实例 **19 → 233**）；
② **地块扩到 200×200 m**（密度 `0.1 → 0.05`，实例 **233 → 1892**，名义 2000、实际 1892 = 94.6%）。

```
UE PCG 图（10 节点：GetLandscapeData → SurfaceSampler → TransformPoints → StaticMeshSpawner
           + 5 个用户参数节点 + 1 个 AttributeMathsOp）
   ↓ pcg_export.py           只读：图参数 + 1892 条实例 transform（uu = cm）
docs/pcg_forest_pilot.json    （1189 KB）
   ↓ pcg_to_manifest.py      cm→m；1 个网格 = 1 个 asset + placements[1892]（不为每实例造资产）
schemas/pcg_manifest.json    （manifest v1.1，626 KB）
   ↓ validate_manifest.py    errors=0
   ↓ consume_manifest.run(dry_run=True)   读到 placements，统计 1892
```

```bash
python tools/run_pcg_chain.py --expect-span-m 200    # 四次无头 UE（各约 13-15s）+ 两次纯 Python；7 步 9 条断言
# 期望末行：CHAIN PASS points=1892 assets=1 placements=1892
```

**改体积 / 密度（必须在真实编辑器里做）**——两个坑都是 2026-11-22 实测：

1. **无头不能重生成 PCG**：`PCGComponent.generate()` 在 `-unattended -nullrhi` 下报
   `Internal Error - ObjectInstance is null!`；带 RHI 的 `-unattended` 也生成不了（233 → 233）。
2. **旧 PCG 组件可能和 actor 脱钩**：这个工程原 `PCG Component` 处于 `generated=False`、
   Details 里 `图 None / PCG组件 None` 的坏状态，`generate()` 和 Details 的生成按钮都无效（点了没反应）。
   解法：**新建一个 PCGVolume → `set_graph()` 挂同一张图 → `generate(True)`**，一次生成 1892 个实例。

体积按米换算：brush 基准 2 m，原 actor scale `25/25/10` = 50×50×20 m；放大到 200 m 用
`actor scale 99.98 / 97.675 / 10`（**Z 保持 20 m**——把 Z 也非均匀放大 150 倍会让实例被拉长、沉到地面以下，
2026-11-22 踩过）。落位 bbox 实测 **196.6 × 191.6 m**（边缘点没采到，< 200 m 属正常）。

导出文件同时是**图参数的原始证据**（`graph.nodes[*].props`）：`points_per_squared_meter=0.05`、
`point_extents=[50,50,50]`、`point_steepness=1.0`、`absolute_rotation=True`、`offset_min/max=±[100,100,50]`、
`scale_min/max=[0.8]/[1.2]`、`uniform_scale=True`、`seed=-1506168781`、`StaticMeshSpawner` 网格 `/Engine/BasicShapes/Cube`。
这比截图更可核对：面试被问"你图里到底怎么配的"，直接翻 JSON。

> **关于数量**：PCG 体积实测 **200×200 m（39997.7 m²）**，0.05 点/m² → 名义 2000、实际 **1892**（94.6%）；
> 上一轮 50×50 m + 0.1 → 名义 250、实际 233（93%）；最早 0.01 → 名义 25、实际 19（76%）。
> 偏差都来自网格量化 + `looseness 0.8`，不是丢数据。

## 三之三、placements 落位（回灌）：manifest → UE ISM 实例

前面三之二证明"PCG 生成能变成 manifest"；这一格证明**反过来也能**：拿 manifest 里的 `placements[1892]`
在 UE 里真的落成 1892 个 ISM 实例，并验证坐标/旋转/缩放一条不差。

```
schemas/pcg_manifest.json（placements 1892 条，m / deg）
   ↓ place_manifest.place()     清空 → 逐条 add_instances(世界坐标) → clear/重建可重复跑
/Game/EAP/PCG/PlacedPilot        专用关卡：1 Actor + 1 InstancedStaticMeshComponent + 1892 实例
   ↓ place_manifest.verify()    新进程重载关卡 → 读回 1892 条 transform → docs/placed_verify.json
```

![placements 落位俯视图](docs/placed_pilot_topdown.png)

> 上图由 `tools/draw_placements.py` 从 `docs/placed_pilot.json` 的读回 transform 画（俯视 XY、方块按 yaw 旋转并乘 scale），不是手绘示意图。

![UE 内实拍：PlacedPilot 关卡](docs/ue_placed_pilot_20261122.png)

> 上图：`/Game/EAP/PCG/PlacedPilot` 引擎内实拍（2026-11-22 16:24）。大纲 5 个 Actor：
> `EAP_Placements`（ISM 1892 实例）、`EAP_Ground`、`EAP_Sun`（Movable）、`EAP_SkyAtmosphere`、`EAP_SkyLight`；
> 机器可读状态见 `docs/placed_level_actors.json`（无头重载复核：5 actors / 1892 instances）。

实现要点（踩过的坑，都写进代码注释了）：

- **给关卡里的 Actor 加组件不能用 `unreal.new_object`**：能建出来但不在 `OwnedComponents` 里，
  存关卡后组件被丢掉（实测：3 条实例落盘后读回 0 条）。必须走
  `SubobjectDataSubsystem.add_new_subobject(AddNewSubobjectParams)`——就是 Details 面板 "Add Component" 的 Python 路径。
- 关卡里 **Actor 自己会存**，所以"先落位再读回"要**新进程**验证，否则验证的是内存里的对象不是存档。
- `Transform.rotation` 是 **Quat**，`FRotator` 要从 `quat.rotator()` 取；`unreal.Rotator(pitch=, yaw=, roll=)` 用关键字，
  位置参数顺序是 `(roll, pitch, yaw)`（`Rotator(10,20,30)` 实际是 roll=10,pitch=20,yaw=30）。
- manifest 的 `r` 沿用导出端的 `[roll, pitch, yaw]`（来自 `FRotator.euler()` 的 x/y/z），落位端按同一顺序还原。

```bash
# 单独跑（不用整条链）：
#   UE 里 -ExecutePythonScript=ue/Content/Python/place_manifest.py        （默认 place）
#   EAP_PLACE_MODE=verify 再跑一次                                        （验证）
```

**已知边界**：只处理单资产 manifest（现在就是 1 个 Cube 网格）；只落 transform（材质/碰撞/LOD 走 manifest 其它字段，
本阶段不落）；落位关卡是专用关卡，不碰你的 `/Game/Untitled` 试点现场。

## 三之四、换真实树资产（glb）：管线第一次吃"真东西"

前三节的所有数字都建立在 `/Engine/BasicShapes/Cube` 上，Unity 端还是跳过的（引擎网格没有 glb）。
这一节把网格换成真实的 **glTF 2.0 树资产**，双端第一次都有文件型目标。

```
assets/raw/trees/conifer.glb（Poly Pizza / Quaternius，CC0，3 370 面，10.236 m，贴图内嵌）
   ↓ tree_to_manifest.py   自己解析 GLB 二进制复算 bbox/面数/本证，再和 sidecar 对账（不信上游）
schemas/tree_manifest.json   manifest v1.1：model.source=file + 双端 engineTargets + placements[1892]
   ↓ validate_manifest.py    errors=0
   ↓ place_manifest.place() 无头 Interchange 导入 glb → StaticMesh → 材质规范化 → ISM 落 1892 个实例
/Game/EAP/PCG/PlacedTrees   专用关卡：1 Actor + 1 ISM（1892 棵 conifer）+ 2 个 EAP 材质覆盖
   ↓ place_manifest.verify() 新进程重载读回 → docs/placed_tree_verify.json
```

```bash
python tools/run_pcg_chain.py --tree --expect-span-m 200    # 历史链路（v1.5 起不作验收门槛）：16 条断言，需 /Game/Untitled 的 200 m 散点
# 期望末行：CHAIN PASS points=1892 assets=1 placements=1892
```

![真实树落位俯视图](docs/placed_tree_topdown.png)

> 上图仍是读回的**真实 transform**（1892 条），只是标记换成树冠圆（半径按 glb bbox X/2 = 2.90 m × scale）。

![UE 内实拍：PlacedTrees 关卡 1892 棵树（材质规范化后）](docs/ue_trees_placed_20261123.png)

> 上图：`/Game/EAP/PCG/PlacedTrees` 引擎内实拍（2026-11-23，用户手操，Windows 桌面版 UE 5.7 编辑器）：
> **材质规范化之后**的绿树，树皮有层次、叶子边缘干净 —— 白模事故已修（见 §三之五）。
> 大纲 6 个 Actor：`EAP_Placements`（ISM 1892 棵）、`EAP_Ground`、`EAP_Sun`、`EAP_SkyLight`、
> `EAP_SkyAtmosphere`、`EAP_Fog`。树根落地、投影方向一致、地块边缘可见。
> 对照图 `docs/ue_trees_placed_20261122.png` 是规范化之前的 Interchange 材质版本（偏荧光绿、树皮没层次），留档。

**资产体检（我独立解析 GLB，不采信 `source_assets.json` 的自述）**

| 项 | conifer | broadleaf |
|---|---|---|
| 许可 / 来源 | CC0-1.0 · Quaternius（Poly Pizza） | 同 |
| 树高 | 10.2364 m | 9.4252 m |
| 三角面 | 3 370（glTF 索引复算，2 个 primitive：2160 + 1210） | 3 505 |
| 原点 | bbox min y = 0.0000（树干底部已归一化） | 同 |
| 贴图 | 3 张 1024² 内嵌，无外部 uri | 同 |
| 材质 | 树皮 OPAQUE + 叶 BLEND，均 metallicFactor 0.4 | 同 |

**三个实测发现（都写进代码注释了）**

1. **无头能导 glTF**：UE 5.7 `-nullrhi -unattended` 下 Interchange glTF 导入可用，单棵 0.4~1.2 s，
   落点固定为 `<destPath>/<文件名>/StaticMeshes/<文件名>`，材质/贴图由 Interchange 自动生成。
   （这条以前不敢保证，先单独跑了一次探针才敢接进链路。）
2. **轴心补偿 0.5 m 必须做**：散点的 `t` 是引擎 Cube 的**中心**，树的轴心在**树根底部**；照抄 `t` 会让
   1892 棵树整体悬空半米。manifest 里 `pivot.zOffsetM = 0.5` 记录这次换算，落位端按底面落。
3. **Nanite 默认开着**：导入后 `mesh.get_num_triangles(0)` 报 **1 414**（源 3 370）、broadleaf 报
   2 354（源 3 505）——返回的是 Nanite 的 fallback 简化网格，不是源数据。**验收表用 glTF 侧的 3 370**，
   UE 侧的 1 414 当作"Nanite fallback"记录，别混着引用。
4. **导入的材质缺用法标记**（用户实拍时 `地图检测` 报出）：`/InterchangeAssets/gltf/M_Default` 与
   `/Game/EAP/Trees/conifer/Materials/Bark_NormalTree` 都报"缺失使用标记 `bUsedWithNanite` /
   `bUsedWithInstancedStaticMeshes`，材质资产未重新保存时**编辑器外运行时可能渲染不出来**"。
   编辑器里没事（实拍正常），但这是打包前的必修项——和材质规范化一起做。

**这一节只解决"网格换成真资产"**；材质三个坑（BLEND / metallic 0.4 / usage 标记）由下一节处理，
LOD、双树种混排、Unity 端实跑仍留在第五节。

### 三之五、材质规范化：Interchange 的默认材质不能直接上

Interchange 导入 glTF 时自动生成的材质有三个坑，实拍（上一节）全暴露出来了：
叶子是 `BLEND`（1892 个实例半透明排序）、`metallicFactor = 0.4`（画面荧光绿/塑料感）、
缺 `bUsedWithNanite` / `bUsedWithInstancedStaticMeshes` 用法标记（打包后可能丢材质）。

解法不是去改 Interchange 的引擎默认材质（那是污染引擎资产），而是**在落位端把材质换掉**：

```
Interchange 生成的 MIC（读参数：BaseColorTexture / NormalTexture / MetallicFactor）
   ↓ place_manifest._normalize_materials()
读贴图映射 → 建/复用 EAP 母材质 M_EAP_Foliage_Masked（MASKED、双面、metallic 0、roughness 0.9）
                               + M_EAP_Opaque（OPAQUE、metallic 0）
          → 派生 MIC（MI_EAP_<资产>_<槽>_<masked|opaque>）→ 设到 ISM 两个材质槽
```

三个关键点：

- **分类看 manifest 里 glTF 原始的 `alphaMode`**，不靠猜：`BLEND/MASK → MASKED`，其余 OPAQUE。
  顺带记一个坑：UE 的枚举字符串是 `"<BlendMode.BLEND_OPAQUE: 0>"`，**OPAQUE 里也含 "BLEND"**，
  用子串判断会把树皮也当成半透明材质（第一版就是这样，被断言抓出来了）。
- **贴图映射从源 MIC 的参数里读**（`BaseColorTexture` / `NormalTexture`），不按文件名猜——换任何 glTF 都能自动对上。
- **用法标记在母材质上设**（`set_material_usage` × STATIC_MESH / INSTANCED_STATIC_MESHES / NANITE），
  这样派生出来的所有 MIC 都继承，不用逐个改。

实测（2026-11-22 18:1x，无头全自动，不需要点编辑器）：

| 槽 | 源材质 | 源 alphaMode | 源 metallic | 规范化后 | 贴图 |
|---|---|---|---|---|---|
| 0 | Bark_NormalTree | OPAQUE | 0.40 | `MI_EAP_conifer_0_opaque`（OPAQUE） | BaseColor + Normal |
| 1 | Leaves_Pine | BLEND | 0.40 | `MI_EAP_conifer_1_masked`（**MASKED**，clip 0.33，双面） | BaseColor（alpha 70.5% 全透明，正好适合 masked） |

顺带记一个**真踩到的坑**：用户开着 UE 编辑器占着 `PlacedTrees.umap` 时，无头落位保存关卡会失败
（`LogFileManager: Error Code 32` → `Failed to move ... to temp directory`），关卡存不进去、
材质覆盖自然也没落盘。**跑链前必须关掉编辑器（或至少关掉目标关卡）**——这条已写进验收文档。

#### 白模事故（v1.3 第一版材质全部编译失败）

第一版 EAP 母材质里给法线采样节点设了 `sampler type = Normal` 但**没给默认贴图**，而树叶在 glTF 里
根本没有法线贴图。UE 编译直接报：

```
M_EAP_Opaque / M_EAP_Foliage_Masked:
  Failed to compile Material for platform PCD3D_SM6, Default Material will be used in game.
(Node TextureSampleParameter2D) Sampler type is Normal, should be Color for DefaultTexture
```

→ 整个材质编译失败 → **UE 回退默认白材质** → 1892 棵树全白（用户在编辑器里一眼看出来的）。

两个教训都写进代码与链路了：

1. **建法**：没法线贴图的槽不建法线节点；BaseColor 采样节点也设默认贴图兜底；母材质改名 `_v2`，
   旧的坏材质（含探针留下的 `M_EAP_Tree_Bark/Leaf`）在落位时自动清理。
2. **验法**：`-nullrhi` 下 UE **不会真正编译材质**，所以"材质资产挂上了"这类断言全 PASS 也可能是白模。
   新增 `ue/Content/Python/check_material_compile.py`：链路末尾用**带 RHI** 的无头进程加载关卡里
   实际用到的材质并重编译，抓日志里的 `Failed to compile Material` → 进第 16 条断言。自那以后白模会直接 FAIL。

## 三之六、多资产 manifest：一份散点文件 → 20 个 asset + 各自 placements（v1.4）

W2 的验收缺口是「一份 **20 个资产** 的 manifest 要通过校验」。v1.1 的链路是「1 个网格 = 1 个 asset」，
这一格把分组打开：散点文件里出现多个不同网格时，**每个网格一条 asset**，`placements` 只装属于它的点。

```
docs/pcg_multi_fixture.json（合成 fixture，seed=20261123，20 网格 / 156 实例 / cm）
   ↓ pcg_to_manifest.py --scatter ... --out ...
schemas/pcg_manifest_multi.json（20 assets / 156 placements / m，errors=0）
   ↓ place_manifest.place()   /Game/EAP/PCG/PlacedMulti：1 个 Actor + 20 个 ISM（EAP_Asset_0..19）
   ↓ place_manifest.verify()  新进程重载关卡，逐 asset 读回数量与 transform
```

```powershell
# 2) 合成 fixture（固定 seed，重复跑逐字节一致）→ 多资产 manifest → 校验
python tools/make_multi_asset_fixture.py --out docs/pcg_multi_fixture.json
python tools/pcg_to_manifest.py --scatter docs/pcg_multi_fixture.json --out schemas/pcg_manifest_multi.json
python tools/validate_manifest.py schemas/pcg_manifest_multi.json      # assets=20 errors=0 warnings=0 / OK

# 3) 多资产全链路（含 UE 无头落位 + 新进程读回）：13 条断言
python tools/run_pcg_chain.py --multi --expect-assets 20
# 期望末行：CHAIN PASS assets=20 placements=156

# UE 不可用时只跑前段（fixture / manifest / validate，7 条纯 Python 断言）
python tools/run_pcg_chain.py --multi --expect-assets 20 --skip-ue
```

实测（2026-11-23）：fixture 20 资产 / 156 实例；manifest `assets=20 placements=156`；
UE 落位 `placed=156/156 levelSaved=True`；新进程读回 `assets=20 count=156 ok=True matched=20/20`；
最大误差 `t=0.000000 m / r=0.0000° / s=0.000000`（阈值 1e-3 m / 0.1° / 1e-3）。重跑一次结果相同（幂等）。

设计要点：

- **分组键是 `mesh.packagePath`**：`pcg_to_manifest.py` 按网格分组，每条 asset 只带属于自己的 points；
  `checks` 里继续记 `instance_count==placements.count`，`validate_manifest.py` 会复核这条声明
  （不新增字段、不改 schema，仍兼容 v1.1 的 9 条负例）。
- **单网格输入保持字节级等价**：`docs/pcg_forest_pilot.json` 重跑后与 `schemas/pcg_manifest.json`
  逐字段对比只差 `generatedAt`。老链路 `--tree --expect-span-m 200` 曾 16/16 PASS，v1.5 起降级为历史链路（见 §五 最后一条）。
- **组件名可预测**：每个 asset 一个 ISM，创建后 `rename` 成 `EAP_Asset_<index>` 并写同名
  `component_tags`；读回按 tag 找组件，不依赖创建顺序。重跑复用同 tag 组件、先 `clear_instances()`
  再落，asset 变少时清掉多余组件（幂等）。
- **合成 fixture 的网格是占位**：`/Game/EAP/PCG/Multi/Meshes/SM_Prop_A01..A20` 在 UE 里不存在，
  `--multi` 会设 `EAP_ALLOW_SYNTHETIC_MESH=1`，由落位端从 `/Engine/BasicShapes/*` 复制派生；
  真实链路（glb / 引擎自带网格）走不到这条分支。

## 三之七、真实双树种混排：conifer + broadleaf 同一份散点，1892 点（v1.5）

v1.4 的多资产链路已经证明"机制"能跑（20 个 BasicShapes 占位），这一格换成**真实资产**：
`assets/raw/trees/conifer.glb`（3 370 tris / 10.236 m）+
`assets/raw/trees/broadleaf.glb`（3 505 tris / 9.425 m）放进同一份散点，
按 `species` 分组各出一条 asset，UE 里落成 2 个 ISM（`EAP_Asset_0` / `EAP_Asset_1`）。

```
docs/pcg_multi_species_fixture.json（固定 seed=20261123，1892 点：conifer 1324 / broadleaf 568）
   ↓ tree_to_manifest.py --species-pair conifer,broadleaf
schemas/tree_manifest_multi.json（2 assets / 1892 placements / errors=0）
   ↓ place_manifest.py（真实 glb → Interchange 导入 → 每 asset 一个 ISM）
   ↓ verify() 新进程读回：数量 + transform + 材质 override
```

```powershell
# 1) 合成双树种散点（真实资产元数据从 glb 二进制复算，固定 seed，两次生成 SHA256 一致）
python tools/make_multi_species_fixture.py --out docs/pcg_multi_species_fixture.json

# 2) 双树种 manifest（每个 species 一条 asset，各自带 placements）
python tools/tree_to_manifest.py --scatter docs/pcg_multi_species_fixture.json `
  --out schemas/tree_manifest_multi.json --species-pair conifer,broadleaf
python tools/validate_manifest.py schemas/tree_manifest_multi.json   # assets=2 errors=0 warnings=0

# 3) 真实双树种 UE 全链路：17 条断言（7 纯 Python + 10 UE）
python tools/run_pcg_chain.py --real-multi --expect-assets 2 --expect-placements 1892
# 期望末行：CHAIN PASS assets=2 placements=1892
```

实测（2026-11-23）：fixture `points=1892`（conifer 1 324 / broadleaf 568，两次生成 SHA256 一致）；
manifest `assets=2 placements=1892`；UE 落位 `placed=1892/1892`，两个 ISM 分别指向
`/Game/EAP/Trees/conifer/...` 与 `/Game/EAP/Trees/broadleaf/...`；读回 `ok=True matched=2/2`，
transform 最大误差 0；两个网格高度 10.236 m / 9.425 m 与 glTF 一致（<5 cm）；两树种材质
metallic=0、usage 标记齐全、alpha 槽为 MASKED。

设计要点：

- **散点的 t 是树根底部**：fixture 写 `scatterOrigin=tree_base`，双树种 manifest 不再扣 Cube 半高；
  老 `--tree` 链路（UE PCG 散点，t=Cube 中心）仍按 0.500 m 补偿，`--species conifer` 行为不变
  （重跑 `tree_manifest.json` 除 `pipelineId` 日期外逐字段一致）。
- **不碰占位网格**：`--real-multi` 的落位环境不设 `EAP_ALLOW_SYNTHETIC_MESH`，两条 asset 都是
  `model.source=file`；断言里显式检查网格路径不含 `BasicShapes`。
- **复用同一套多资产落位**：不新增第二个落位脚本，仍是 `place_manifest.py` 的 `_place_multi`；
  每个 asset 走一遍 glb 导入 + 材质规范化（MIC 按 asset id 命名，避免两个树种互相覆盖）。
- **`--multi` 原样保留**：合成 20 资产链路与它的 13 条断言不动，继续当"机制"回归。

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

### 四之二、Layer 1 链路验收（现行口径：`--multi` 13 条 + `--real-multi` 17/18 条）

> **现行判据（v1.5）**：`python tools/run_pcg_chain.py --multi --expect-assets 20` = **13 条**；
> `python tools/run_pcg_chain.py --real-multi --expect-assets 2 --expect-placements 1892` = **17 条**，
> 再加 `--with-compile` = **18 条**。
> 下表 1–9 是 Layer 1 链路的公共判据（双树种链路同样覆盖）；**10–16 属于 `--tree`（单树种全量替换），
> 已于 2026-11-23 降级为历史链路**（原因见 §五 最后一条），数字保留作历史记录，不再作为验收门槛。

| # | 判据 | 实测（2026-11-22 17:33 / 18:0x / 18:1x） |
|:--:|---|---|
| 1 | 导出散点数 == 图生成实例数 | 1892 == 1892 |
| 2 | 导出散点数 == ISM 实例数之和 | 1892 == 1892 |
| 3 | 导出散点数 == `manifest.placements.count` | 1892 == 1892 |
| 4 | `manifest.placements` == UE 消费端 dry_run 报告里的 placements | 1892 == 1892 |
| 5 | `manifest.placements` == 落位实例数（UE ISM） | 1892 == 1892 |
| 6 | 新进程重载关卡读回实例数 == placements | 1892 == 1892（持久化，不是内存假象） |
| 7 | 落位实例 bbox == manifest items bbox | 误差 0.0000 m（阈值 1 cm） |
| 8 | 落位实例逐条 transform == manifest items | 0/1892 不匹配（阈值 1 cm / 0.05°） |
| 9 | 落位地块跨度 == 200 m（±15%） | X=196.6 m / Y=191.6 m |
| 10 | `--tree`（历史）：落位网格是树资产（非引擎 Cube） | `/Game/EAP/Trees/conifer/StaticMeshes/conifer.conifer` |
| 11 | `--tree`（历史）：UE 侧树高 == glTF 树高（<5 cm） | UE=10.236 m / glTF=10.236 m |
| 12 | `--tree`（历史）：材质槽已规范化（metallic=0 且 usage 标记齐全） | 2 槽 `['opaque', 'masked']`，三项 usage 全 True |
| 13 | `--tree`（历史）：alpha 材质已从 BLEND 转 MASKED | `BLEND_OPAQUE` + `BLEND_MASKED` |
| 14 | `--tree`（历史）：落位组件材质覆盖 = EAP 材质资产 | `MI_EAP_conifer_0_opaque_n` / `MI_EAP_conifer_1_masked`（新进程读回仍在） |
| 15 | `--tree`（历史）：材质贴图参数已生效（BaseColor 挂上目标贴图） | `base_color_texture` / `base_color_texture1` |
| 16 | `--tree`（历史）：材质编译通过（带 RHI 复检，无 `Failed to compile`） | failed=0（修好白模事故后） |

产物：`docs/pcg_forest_pilot.json`（1189 KB，图参数 + 1892 条散点）、`schemas/pcg_manifest.json`（626 KB）、
`docs/placed_pilot.json`（436 KB）、`docs/placed_verify.json`（435 KB）；
换树链路另有 `schemas/tree_manifest.json`、`docs/placed_tree.json`、`docs/placed_tree_verify.json`。

## 五、阶段一未覆盖（写在明处，别在面试里含糊）

- **轴心（Pivot）**：UE Python API **没有**直接改 StaticMesh 轴心的接口。本仓库的做法是把 `pivot.mode` + `pivot.offsetM` 写进 manifest，在**导出端**（DCC/glTF 节点变换）执行；消费端只做校验与告警。Unity 端同理由 Prefab 根节点补偿。
- **Unity 的粗糙度通道**：URP 的 `_MetallicGlossMap` 是 R=Metallic、A=Smoothness，**Smoothness = 1 − Roughness**。manifest 里显式写 `channelPacking`，转换在消费端完成（不在导出端预乘），避免两端各算一遍。
- **贴图压缩格式**：UE 的 `TC_Normalmap` / Unity 的 crunch 等属于平台设置，本阶段只写进 manifest 的期望值，不保证跨机器一致。
- **LOD/HLOD**：manifest 里记录 `lods[]`，本阶段只校验不生成。
- **placements 落位只落 transform（v1.1）**：`place_manifest.py` 把 1892 条落成 ISM 实例并验证坐标/旋转/缩放，
  但 manifest 里的材质、碰撞、LOD 字段本阶段不落；v1.4 起落位支持多资产（每 asset 一个 ISM），
  但材质规范化只对文件型 glb（`model.source=file`）生效。
- **给关卡 Actor 加组件的坑（v1.1）**：`unreal.new_object(InstancedStaticMeshComponent, outer=actor)` 建出来的组件
  不在 `OwnedComponents`，存关卡后会被丢弃；必须用 `SubobjectDataSubsystem.add_new_subobject`（Details→Add Component 的 API）。
- **PCG 点级 metadata 读不到（v1.1）**：`get_generated_graph_output()` 能拿到 `PCGDataCollection`（tagged=1），但点级 attribute（密度/seed）没有暴露的 Python 访问器。所以出口以**实例 transform** 为准——实例是最终结果，反而更可信。
- **Unity 端跳过引擎内置网格（v1.1）**：`model.source=engine`（如 `/Engine/BasicShapes/Cube`）没有 glb，Unity 不适用；只有文件型资产走双端。
- **无头导出依赖本机路径（v1.1）**：`run_pcg_chain.py --ue/--project` 可覆盖，默认 `C:\Program Files\Epic Games\UE_5.7` + `D:\UE_Projects\VegetationPCG`。
- **材质编译必须带 RHI 复检（v1.3）**：`-nullrhi` 下 UE 不真正编译材质，材质图写错了也照样 PASS；
  现在链路末尾多跑一次带 RHI 的无头复检，抓 `Failed to compile Material`。**白模事故就是这么漏过去的。**
- **树资产材质已规范化，但仍有视觉短板（v1.3）**：叶子已 `BLEND → MASKED`（clip 0.33、双面），
  两个槽的 metallic 都清零、usage 标记齐全；但**贴图仍是 Interchange 的 `TC_DEFAULT` 压缩**（蒙版用
  `TC_MASKED` 边缘更干净）、roughness 统一 0.9 是拍脑袋值、粗糙度/金属度还没走贴图。要出图得再调一轮。
- **跑链前必须关掉 UE 编辑器（v1.3 实测）**：编辑器打开目标关卡时，无头落位写盘会被 Windows 文件锁挡住
  （`LogFileManager: Error Code 32` → `Failed to move ... to temp dir`），`save_current_level()` 返回 False、
  材质覆盖不落盘。**先关编辑器再跑链**，否则会拿到"看着 PASS 但没存住"的假结果。
- **UE 侧三角面数要看清口径（v1.2）**：glTF 源数据 conifer 3 370 面，导入 UE 后 `get_num_triangles(0)` 报 1 414
  （broadleaf 3 505 → 2 354）——返回的是 **Nanite fallback** 简化网格。引用数字时写明是哪一侧。
- **树资产没有 LOD（v1.2）**：两棵树都只有 1 个 LOD（3 370 / 3 505 面，符合"低模无 LOD"门槛），
  1892 棵全量无 LOD 只能靠 Nanite 与实例剔除撑；LOD 链是下一项工作。
- **真实双树种混排已接通（v1.5）**：`--real-multi` 用 `conifer.glb` + `broadleaf.glb` 同一份 1892 点散点，
  UE 落成 2 个 ISM 并读回（17/17 断言）。但**散点仍是合成的**（固定 seed，只保证可复现，不代表真实 PCG 图输出），
  且只支持 2 个树种 / 单块 200×200 m 地块；`--tree`（单树种全量替换）自 v1.5 起降级为历史链路（见 §五）。
- **多资产 fixture 是合成的（v1.4）**：`docs/pcg_multi_fixture.json` 由 `tools/make_multi_asset_fixture.py`
  按固定 seed 生成，20 个网格名不对应真实美术资产；UE 侧的 20 个网格是 `--multi` 从
  `/Engine/BasicShapes/*` 复制出来的占位（`EAP_ALLOW_SYNTHETIC_MESH=1`），只用于验证多资产链路本身。
  `--multi` 与它的 13 条断言在 v1.5 原样保留，专门当"机制"回归；真实资产走 `--real-multi`。
- **多资产链路不跑 UE 消费端 dry-run（v1.4）**：`--multi` 只做 fixture → manifest → validate →
  落位 → 读回；`consume_manifest` 的多资产读回仍由默认/`--tree` 链路覆盖（当前都是单 asset）。
- **多资产读回按 tag 找组件（v1.4）**：组件名靠 `rename` + `component_tags` 固定为 `EAP_Asset_<i>`；
  如果手工在编辑器里改了 tag/名字，`verify()` 会找不到该 asset（表现为 count=0，链路 FAIL 而不是静默通过）。
- **落位关卡不带灯光与地面（v1.2）**：`PlacedTrees` 由无头新建，只有 `EAP_Placements`。灯光/天空/地面
  （`place_manifest.stage()`）**只能在真实编辑器里跑**——无头加 SkyAtmosphere/SkyLight 会
  `EXCEPTION_INT_DIVIDE_BY_ZERO` 崩掉（2026-11-22 实测）。
- **Unity 端还没实跑树资产（v1.2）**：`tree_manifest.json` 已经有 `engineTargets.Unity`（`Assets/EAP/Trees`），
  但 Unity 侧导入尚未执行（计划 2026-11-25 跑空工程验证）。
- **`--with-compile` 是严格模式（v1.5）**：`--real-multi` 默认不跑带 RHI 的材质编译复检；加上 `--with-compile`
  后判据是「编译子进程 `exit == 0` **且**日志无 `Failed to compile Material`，**且**证据里的 MIC 数
  == manifest 材质槽位总数」三条件同时满足。**进程崩溃就是 CHAIN FAIL**（不是 SKIP、更不是 PASS），
  没有任何"忽略 exit=3"的开关；证据里的 `shaderCompiled` 只是弱信号，不单独作数。
  2026-11-23 本机实测 18/18 PASS（exit=0，6 个材质、MIC 4/4）；同一判据在 lead 机曾遇
  `Out of video memory` / `Ran out of memory`（固定 16 GB 页面文件、编辑器提交约 9 GB、commit limit 47.4 GB），
  属内存环境问题，换环境/扩大页面文件后复验。
- **`--tree` 已降级为历史链路（v1.5，2026-11-23）**：`--tree`（单树种全量替换）**不再作为验收回归门槛**，
  代码与 16 条断言原样保留、随时可跑；现行验收口径是 `--multi` 13 条 + `--real-multi` 17 条
  （加 `--with-compile` 时 18 条）。降级原因是**环境事件而不是代码回归**：它的数据源关卡
  `/Game/Untitled`（UE PCG 散点，200 m / 1892 点）在 2026-11-23 19:43 清理残留时被误删；从
  `Saved/EAP/backup/level_20261122-163435` 恢复出来的是 11-22 16:34 的旧状态——只读导出探针实测
  `instanceCount=233`、跨度约 47×49 m，不是 200 m / 1892 点；并且 `-nullrhi -unattended` 下无头触发
  `PCGComponent.cleanup + generate` 是**空操作**（实例 233→233 未变），真正重算必须在编辑器里做，
  而旧备份里没有 200 m 的体积缩放参数，无法复原 1892 点。若要恢复这条回归：先在编辑器里重建
  200 m 地块并生成散点，再重跑 `--tree --expect-span-m 200`（16 条断言一字未改）。

## 六、下一步（阶段二/三）

- 阶段二 · 出图质量：贴图压缩改 `TC_MASKED`、roughness/金属度走贴图、必要时换更写实的树（当前两棵是低模风格化）；
  双树种混排已在 v1.5 接通（`--real-multi`），下一步是把它接到**真实 PCG 图输出**而不是合成散点
- 阶段二 · Unity 端实跑同一份 `tree_manifest.json`（glb + placements 双端一致性）
- 阶段二 · 落位扩展到材质/碰撞/LOD：现在只落 transform，manifest 里其它字段还没在 UE 侧生效
- 阶段二 · GUI 接 ComfyUI 客户端（GUI 阶段二的 QThread 异步 + 进度回填）
- 阶段三：`trimesh` + `numpy` 坐标转换（Y-up↔Z-up、cm↔m）与合规拦截（与 `tools/validate_manifest.py` 合并）
- 阶段四（作品集包装）：量化三段时间——**导入耗时 / 材质实例化耗时 / 碰撞生成耗时**，各自单测 20 个资产取中位数；把"10 分钟/个 → X 秒/个"写成三段可复现的表
