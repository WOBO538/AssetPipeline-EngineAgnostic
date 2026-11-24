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

## B2. 换真实树资产链路（v1.2，`--tree`，第一次吃文件型 glTF）

复现：

```bash
python tools/run_pcg_chain.py --tree --expect-span-m 200
# 期望末行：CHAIN PASS points=1892 assets=1 placements=1892
# 依赖：assets/raw/trees/{conifer,broadleaf}.glb + source_assets.json（CC0，Poly Pizza/Quaternius）
```

| # | 判据 | 实测（2026-11-22 18:0x） |
|:--:|---|---|
| 1–8 | 同 B 组 1–8（散点/instanceCount/placements/落位/持久化/bbox/逐条 transform） | 全部 1892 == 1892，transform 0/1892 不匹配 |
| 9 | 落位地块跨度 == 200 m（±15%） | X=196.6 m / Y=191.6 m |
| 10 | 落位网格是树资产（非引擎 Cube） | `/Game/EAP/Trees/conifer/StaticMeshes/conifer.conifer` |
| 11 | UE 侧树高 == glTF 树高（<5 cm） | UE=10.236 m / glTF=10.236 m |
| 12 | 材质槽已规范化（metallic=0 且 usage 标记齐全） | 2 槽 `['opaque','masked']`，STATIC_MESH/ISM/NANITE 三项全 True |
| 13 | alpha 材质已从 BLEND 转 MASKED | 树皮 `BLEND_OPAQUE` / 叶子 `BLEND_MASKED`（clip 0.33、双面） |
| 14 | 落位组件材质覆盖 = EAP 材质资产 | `MI_EAP_conifer_0_opaque_n` / `MI_EAP_conifer_1_masked`（新进程读回仍在） |
| 15 | 材质贴图参数已生效（BaseColor 挂上目标贴图） | `base_color_texture` / `base_color_texture1` |
| 16 | 材质编译通过（带 RHI 复检，无 `Failed to compile`） | failed=0 |

产物与证据（换树链路）：

| 文件 | 说明 |
|---|---|
| `assets/raw/trees/source_assets.json` | 采集凭证：来源 URL / 作者 / 许可证 / 树高 / 面数 / 原点 / 校验器结果（模型 .glb 按 .gitignore 不入库） |
| `schemas/tree_manifest.json` | 中台产物：`model.source=file`、`format=gltf2.0`、双端 `engineTargets`、placements[1892]、`pivot.zOffsetM=0.5`、10 条 checks |
| `docs/placed_tree.json` | 落位证据：关卡 `/Game/EAP/PCG/PlacedTrees`、ISM 1892 棵 conifer、`meshInfo`（高 10.236 m / 2 材质） |
| `docs/placed_tree_verify.json` | 新进程重载读回：1892 条，网格路径与 bbox 与落位一致 |
| `docs/placed_tree_topdown.png` | 俯视图（树冠圆，读回 transform 绘制） |
| `docs/ue_trees_placed_20261123.png` | 引擎内实拍（2026-11-23 用户手操）：**规范化后**的 1892 棵绿树、6 Actor（对照图 `ue_trees_placed_20261122.png` 是规范化前版本） |
| `docs/ue_material_usage_warning_20261122.png` | `地图检测` 截图：材质缺 `bUsedWithNanite` / `bUsedWithInstancedStaticMeshes` 用法标记 |
| `Content/EAP/PCG/PlacedTrees.umap`（UE 工程内） | 落位关卡：1 Actor + 1 ISM（1892 棵 conifer），无灯光/地面（补灯需真实编辑器） |
| `Content/EAP/Materials/`（UE 工程内） | 规范化材质：`M_EAP_Foliage_Masked` / `M_EAP_Opaque` 母材质 + `MI_EAP_<资产>_<槽>_<kind>` 实例 |
| `assets/raw/trees/_previews/*.png` | 采集会话附的两棵树预览图（非引擎内实拍） |

**换树链路的已知限制**：无 LOD、单树种、UE 侧三角面口径为 Nanite fallback（1 414 / 源 3 370）、
Unity 端尚未实跑、`PlacedTrees` 关卡无灯光地面（需真实编辑器补）、贴图压缩仍是 `TC_DEFAULT`。

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
   网格现在有两套证据：引擎 Cube（B 组）与真实树 glb（B2 组）；视觉像不像森林仍是资产质量/材质规范问题。
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
13. **新建空关卡没有灯光/地面**：`new_level` 造出来的关卡只有空世界，视口里实例是黑的。补灯必须在**真实编辑器**里做——
    无头跑 `place_manifest.stage()` 会 `EXCEPTION_INT_DIVIDE_BY_ZERO` 崩溃（SkyAtmosphere/SkyLight 需要渲染管线，实测 2026-11-22）；
    而且 DirectionalLight 默认 Static，新关卡没烘焙时对物体无贡献，必须改成 Movable。
14. **无头能导 glTF（v1.2 实测）**：UE 5.7 `-nullrhi -unattended` 下 Interchange glTF 导入可用，单棵 0.4~1.2 s；
    资产落点固定为 `<destPath>/<文件名>/StaticMeshes/<文件名>`，材质由 Interchange 自动生成 MIC。
15. **轴心补偿（v1.2）**：散点 `t` 是引擎 Cube 的**中心**，树轴心在**树根底部**，必须把 `t.z` 减去 Cube 半高 0.5 m，
    否则 1892 棵树整体悬空 0.5 m。manifest 里记 `pivot.zOffsetM`，链路上有断言守着树高对账。
16. **UE 三角面口径（v1.2）**：glTF 源 3 370 面，导入后 `get_num_triangles(0)` 报 1 414（broadleaf 3 505 → 2 354），
    返回的是 Nanite fallback 网格。**不要混用两侧数字**，验收表统一引用 glTF 侧。
17. **树资产材质已规范化（v1.3 解决）**：叶子 `BLEND → MASKED`（clip 0.33、双面）、两槽 metallic 0.4 → 0、
    usage 标记齐全，全部由 `place_manifest._normalize_materials()` 无头完成，第 12–14 条断言守着。
18. **树资产无 LOD（v1.2）**：两棵树各只有 1 个 LOD，1892 棵全量靠 Nanite/实例剔除撑；LOD 链是下一项工作。
19. **导入材质用法标记已补齐（v1.3 解决）**：自建 EAP 母材质时用 `set_material_usage` 设了
    `MATUSAGE_STATIC_MESH / INSTANCED_STATIC_MESHES / NANITE`，派生 MIC 继承，原 Interchange 材质的告警不再适用。
20. **画面观感（v1.2 实拍结论）**：树成片、落地、投影方向一致；但叶片偏**荧光绿**、树皮无层次，
    与 `metallicFactor 0.4` + 叶子 `BLEND` 的预期缺陷吻合。**当前不主张画面质量，只主张管线连通。**
21. **跑链前必须关 UE 编辑器（v1.3 实测）**：编辑器占着 `PlacedTrees.umap` 时，无头保存关卡报
    `LogFileManager: Error Code 32` + `Failed to move ... to temp directory`，`save_current_level()` 返回 False，
    实例与材质覆盖都不落盘（但脚本其它断言仍会 PASS，属"假通过"）。所以落位前后要确认编辑器已关该关卡。
22. **贴图压缩仍是 TC_DEFAULT（v1.3）**：叶子 alpha 贴图（70.5% 全透明）配 MASKED 更该用 `TC_MASKED`，
    现在沿用 Interchange 的 `TC_DEFAULT`；边缘可能出现锯齿/渗色，出图前再改并重导。
23. **roughness 0.9 是拍脑袋值（v1.3）**：EAP 母材质把 roughness 设成常量 0.9、metallic 常量 0，
    没有走贴图（glb 本身也没 MR 贴图）。这是"去塑料感"的最小修法，不是最终材质方案。
24. **白模事故（v1.3，已修 + 已加防线）**：第一版母材质的法线采样节点没默认贴图，编译失败 →
    UE 回退默认白材质 → 1892 棵树全白。修法：没法线贴图的槽不建法线节点、BaseColor 也设默认贴图、
    母材质改名 `_v2`、清理旧材质（含探针残留 `M_EAP_Tree_Bark/Leaf`）。
    **更重要的教训**：`-nullrhi` 不真正编译材质，"材质挂上了"的断言全 PASS 也可能是白模；
    现在链路末尾用带 RHI 的进程复检关卡里实际用到的材质，抓 `Failed to compile Material` → 第 16 条断言。
25. **规范化后的实拍图（已补，v1.3 收尾）**：`docs/ue_trees_placed_20261123.png` 是材质规范化之后的
    绿树实拍（2026-11-23 用户手操），README 主图已换成它；`docs/ue_trees_placed_20261122.png`
    保留为规范化之前的对照图。

---

## C. 阶段一 6 条：2026-11-24 复核（v1.6 / v1.7 / v1.8 之后）

> 复核人：lead；复核方式：重跑命令 + 读回 JSON + 负向测试。**行号对应上面 A 组表格。**
> 现行门槛：`--multi` 13/13、`--real-multi` 20/20、`--with-compile` 21/21、`--unity-check` 4/4、`--unity-placements-check` 4/4。

| # | 判据 | 当前证据（2026-11-24 实测） | 状态 |
|:--:|---|---|:--:|
| 1 | 同一份 manifest 双端都能读，日志资产数一致 | 两端都读 `schemas/tree_manifest_multi.json`：UE 侧 `CHAIN PASS assets=2 placements=1892`；Unity 侧 `UNITY-CHECK PASS assets=2 prefabs=2 textures=4/4` | ✅ |
| 2 | UE 端自动建母材质 + MIC 并连好贴图 | v1.6 断言②③：MIC `Roughness` 参数后缀 `_Roughness`、标量 `RoughnessScale=0.9`、4/4 槽；贴图三档（`TC_Default`+sRGB / `TC_Normalmap`+线性 / `TC_Masks`+线性）；main=6 / total=10 张 | ✅ |
| 3 | Unity 端自动建 Material 并映射贴图 | v1.7 读回：shader=`Universal Render Pipeline/Lit`，`texturesApplied` BaseColor/Normal 全 true，路径全 `Assets/EAP/Trees/Textures/…` | ✅ |
| 4 | 法线贴图不吃 sRGB | UE：`TC_Normalmap` + `srgb=False`（代码 + 断言）；Unity：`TextureImporterType.NormalMap`（代码路径）；**缺两端 Inspector 截图**（非阻塞，有代码判据） | ⚠️ |
| 5 | 2 的幂/通道不通过时被拦住 | `python tools/test_validator.py` → `拦截成功 9/9`，exit=0（2026-11-24 复跑） | ✅ |
| 6 | 两端资产名/路径/尺寸一致 | Unity 读回 meshTriCount **3,370 / 3,505** == manifest；Prefab bounds 每轴误差 ≤2e-5 m；贴图路径全在 `Assets/` 下；**但还没有"逐资产三列对照表"**（见缺口 G3） | ⚠️ |

### Layer 1 / 换树链路（B、B2 组）现状

- B 组 1–9：`--real-multi` 今日 `CHAIN PASS assets=2 placements=1892`，1892==1892 全程成立；落位 bbox 误差 0.0000 m、逐条 transform 0/1892 不匹配。
- B2 组 10–16：v1.6 起并入 `--real-multi`（20 条断言）与 `--with-compile`（21 条）；树高 10.236 m、双树种 1324+568、材质规范化都在断言内。
- **跨引擎新增（v1.8）**：Unity 侧 1,892 实例落位 + 1892 Collider；坐标换算包围盒 vs UE 侧 `bboxPlacedM` 误差 **0.0000 m**。

## D. 真实缺口清单（2026-11-24）

| # | 缺口 | 影响 | 建议 |
|:--:|---|---|---|
| G1 | UE 侧落位只落 transform：ISM **无碰撞体、无 LOD** | 阶段一不阻塞；进游戏/演示会露 | 任务单 7 候选 |
| G2 | Unity 侧**朝向正确性无直接判据**（yaw 取反只靠换算前 bbox 交叉验证） | 视觉可能镜像 | 任务单 7：逐实例对账 |
| G3 | 双端"资产名/路径/尺寸一致"只有 triCount/bounds/贴图路径，**无三列对照表** | 面试被追问"怎么对账"时缺一页证据 | 任务单 7：导出双端对照表 |
| G4 | 法线 `sRGB` 两端 Inspector 截图未截 | 阶段一第 4 条只算 ⚠️ | 开编辑器时顺手截 |
| G5 | 性能表（导入 / 材质 / 碰撞 三段 × 20 资产）未做 | 加分项，非资格项 | 低优先 |
| G6 | 树资产各只有 1 个 LOD | 大规模场景性能 | ✅ v1.12 已补：Blender Decimate 3 级 → UE SM 4 级 + Unity LODGroup 4 级，`--lod-check` 4/4（只验数据，不验视觉切换） |
| G7 | roughness 合成区间窄（0.65–0.80），视觉差异弱 | 画面说服力 | 可选：拉大到 0.95 / 0.55（需同步 3 条断言与 4 张 SHA） |
