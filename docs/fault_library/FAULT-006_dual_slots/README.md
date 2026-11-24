# FAULT-006 · 双材质槽共用树皮材质（Unity 树变铜色、叶子无镂空）

> 状态：✅ 已修复、已复验（2026-11-24，v1.13）
> 证据：`before_copper.png`（修复前：整棵铜色、叶子实心）、`after_green.png`（修复后：绿色、叶子镂空）

## 1. 症状

- Unity 2022.3 里给树林加平行光后，**整棵树是铜色/粉色**，叶子是实心片、没有镂空边缘；同一份 manifest 在 UE 里是正常的绿色树。
- 第一反应会怀疑光照（我本人第一次就误判成"没配灯"），但加灯后颜色依然不对。

## 2. 影响

Unity 端无法出图（作品集里"双端都有画面"不成立），而且 UE 端完全正常 → 典型的"跨引擎表象不一致"。

## 3. 怎么发现

1. 排除光照：加 Directional Light、调环境光后仍铜色；
2. 查预制体：`SM_Tree_conifer_PCG_Multi.prefab` 的 LOD0–LOD3 每个 Renderer **两个材质槽都指向同一个材质** `M_SM_Tree_conifer_PCG_Multi`；
3. 查该材质：`_BaseMap` = 槽 0 的**树皮**贴图，`_AlphaClip: 0`（不透明）；
4. 查 manifest：`textures` **只记录了槽 0**；槽 1 的树叶贴图 `assets/eap_textures/..._1_BaseColor.png` 在磁盘上存在，但从未进 manifest。

## 4. 根因层

**契约层**：manifest 只描述"一个资产一张贴图"，没有"每个材质槽各一组贴图"的结构；Unity 消费端只能把一张树皮贴图塞给两个槽。

## 5. 命中门禁

`--unity-materials-check`（v1.13，4 条断言）：

1. 每资产材质数 == manifest 槽数（2），且**每 LOD 每 Renderer 的槽 0 与槽 1 路径不同**（比资产路径，不比 instanceID）；
2. 槽 0 的 `_BaseMap`/`_BumpMap` 与 manifest 槽 0 一致；
3. 槽 1 的 `_BaseMap` 对、`alphaClip=true`、`_ALPHATEST_ON` 在 keywords、`cutoff=0.33`、`renderQueue=2450`；
4. manifest 里每张槽贴图的 SHA256 与磁盘一致。

## 6. 修复（v1.13 / commit `373ba22`）

1. manifest `textures` 增加 `slots[]`（槽 0 树皮 BaseColor+Normal，槽 1 树叶 BaseColor 带 alpha），旧字段保留指向槽 0，向后兼容；
2. Unity 按槽建两个材质：`M_<asset>_Bark`（不透明）与 `M_<asset>_Leaves`（Alpha Clipping 开、Cutoff 0.33）；
3. LOD0–LOD3 的 8 个 Renderer 全部按 `[槽0, 槽1]` 赋值；
4. 槽序与 glb primitive 顺序、UE 侧 `materialNamesLod0` 三方对齐（Bark=0 / Leaves=1）。

## 7. 修复后证据（本目录）

| 图 | 内容 |
|---|---|
| `before_copper.png` | 修复前：Unity 场景里整片树林铜色、叶子实心 |
| `after_green.png` | 修复后：绿色树叶、边缘镂空、天空盒正常 |

## 8. 回归断言

```bash
python tools/run_pcg_chain.py --unity-materials-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer" --manifest schemas\tree_manifest_multi.json
# 期望：UNITY-MATERIALS-CHECK PASS assets=2 slots=[2, 2] textures=6
```

反验（证明断言有牙）：把某 LOD 槽 1 材质路径改成槽 0 → 断言 1 必须 FAIL；关掉槽 1 的 alpha clip → 断言 3 必须 FAIL。

## 9. 为什么内置导入器不会报

内置导入器按"这个资产有 2 个材质槽"导入，但它不会检查"两个槽是否指向同一材质、槽材质是否用对了贴图、alpha 模式是否与 manifest 约定一致"——这些是**跨引擎语义**，只有契约层能判。
