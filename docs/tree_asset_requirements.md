# 树资产采集要求（给负责下载资产的那个会话）

## 已采用资产（2026-11-22 到货，体检通过）

| 文件 | 树种 | 来源 | 许可 | 树高 | 三角面 | 备注 |
|---|---|---|---|---|---|---|
| `assets/raw/trees/conifer.glb` | pine（针叶） | Quaternius · Poly Pizza `79gmlLnweB` | CC0-1.0 | 10.2364 m | 3 370 | 已接入落位（1892 实例） |
| `assets/raw/trees/broadleaf.glb` | broadleaf（阔叶） | Quaternius · Poly Pizza `QVOop92WmG` | CC0-1.0 | 9.4252 m | 3 505 | 已入库，尚未接入落位（下一单元双树种混排） |

体检结论：原点均在树干底部（bbox min y = 0.0000）、贴图全内嵌、无外部 uri、Khronos 校验 0 error。
遗留问题：叶子材质 `BLEND`（应改 MASKED）、`metallicFactor 0.4`（应改 0）、无 LOD。

> 用途：EAP（Engine-Agnostic Pipeline）阶段一要做的最后一件事——把当前 200×200 m 地块里 1892 个「方块占位」换成**真实树资产**，验证整条管线（glTF 导入 → Python 中间件 → 校验 → UE5 + Unity 双端）能吃真实模型。
>
> 采集者不需要懂 UE。只要按本文交付，放进 `D:\Projects\AssetPipeline-EngineAgnostic\assets\raw\trees\`，后续由 lead 跑链路。

## 已采用资产（2026-11-22 落地）

| 类型 | 文件 | 来源 | 许可 | 高度 | 三角面 | 材质 / 贴图 | 原点 |
|---|---|---|---|---|---|---|---|
| 针叶（pine） | `conifer.glb` | Poly Pizza「Pine」/ Quaternius | CC0-1.0 | 10.24 m | 3,370 | 2 材质；3 张 1024² PNG 内嵌（含法线） | 树干底部中心（已归一化） |
| 阔叶 | `broadleaf.glb` | Poly Pizza「Tree」(CommonTree_3) / Quaternius | CC0-1.0 | 9.43 m | 3,505 | 2 材质；3 张 1024² PNG 内嵌（含法线） | 树干底部中心（已归一化） |

机器可读字段见 `assets/raw/trees/source_assets.json`：来源 URL、作者、CC0 证据、sha256、包围盒、三角面、贴图尺寸、原点归一化平移量、Khronos glTF Validator 结果都在里面。

- 两棵均为**单棵树、Y-up、米制、贴图内嵌、无外部依赖**；无 LOD（LOD0 远低于 2 万，按硬门槛 7 的"没有 LOD 的低模"合格）。
- 原始下载资产的树根底部在 y<0（约 -0.24 m），落地时已用**根节点平移**把树干底部中心对齐到原点；平移量写在 sidecar 的 `pivot_normalization.delta_m`，可复现。
- Khronos glTF Validator：**0 error**，各 1 条 `MESH_PRIMITIVE_GENERATED_TANGENT_SPACE` warning（无 TANGENT 属性，导入端会自行生成切线空间）。

---

## 一、为什么要求这么多

管线里每一环都有硬约束，资产不合要求不会"报错"，而是安静地错：

| 管线环节 | 对资产的真实约束 |
|---|---|
| Python 中间件 | 只解析 **glTF 2.0**；单位按**米**处理；按 **Y-up** 转 Z-up 给 UE |
| UE 落位 | 实例的**原点即树干底部**；原点错 = 1892 棵全部沉地或浮空 |
| 性能 | 1892 个实例，靠 LOD + 实例剔除扛住；无 LOD 会爆三角面 |
| 许可证 | 作品集要公开，README 得能写出每个资产的可追溯来源 |

所以下面第 2 节是**硬门槛**，不满足的请直接不要带回来，省得后面返工。

---

## 二、硬门槛（7 条，缺一不可）

| # | 要求 | 判定方式 |
|---|---|---|
| 1 | 许可证 **CC0 或 CC-BY**，可商用；附**来源 URL + 作者名** | sidecar JSON 里必须写清；NC（非商用）、ND（禁改）直接淘汰 |
| 2 | 格式 **glTF 2.0**：`.glb` 优先，`.gltf` + `.bin` 也可 | 文件头是二进制 glTF 或 JSON 里 `"asset":{"version":"2.0"}` |
| 3 | 单位是**公制米**，且给出**树高（米）** | sidecar 写 `height_m`；不确定就写"未知"并说明来源页面怎么写 |
| 4 | **Y 轴向上**（glTF 标准） | glTF 默认就是 Y-up；若来源是 Z-up 需在 sidecar 注明 |
| 5 | **原点在树干底部中心**（不是几何中心、不是包围盒中心） | 导入后树根应落在 y=0 平面；不确定就在 sidecar 写 `origin:"unknown"` |
| 6 | **一棵树一个文件**（禁止整片森林、kitbash、多树种打包） | 一个 `.glb` 只含 1 个树的节点树 |
| 7 | **带 LOD**：LOD0 ≤ 2 万三角面，LOD1 ≤ 5 千，LOD2 ≤ 1.5 千或 billboard | 没有 LOD 的低模（LOD0 ≤ 2 万）也算合格；有 LOD 请在 sidecar 列出 |

## 三、可接受 / 加分项

- 材质 ≤ 2 个，标准 PBR 贴图：BaseColor / Normal / ORM（R=AO, G=Rough, B=Metallic）
- 贴图 1K~2K（4K 会拖仓库，不用）
- UV 不重叠、法线贴图按线性（非 sRGB）导出的更好
- **针叶 + 阔叶各一棵**就够（两棵能测材质差异和形状差异，不需要更多）
- 有碰撞体更好；没有我们自己生成胶囊，不强求

## 四、明确不要

- 绑定 / 动画 / 季节变化 / 风动骨骼
- SpeedTree `.sprt`、UE `.uasset`、Unity `.prefab`（跨引擎不认）
- Sketchfab 登录墙后的资产（自动化拉不下来，CC-BY 署名链也复杂）
- 4K 贴图大包、多树种合集的超大 zip（只要单棵）

## 五、交付形式

```
D:\Projects\AssetPipeline-EngineAgnostic\assets\raw\trees\
  conifer.glb           ← 针叶，单棵
  broadleaf.glb         ← 阔叶，单棵（文件名不限，用英文小写+下划线）
  source_assets.json    ← 每个资产一条，见下
```

`source_assets.json`（可以是数组，也可以一个文件一条）：

```json
[
  {
    "file": "conifer.glb",
    "species": "spruce",
    "source_url": "https://example.com/pack",
    "author": "作者或工作室名",
    "license": "CC0-1.0",
    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    "height_m": 12.0,
    "origin": "trunk_base",
    "up_axis": "Y",
    "unit": "m",
    "tri_count_lod0": 8400,
    "lods": [8400, 2600, 900],
    "materials": 1,
    "textures": ["BaseColor.png", "Normal.png", "ORM.png"],
    "downloaded_at": "2026-11-22"
  }
]
```

字段缺失可以写 `"unknown"`，**不要编数字**——编的数字后面会害我误判尺度。

> 入库规则：目录已建好；`.glb` 等模型文件**不入 git**（`.gitignore` 已忽略，体积大），但 `source_assets.json` 会入库——它就是我们对外可查的许可证凭证。

## 六、推荐去处（许可证干净、可直链下载）

| 站点 | 许可 | 说明 |
|---|---|---|
| Quaternius | CC0 | 原生 glTF/FBX，低模自然包，最适合先跑通管线 |
| Kenney（Nature Kit） | CC0 | `.glb` 现成，低模风格化 |
| Poly Pizza | 多为 CC0 | 聚合站，下载前逐个看许可证字段 |
| Poly Haven | CC0 | 树不多，但有就是精品 |

Fab / Megascans 这次**不走**（账号绑定 + 下载器 + 曾经踩坑，对自动化不友好）。

## 七、拿回来之后 lead 会做什么（供你判断"够不够"）

1. 读 `source_assets.json`，核许可证与尺度
2. 跑 EAP 导入：glTF → 中间件（米制、Y-up→Z-up、ORM 通道整理、法线 srgb=false）
3. 跑校验层：三角面、材质槽、UV、贴图通道、许可证元数据
4. 把地块占位网格（`/Engine/BasicShapes/Cube`）换成树，重跑 `tools/run_pcg_chain.py --expect-span-m 200`
5. 判据：1892 个实例全部用树渲染、双端导入无报错、性能表（导入耗时 / 实例内存 / 三角面）落进验收文档

## 八、给采集者的自检三问

1. 这个资产能不能写进 README 的许可证表，且点得开来源 URL？
2. 树根是不是在原点（y=0）？不确定就在 sidecar 写 `unknown`，别猜。
3. 是不是单棵树、带 LOD、公制米？三条有一不满足就换一个。

---

_本文档随阶段一验收矩阵一起维护；资产落地后，本文件顶部追加上"已采用资产"一节。_
