# manifest.json 规范（v1.0）

> 一份 manifest = 一个批次（batch）的资产清单。**双端消费端只读这一份文件**，不允许各端自己扫描目录。
> 版本策略：字段只增不改语义；破坏性变更抬 `manifestVersion` 次版本号；消费端遇到不认识的主版本号必须**拒绝执行**。

## 一、顶层结构

```jsonc
{
  "manifestVersion": "1.0",
  "pipelineId": "eap-20261122-0001",     // 批次唯一 ID，用于日志关联
  "generatedAt": "2026-11-22T08:00:00+08:00",
  "source": {                             // 这批资产怎么来的
    "kind": "pcg",                        // pcg | aigc | manual | mixed
    "tool": "ue-pcg-framework",           // ue-pcg-framework | comfyui | blender | ...
    "workflowId": "pcg_forest_v3",
    "seed": 20261122
  },
  "units": "m",                           // 本批资产的统一单位（m | cm）
  "assets": [ /* 见下 */ ]
}
```

## 二、单个资产（assets[]）

| 字段 | 必填 | 说明 |
|---|:--:|---|
| `id` | ✅ | 引擎内唯一标识，建议 `SM_<Name>_<NN>`；**双端的资产名都用它** |
| `name` | ✅ | 展示名（允许中文，仅用于日志/报告） |
| `category` | ✅ | 分类路径，如 `env/rock`、`prop/chest`；消费端据此建目录 |
| `type` | ✅ | `static_mesh` \| `skinned_mesh` \| `material_only` |
| `model` | ✅ | `{path, format, unit, upAxis, forwardAxis, boundingBoxM{min,max}, triCount}` |
| `textures` | ✅ | 键必须是 PBR 标准通道：`BaseColor` `Normal` `Roughness` `Metallic` `AO` `ORM` `Height`；每项 `{path, srgb, size[2], channels}` |
| `material` | ✅ | `{template, params{}, channelPacking{}}` |
| `collision` | ⬜ | `{mode: auto_convex\|box\|none}`，默认 `box` |
| `pivot` | ⬜ | `{mode: bbox_center\|center_bottom\|none, offsetM:[x,y,z]}` |
| `lods` | ⬜ | `[{level, triCount, path?}]` |
| `engineTargets` | ✅ | `{UE5:{destPath, ...}, Unity:{destPath, ...}}`，见下 |
| `checks` | ⬜ | 生成/审查阶段写入的检查记录 `[{rule, pass, value}]`，消费端只读不改 |

## 三、引擎映射表（**这是全项目最容易出错的地方**）

### 3.1 贴图通道

| 语义 | UE5 材质参数 | Unity URP 属性 | 备注 |
|---|---|---|---|
| BaseColor | `BaseColor` | `_BaseMap` | 吃 sRGB |
| Normal | `Normal` | `_BumpMap` | **不吃 sRGB**，需 NormalMap 导入类型 |
| ORM（Occlusion/Roughness/Metallic 三合一） | `ORM` | `_MetallicGlossMap` | 通道布局不同，见下 |
| Roughness / Metallic（分开的图） | `Roughness` / `Metallic` | `_MetallicGlossMap`（需打包） | 分开的图在 Unity 端要先合成 |
| AO（单独图） | `AO` | `_OcclusionMap` | |
| Height | `Height` | `_ParallaxMap` | 仅视差材质用 |

**通道布局差异（务必写进 manifest 的 `channelPacking`）**

| 引擎 | ORM 期望布局 |
|---|---|
| UE5（本项目） | `R=Occlusion, G=Roughness, B=Metallic` |
| Unity URP | `R=Metallic, G=Occlusion, A=Smoothness`，且 **Smoothness = 1 − Roughness** |

→ 结论：**不要在中台预乘某一端的布局**。manifest 记录源布局（`channelPacking.source`），消费端各自重排。

### 3.2 坐标与单位

| 项 | UE5 | Unity | 转换位置 |
|---|---|---|---|
| 上方向 | Z-up | Y-up | 中台（阶段三，`trimesh` 矩阵） |
| 前方向 | +X | +Z | 同上 |
| 单位 | 1 uu = 1 cm | 1 unit = 1 m | 同上 |
| 手性 | 左手 | 左手 | 一致，无需镜像 |

manifest 里的 `model.boundingBoxM` **一律用米**，由中台换算；消费端不再做单位推断。

### 3.3 材质参数命名

`material.template` 指向目标引擎里的母材质/Material 名：

```jsonc
"material": {
  "template": "M_EAP_StandardPBR",              // UE: /Game/EAP/Materials/M_EAP_StandardPBR
  "unityShader": "Universal Render Pipeline/Lit", // Unity 端
  "params": { "RoughnessScale": 1.0, "MetallicScale": 1.0, "NormalStrength": 1.0 },
  "channelPacking": { "source": "UE", "target": { "UE": "keep", "Unity": "repack_orm_to_mg" } }
}
```

## 四、示例的最小资产（复制即可测）

```json
{
  "id": "SM_Rock_A01",
  "name": "岩石 A01",
  "category": "env/rock",
  "type": "static_mesh",
  "model": {
    "path": "assets/models/SM_Rock_A01.glb",
    "format": "gltf2.0",
    "unit": "m",
    "upAxis": "Z",
    "forwardAxis": "X",
    "boundingBoxM": { "min": [-0.8, -0.6, 0.0], "max": [0.8, 0.7, 1.2] },
    "triCount": 1840
  },
  "textures": {
    "BaseColor": { "path": "assets/textures/T_Rock_A01_BaseColor.png", "srgb": true, "size": [1024, 1024], "channels": "RGB" },
    "Normal":    { "path": "assets/textures/T_Rock_A01_Normal.png",    "srgb": false, "size": [1024, 1024], "channels": "RGB" },
    "ORM":       { "path": "assets/textures/T_Rock_A01_ORM.png",       "srgb": false, "size": [1024, 1024], "channels": "RGB" }
  },
  "material": {
    "template": "M_EAP_StandardPBR",
    "unityShader": "Universal Render Pipeline/Lit",
    "params": { "NormalStrength": 1.0 },
    "channelPacking": { "source": "UE", "target": { "UE": "keep", "Unity": "repack_orm_to_mg" } }
  },
  "collision": { "mode": "box" },
  "pivot": { "mode": "center_bottom", "offsetM": [0, 0, 0] },
  "engineTargets": {
    "UE5":   { "destPath": "/Game/EAP/Env/Rock" },
    "Unity": { "destPath": "Assets/EAP/Env/Rock" }
  }
}
```

## 五、五条硬约束（消费端必须校验，违反就整批拒绝）

1. `manifestVersion` 主版本不认识 → 拒绝。
2. `units` 不是 `m`/`cm`，或 `model.unit` 与顶层 `units` 冲突 → 拒绝。
3. `Normal` 贴图 `srgb=true` → 拒绝（这是误配，会整体偏亮）。
4. 贴图 `size` 不是 2 的幂 → 警告（UE/Unity 能跑，但会浪费显存/触发 NPOT 路径）。
5. `engineTargets` 缺目标端 → 该端跳过并在报告里记 `skipped_no_target`，不影响另一端。
