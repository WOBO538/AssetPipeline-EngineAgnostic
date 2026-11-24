# 五层解耦架构 · 主线计划存档（2026-11-24）

> 来源：用户 2026-11-24 19:00 在会话里粘贴的计划原文（项目代号《基于AIGC与PCG的多引擎资产管线系统 Engine-Agnostic Pipeline》）。
> 用途：**作品集主线架构的唯一存档**。以后讨论"串联/成环"先读这份，别再各说各的。
> 项目对应：本架构 = 作品集 **NO.10 = EAP（`D:\Projects\AssetPipeline-EngineAgnostic`）**；生成端对应 **NO.01 = UE5-AI-Asset-Pipeline（V3）** 与 **NO.07 = 宝箱**。
> lead 注解（2026-11-24）：以下"原文"逐字保留；"现状/缺口/纠正"是 lead 按今天代码实际状态补的。

---

## 一、计划原文（用户粘贴，逐字保留）

**📄 项目代号：《基于AIGC与PCG的多引擎资产管线系统 (Engine-Agnostic Pipeline)》**

### 🎯 一、项目愿景与定位

本项目旨在构建一套引擎无关（Engine-Agnostic）的自动化资产生产线。前端结合程序化生成（PCG）与生成式AI（AIGC），中台通过Python进行数据清洗与坐标转化，后端提供虚幻引擎（UE5）与Unity双端的自动化消费适配器。目标是在作品集中展现"全链路工程化能力"与"跨引擎管线架构能力"。

### 🏗️ 二、核心系统架构（五层解耦模型）

**Layer 1: 规则与生成层 (Generation Layer)**

- 引擎端 PCG：在 UE5 中使用 PCG 框架，基于坡度、海拔等规则，生成基础白模（占位符盒子/基础网格）。支持泊松盘采样（Poisson Disk Sampling）确保分布均匀。
- AIGC 层 (ComfyUI)：
  - 2D 材质流：接收白模截图或 Prompt，输出 5 张标准 PBR 贴图（BaseColor, Normal, Roughness, Metallic, AO）。
  - 3D 模型流：通过 Tripo3D/Meshy 等 API（或本地 TripoSR），将提示词转为基础 .glb / .obj 模型。

**Layer 2: 数据流转与转换层 (Middleware Layer - Python)**

- 通信协议：使用 HTTP RESTful 接口，向上对接 ComfyUI 的 API，向下接收引擎指令。
- 格式中转（核心）：
  - 统一使用 glTF 2.0 作为 3D 模型的中间标准格式。
  - 空间自适应模块：将左/右手坐标系、Z-up/Y-up、cm/m 单位进行矩阵转换。
  - 元数据封装：生成标准 JSON 清单（Manifest），记录模型路径、贴图通道映射、PBR 材质参数。

**Layer 3: AI 合规审查层 (Validation Layer)**

- 拓扑与尺寸审查：通过 Python 脚本（结合 trimesh 等库）检测模型面数、法线朝向、包围盒尺寸（强制匹配目标引擎单位）。
- 贴图通道审查：校验生成的 PBR 贴图是否完整，尺寸是否为 2 的 N 次幂（如 1024x1024）。
- 拦截机制：不合规的资产直接打回 Layer 1 重做，合规资产放行至 Layer 4。

**Layer 4: 引擎消费适配层 (Consumer Layer)**

- UE5 消费端 (Python)：读取 JSON，通过 Unreal Python API 自动创建母材质与材质实例（MIC），自动分配贴图通道，生成简单碰撞，并根据包围盒自动设置轴心（Pivot）。
- Unity 消费端 (C# Editor)：读取同一份 JSON，通过 AssetDatabase 自动创建 Prefab，将贴图赋给 Standard/URP Shader，自动挂载基础 Collider。

**Layer 5: 表现与性能层 (VFX & Performance)**

- 数据驱动特效：在引擎中绑定 Niagara/VFX Graph，将材质的参数或场景变量（如风速、亮度）接入特效系统。
- 性能规范：对批量生成的资产应用 HLOD 与 LOD 规则，限制远距离资产的 Draw Call。

### 🛠️ 三、技术栈指定

- 生成端：ComfyUI (Python API), UE5 PCG Framework
- 中台：Python 3.10+，requests, trimesh, numpy (矩阵运算)
- UE端：Unreal Python API
- Unity端：C# (Unity Editor Scripting)

---

## 二、现状对照（2026-11-24，v1.13）

| 层 | 状态 | 证据 / 说明 |
|---|---|---|
| Layer 1 · UE PCG | 🟡 部分 | 合成散点 1,892 点已跑通（`docs/pcg_forest_pilot.json`、`--real-multi`）；真实 PCG Graph 的坡度/海拔规则在 UE 图里（图参数已导出），但**泊松盘采样**未在 EAP 链路里验证 |
| Layer 1 · AIGC 2D 材质流 | ❌ 未接 | 现有贴图是 glb 内嵌 BaseColor/Normal；roughness 是**程序化亮度映射**（v1.6 三张 PNG），不是 ComfyUI 产物；Metallic/AO 无独立贴图 |
| Layer 1 · AIGC 3D 模型流 | ❌ 未接 | Tripo/Meshy/TripoSR 属 **NO.07 宝箱线**，与 EAP 之间没有接口 |
| Layer 2 · 中台 | ✅ 完成 | manifest 契约（`manifestVersion 1.2`）、glTF 中转、UE cm/Z-up vs Unity Y-up/左手转换、双端路径 |
| Layer 3 · 合规审查 | ✅ 完成（规则版） | `test_validator.py` 拦截 9/9；`--lod-check` 4/4、`--unity-materials-check` 4/4；**AI 版审查未做**（见 §四 NO.01） |
| Layer 4 · UE 消费 | ✅ 完成 | 材质/MIC/三档压缩/roughness 贴图/ISM 落位/碰撞（BlockAll 1,892） |
| Layer 4 · Unity 消费 | ✅ 完成 | Prefab/双材质槽（树叶 alpha 裁 0.33）/Collider/LODGroup/1,892 实例；**逐实例对账 1,892 条**（maxPos 0 / maxYaw 0 / maxScale 1e-6） |
| Layer 5 · LOD | ✅ 完成 | 双端 4 级（`--lod-check` 4/4），LOD0 与源 glb 对顶点/材质/UV |
| Layer 5 · 性能 | 🟡 部分 | 三段性能表已采（`docs/perf_20261124_141714.json` + `docs/perf_report.md`）；**冷/热 DDC 未分离已如实标注**；只记录，**未回灌生成参数** |
| Layer 5 · HLOD / Niagara | ❌ 未做 | README §五 已写明"不做 Layer 5 的 VFX/HLOD（加分项，不是资格项）" |

## 三、原文里三处要纠正（防面试翻车）

1. **"根据包围盒自动设置轴心（Pivot）"** —— UE Python **没有**改 StaticMesh 轴心的 API。EAP 实际做法：manifest 记录 `pivot.mode` + `pivot.offsetM`，在**导出端**（DCC/glTF 节点）执行，消费端只校验/告警（README §五）。计划里不能当已实现。
2. **"HTTP RESTful 接口"** —— 现有实现是 **CLI/无头批处理 + 文件契约（manifest）**，没有服务端。建议写成"文件契约 + 可替换传输层"；否则会被问服务端、并发、鉴权。
3. **"输出 5 张标准 PBR 贴图"** —— 是目标不是现状：当前只有 BaseColor/Normal 贴图，Metallic=0 标量、AO 无独立贴图。

## 四、NO.01（AI 增强型资产管线）在环里的位置

- **NO.01 = V3 资产智能管线 = `D:\Projects\UE5-AI-Asset-Pipeline`**（310 资产、外部流转层 0.21 s、演示视频 BV1aP436WEw9）。README §一 的定位：NO.01 是"单引擎 + 规则/AI 分类"，EAP 是"跨引擎 + PCG 生成 + 合规拦截"——**同一条线的两代**。
- **这份五层计划"功能上"包含了它**：NO.01 的 AI 分类/分析属于 Layer 1（生成/增强）与 Layer 3（AI 审查）的范畴；但**"工程上"没把它作为一环接进来**：
  - EAP 当前输入是 glb（真实树/合成盒子），**不是 NO.01 的 AI 产物**；
  - NO.01 的 310 资产清单、AI 分类器、0.21 s 外部流转层，**都不在 EAP 的链里**；
  - NO.01 是 UE 单引擎，EAP 是双引擎，接口还没定。
- **最自然的接法（成环的关键一环）**：
  1. NO.01 输出一份**资产清单 + 贴图包**（它已有 310 资产的批处理能力）；
  2. 新写 `ingest_aigc.py`：把 NO.01（或 ComfyUI）的产物转成 EAP manifest → 过 Layer 3 校验 → 双端消费；
  3. 再把 NO.01 的 **AI 分类/坏资产识别**作为 Layer 3 的前置筛（比纯规则更早拦截，正好补"AI 版审查"）。
- 这样"环"才闭合：**NO.01/ComfyUI（AI 生成与增强）→ ingest → manifest → 校验 → UE/Unity 消费 → 性能表 → 回灌生成参数**。

## 五、循环还差的两条连接线（lead 判断）

| 回路 | 缺什么 | 最小可做版本 |
|---|---|---|
| **A. 生成 → 中台** | 生成端（NO.01 / ComfyUI）产物没有入口生成 manifest（目前 manifest 由 `tools/tree_to_manifest.py` 从 glb 生成） | `ingest_aigc.py`：读资产/贴图目录 → 出 manifest → `validate_manifest.py` → `--unity-check`/`--real-multi` |
| **B. 性能 → 生成** | 性能表只是记录，没有回灌到 LOD/预算/生成参数 | 用 v1.10 性能表定 LOD 比例与预算阈值，改一次生成参数再跑一次，形成"数据决定参数" |

## 六、与日历的关系（2026-11-22 三项目计划）

- 12-15 前**材料先保**（一页纸/数据表/简历同步）；12-31 是 **v4 手写管线硬闸**；
- 因此回路 A（AIGC 接入）**不排在 12 月**，最早 2027-01；
- 材料阶段正确做法：把本文档的三色表（已实现/部分/未实现）写进一页纸与技术文档，**只写已实现的**，未实现标为下一阶段。
