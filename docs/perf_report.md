# 性能表（v1.10）：导入 / 材质 / 碰撞 × 20 资产

> 生成：2026-11-24 14:17:16｜原始采样：`docs\perf_20261124_141714.json`
> **这些数字只对本机配置有效，换机器需重跑 `python tools/run_pcg_chain.py --perf`。**

## 机器与版本

| 项 | 值 |
|---|---|
| CPU | Intel(R) Core(TM) Ultra 9 275HX |
| 逻辑核 | 24 |
| RAM (GB) | 31.4 |
| GPU | Todesk Virtual Display Adapter; NVIDIA GeForce RTX 5060 Laptop GPU; Intel(R) Graphics |
| UE | UE 5.7.4 (CL 51494982, ++UE5+Release-5.7) |
| Unity | 2022.3.62f3c1 |
| Python | 3.12.10 |
| 采样时间 | 2026-11-24 14:17:16 |

## 方法说明

### 计时边界（与 `ue/Content/Python/perf_probe.py` / `unity/Editor/ManifestImporter.cs` 逐字对应）

| 段 | 从哪到哪 | 20 资产定义 |
|---|---|---|
| **A. 导入** | `t0` 紧贴 `import_asset_tasks([task])` 之前，`t1` 紧跟其返回之后；含 Interchange 翻译 glb、建 StaticMesh/贴图资产、`save=True` 落盘；不含材质规范化、不含 UE 启动、不含副本复制 | 同一份 conifer.glb 的 20 个字节级相同副本（`perf_import_00..19.glb`，SHA256 `56f88f78f38fc194`），导入到每样本唯一的 `/Game/EAP/Perf/<tag>/A/imp_XX` |
| **B. 材质** | `t0` 紧贴 `_normalize_materials(mesh, ...)` 之前，`t1` 紧跟其返回之后；含母材质复用（母材质已在本样本计时前预热）、MIC 创建、BaseColor/Normal 贴图参数、三档压缩设置、roughness 灰度贴图生成与导入、`save_loaded_asset` 落盘；不含 A 段导入 | A 段导入出来的 20 个资产，各 2 个材质槽（Bark: OPAQUE+Normal / Leaves: BLEND 无 Normal） |
| **C. 碰撞（Unity）** | 每个 prefab 副本从"实例化"到"加完 `MeshCollider(convex)` + `Physics.BakeMesh`"；含实例化/销毁副本、凸包烘焙；不含 prefab 副本复制、模型导入、材质。Unity 2022.3 的 `BakeMesh` 返回 void（无法回读成功与否）→ 以调用未抛异常 + `MeshCollider.convex`/`sharedMesh` 读回计数当证据 | 20 个 prefab 副本（conifer/broadleaf 各 10 份，从 v1.8 的真实 prefab 复制） |
| **C. 碰撞（UE）** | 无独立可测耗时 | 1,892 实例 ISM 的碰撞是组件开关 + profile 设置（一次性组件标志，无法稳定测出独立耗时），没有可比的三次采样 → **不编数字**；碰撞证据见 `docs/placed_tree_multi_verify.json`（v1.9 已验证 enabled/BlockAll/1892/meshHasCollision） |

### 20 资产的真实定义（坦白）

- A/B 的 20 个资产是**同一棵 conifer glb 的 20 个副本**，不是 20 个不同资产；它们字节级相同（SHA256 一致），DDC 键含源文件名所以是 20 个不同键，但几何/贴图工作量等价。
- A 段是**单进程 20 资产批处理**：第 1 个副本付完整翻译成本，第 2~20 个在进程内存 DDC 里共享第 1 个的结果。这是批处理口径的固有性质。
  （本次冷隔离未成功，见「冷/热 DDC 处理」——进程起点**仍能读到磁盘 DDC**，所以这批数字不是"从零冷启动"的成本。）
- C 段 Unity 的 20 个 prefab 是 2 个真实 prefab（conifer/broadleaf）各复制 10 份；凸包烘焙时每个副本用独立的网格实例（否则 2 个源网格的烘焙结果会被共享，耗时塌成 0）。

### 冷/热 DDC 处理

- DDC 目录（快照）：`C:\Users\ASUS\AppData\Local\UnrealEngine\Common\DerivedDataCache`、`D:\UECache`、`D:\UE_Projects\VegetationPCG\DerivedDataCache`
- **冷（本次未隔离成功）**：冷样本的 UE 进程命令带了 `-DDC-ForceMemoryCache`，预期"DDC 只驻内存、磁盘/共享 DDC 不读不写"；每个冷样本前后各拍一次 DDC 目录快照用来验证。
  **实测：3 次冷样本的 DDC bytes 持续增长（+23/+25/+24 KB，files 266 → 271），`-DDC-ForceMemoryCache` 没能隔离磁盘 DDC。**
  因此本次的"冷"样本并不是真冷，磁盘 DDC 依然被写入/读取。
- **热**：不带该 flag，走磁盘 DDC；3 次热采样前先跑一次**不计时**的热启动（只做 A 段）把 DDC 填满。
- **结论：冷未隔离成功（证据即下方快照表），A/B 的 cold/hot 两组实际都处于"磁盘 DDC 已热"的状态，两组差值在噪声内，不可当作冷热对比，只能当作 6 次重复采样。**
  快照表原样保留，作为"没做到就是没做到"的诚实记录；冷热差不做断言。

| 样本 | 模式 | flag | DDC files 前→后 | DDC bytes 前→后 | 结论 |
|---|---|---|---|---|---|
| import,material r1 | cold | `-DDC-ForceMemoryCache` | 266 → 266 | 680630606 → 680653670 | DDC 目录有变化（见数字） |
| import,material r2 | cold | `-DDC-ForceMemoryCache` | 266 → 269 | 680653670 → 680678986 | DDC 目录有变化（见数字） |
| import,material r3 | cold | `-DDC-ForceMemoryCache` | 269 → 271 | 680678986 → 680703286 | DDC 目录有变化（见数字） |
| import r0 | hot | `(none)` | 271 → 273 | 680703286 → 680727069 | 热：走磁盘 DDC（写入属预期） |
| import,material r1 | hot | `(none)` | 273 → 278 | 680727069 → 680752684 | 热：走磁盘 DDC（写入属预期） |
| import,material r2 | hot | `(none)` | 278 → 278 | 680752684 → 680776825 | 热：走磁盘 DDC（写入属预期） |
| import,material r3 | hot | `(none)` | 278 → 280 | 680776825 → 680801126 | 热：走磁盘 DDC（写入属预期） |

## 汇总表

> **注意：冷 DDC 未隔离成功（证据见上节快照表：3 次冷样本 DDC bytes +23/+25/+24 KB、files 266→271）。
> 因此 A/B 两组的 cold / hot 不是干净的冷热对比——两组实际都是"磁盘 DDC 已热"，差值在噪声内。
> 下表按原始采样保留 cold / hot 两行只为可追溯，**不要把它读成"冷 vs 热"**；同一段的 6 次采样应合起来看。**

| 段 | 状态 | n | min (s) | p50 (s) | max (s) |
|---|---|---|---|---|---|
| A_import | cold | 3 | 3.0564 | 3.1936 | 3.3945 |
| A_import | hot | 3 | 3.0580 | 3.0637 | 3.1052 |
| B_material | cold | 3 | 31.1837 | 31.5955 | 32.1345 |
| B_material | hot | 3 | 31.3557 | 31.5323 | 31.5606 |
| C_collision_unity | n/a | 3 | 0.1342 | 0.1371 | 0.1525 |

## 原始采样

| 段 | 状态 | round | duration_s | 备注 |
|---|---|---|---|---|
| A_import | cold | 1 | 3.3945 | 20 副本；首个 0.2403s / 其余 min 0.1562s |
| A_import | cold | 2 | 3.1936 | 20 副本；首个 0.2164s / 其余 min 0.1505s |
| A_import | cold | 3 | 3.0564 | 20 副本；首个 0.2141s / 其余 min 0.1444s |
| A_import | hot | 1 | 3.0580 | 20 副本；首个 0.2236s / 其余 min 0.1437s |
| A_import | hot | 2 | 3.0637 | 20 副本；首个 0.2158s / 其余 min 0.1442s |
| A_import | hot | 3 | 3.1052 | 20 副本；首个 0.2206s / 其余 min 0.1410s |
| B_material | cold | 1 | 31.5955 | 20 资产 × 2 槽；warmup(不计时) 1.95s |
| B_material | cold | 2 | 32.1345 | 20 资产 × 2 槽；warmup(不计时) 1.79s |
| B_material | cold | 3 | 31.1837 | 20 资产 × 2 槽；warmup(不计时) 1.71s |
| B_material | hot | 1 | 31.5606 | 20 资产 × 2 槽；warmup(不计时) 1.72s |
| B_material | hot | 2 | 31.5323 | 20 资产 × 2 槽；warmup(不计时) 1.72s |
| B_material | hot | 3 | 31.3557 | 20 资产 × 2 槽；warmup(不计时) 1.70s |
| C_collision_unity | n/a | 1 | 0.1525 | prefabs=20 colliders=20 convex=20 bakeCalls=20 bakeErrors=0 |
| C_collision_unity | n/a | 2 | 0.1342 | prefabs=20 colliders=20 convex=20 bakeCalls=20 bakeErrors=0 |
| C_collision_unity | n/a | 3 | 0.1371 | prefabs=20 colliders=20 convex=20 bakeCalls=20 bakeErrors=0 |

原始 JSON：`docs\perf_20261124_141714.json`（每次采样一行，含 per-asset 明细与机器信息）。

## 断言

| # | 断言 | 结果 |
|---|---|---|
| 1 | 三段 × 冷热 × 3 次采样齐全（A/B 各 3 冷 + 3 热；C 3 次，无 DDC 标 n/a） | PASS |
| 2 | 每次采样 duration_s > 0 | PASS |
| 3 | 汇总表 min ≤ p50 ≤ max 且样本非退化（防写死） | PASS |
| 4 | 报告含方法说明段 | PASS |
| 5 | 报告冷隔离结论与 DDC 快照数据一致 | PASS |

## 已知噪音源

- DDC 状态：冷样本尝试用 `-DDC-ForceMemoryCache` 隔离磁盘 DDC，**但本次未隔离成功（DDC 仍在增长，见快照表）**；因此 cold/hot 两组的差异属于 DDC 状态 + 普通运行噪声的混合，不能当成冷热差。
- 后台进程 / Windows Defender 对 `%TEMP%` 与 UE 资产的实时扫描。
- 笔记本温度墙 / 频率漂移：A/B 每样本约几十秒，长跑后半段可能降频。
- B 段的大头是 `_make_roughness_texture` 的**纯 Python** PNG 解码/缩放/编码（CPU 单线程），不是 UE 材质系统本身；换实现会显著改变 B 的数字。
- Unity 侧 C 段 3 轮在同一进程内连续跑（Unity 无 DDC/冷热语义），第 1 轮含首次资产加载，后两轮更热。
- `-nullrhi`：A/B 不编译渲染管线着色器；真实带 RHI 的首次导入会额外付 Nanite/着色器编译成本。

**声明：以上数字只对本机配置有效，换机器/换 UE·Unity 版本需重跑 `--perf`，不要跨机器比较。**

