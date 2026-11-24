# TRAE 任务单 11：Unity 双材质槽（manifest 多槽贴图 → 树皮/树叶分开，v1.13）

> 下发：2026-11-24 18:00（lead）
> 仓库：`D:\Projects\AssetPipeline-EngineAgnostic`
> 基线：**`2120155`**（v1.12.1，阶段一 6/6 全闭、GitHub 远端 `2120155`）
> 目标版本号：v1.13
> 起因：Unity 侧出图发现**整棵树是铜色/粉色**，lead 定位到根因（不是光照）

---

## 0. 根因（lead 已核实，不是猜的）

用户在 Unity 里加平行光后，树林仍是铜色/粉色。lead 查证如下：

1. **manifest 的 `textures` 只记了槽 0（树皮）**：
   `schemas/tree_manifest_multi.json` 里每个 asset 只有 `BaseColor`（`..._0_BaseColor.png`）+ `Normal`（`..._0_Normal.png`），
   **没有槽 1（树叶）的贴图记录**——虽然磁盘上 `assets/eap_textures/` 里有 `SM_Tree_conifer_PCG_Multi_1_BaseColor.png`（33,660 B）
   和 `SM_Tree_broadleaf_PCG_Multi_1_BaseColor.png`（158,015 B）。
2. **Unity 预制体两个材质槽指向同一个材质**：
   `SM_Tree_conifer_PCG_Multi.prefab` 里 LOD0–LOD3 的 `m_Materials` 两条都是 `M_SM_Tree_conifer_PCG_Multi`
   （guid `272f76f1dd2e7de48aea3f9f49f05a75`），该材质 `_BaseMap` = 树皮 BaseColor，`_AlphaClip: 0`、`_Surface: 0`（不透明）。
   → 树叶几何体用的是**树皮贴图**且没有 alpha 裁剪，所以整棵树是树皮铜色、叶子是实心片。
3. UE 侧没这个问题（UE 是两槽两材质：`Bark_NormalTree` / `Leaves_Pine`，叶子 MASKED）。**所以这是 Unity 消费端的真实缺口**，
   不是"光照没配"——lead 之前那句"没配光照"说错了，这里更正。

---

## 1. 红线

1. 五条老门槛 13 / 20 / 21 / 4 / 4 + parity 4/4 + lod-check 4/4 **一条都不许变**；
2. manifest **只允许新增**多槽字段，**旧字段必须保留**（`textures.BaseColor` / `textures.Normal` 仍指向槽 0，老消费端/老证据不破）；
3. 不许手工在 Unity 里改材质冒充（**必须由 manifest 驱动**，改完跑 `--unity-check` 能重建出同样的两槽材质）；
4. UE 侧行为不受影响（UE 两槽已经是对的，不许动 `place_manifest.py` 的 UE 材质逻辑）；
5. LOD0–LOD3 的六条材质槽都要映射正确（LOD 网格同样 2 槽）。

---

## 2. 要做的事

### A. manifest 增加多槽贴图结构（`tools/tree_to_manifest.py`）

```json
"textures": {
  "BaseColor": {...槽 0 旧字段，保留...},
  "Normal": {...槽 0 旧字段，保留...},
  "slots": [
    {"slot": 0, "material": "Bark_NormalTree", "alphaMode": "OPAQUE",
     "BaseColor": {"path": "assets/eap_textures/SM_Tree_conifer_PCG_Multi_0_BaseColor.png", "srgb": true, "channels": "RGB", "size": [1024,1024]},
     "Normal":    {"path": "assets/eap_textures/SM_Tree_conifer_PCG_Multi_0_Normal.png", "srgb": false, "channels": "RGB", "size": [1024,1024]}},
    {"slot": 1, "material": "Leaves_Pine", "alphaMode": "MASKED", "cutoff": 0.33,
     "BaseColor": {"path": "assets/eap_textures/SM_Tree_conifer_PCG_Multi_1_BaseColor.png", "srgb": true, "channels": "RGBA", "size": [1024,1024]}}
  ]
}
```

- 槽 1 的贴图必须从 glb 内嵌资源导出（`_1_BaseColor.png` 已在磁盘，确认 SHA/尺寸/通道并写进 manifest）；
- 每个槽的 SHA256 写进 manifest，供断言 4 核对。

### B. Unity 消费端：按槽建材质并逐槽赋给 Renderer

`unity/Editor/ManifestImporter.cs`：

- 每个 asset 按 `textures.slots` 建**两个材质**：
  - 槽 0：`M_<asset>_Bark`，URP Lit，`_BaseMap` = 槽 0 BaseColor，`_BumpMap` = 槽 0 Normal，Alpha Clipping **关**；
  - 槽 1：`M_<asset>_Leaves`，URP Lit，`_BaseMap` = 槽 1 BaseColor，**Alpha Clipping 开、Cutoff = 0.33**，`_BumpMap` 有就挂没有就空；
- Prefab 的 **LOD0–LOD3 每个 Renderer** 的 `m_Materials` 数组必须是 `[槽0材质, 槽1材质]`（长度 2、元素不同）；
- 贴图仍按内容哈希复制 + `AssetDatabase.Refresh`（沿用 v1.7 的机制）。

### C. 新命令 `--unity-materials-check`（4 条断言，不并入老门槛）

```
python tools/run_pcg_chain.py --unity-materials-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer" --manifest schemas\tree_manifest_multi.json
```

| # | 断言 |
|:--:|---|
| 1 | 每个 prefab 的每个 LOD Renderer 材质数 = manifest 槽数（2），且**槽 0 ≠ 槽 1**（不许同材质） |
| 2 | 槽 0 材质的 `_BaseMap` 路径 == manifest 槽 0 BaseColor；`_BumpMap` == 槽 0 Normal |
| 3 | 槽 1 材质 `_BaseMap` == manifest 槽 1 BaseColor，且 `AlphaClip=true`、`Cutoff=0.33` |
| 4 | manifest 里每张槽贴图的 SHA256 与磁盘文件一致（可追溯、没被手改） |

**反验（必须两条）**：
1. 把两槽都指向同一个材质（手动改一份副本再跑）→ 断言 1 必须 FAIL；
2. 把槽 1 材质的 Alpha Clipping 关掉 → 断言 3 必须 FAIL。

### D. 视觉复核（lead 需要一张图）

修完由 lead/用户重开 Unity 截图确认树叶是绿色而不是铜色（这步属于人工验收，不算断言）。

---

## 3. 自验

```bash
python tools/run_pcg_chain.py --unity-check --unity "..." --unity-project "D:\unity\EAP_Unity_Consumer"
python tools/run_pcg_chain.py --unity-placements-check --unity "..." --unity-project "D:\unity\EAP_Unity_Consumer"
python tools/run_pcg_chain.py --unity-materials-check --unity "..." --unity-project "D:\unity\EAP_Unity_Consumer"
python tools/run_pcg_chain.py --lod-check
python tools/run_pcg_chain.py --multi --expect-assets 20
python tools/run_pcg_chain.py --real-multi --expect-assets 2 --expect-placements 1892
python tools/run_pcg_chain.py --parity-check
python tools/validate_manifest.py schemas\tree_manifest_multi.json
```

---

## 4. 交付清单

- [ ] manifest 多槽结构（旧字段保留，`textures.slots[]` 带 SHA）
- [ ] Unity 两材质（Bark 不透明 / Leaves alpha clip 0.33）逐槽赋给 LOD0–3
- [ ] `--unity-materials-check` 4/4 + 两条反验
- [ ] 老门槛 13 / 20 / 21 / 4 / 4 / 4 / 4 不变
- [ ] README「三之十四、Unity 双材质槽（v1.13）」+ 诚实边界
- [ ] board 更新
- [ ] 一次 commit（ASCII message）

---

## 5. 做不完怎么拆

优先保 **manifest 多槽 + Unity 两材质 + 断言 1/3 + 反验 2 条**；断言 2/4（BaseMap 路径与 SHA 核对）若时间不够，下一单补，但**不许把"两槽同一材质"放过去**。
