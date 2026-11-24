# FAULT-006 · 双材质槽共用树皮材质，Unity 树变铜色

> 状态：✅ 已修、已复验（v1.13 / `373ba22`）
> 证据：`before_copper.png`、`after_green.png`

## 症状

Unity 里给树林加平行光，整棵树是铜色，叶子是实心片、没有镂空。同一份 manifest 在 UE 里是正常的绿树。

## 影响

Unity 端出不了图，"双端都有画面"这条不成立。UE 端完全正常，所以问题只出在 Unity 消费侧。

## 怎么发现

我第一次的判断是错的，以为是没配灯。加完 Directional Light、调了环境光，还是铜色，才回去查材质。

查预制体：`SM_Tree_conifer_PCG_Multi.prefab` 的 LOD0–LOD3，每个 Renderer 两个材质槽都指向同一个材质 `M_SM_Tree_conifer_PCG_Multi`。查材质：`_BaseMap` 是槽 0 的树皮贴图，`_AlphaClip: 0`。查 manifest：`textures` 只记了槽 0。槽 1 的树叶贴图在磁盘上有，但从来没进过 manifest。

## 根因层

契约层。manifest 只描述"一个资产一张贴图"，没有"每个材质槽一组贴图"的结构，Unity 只能把树皮塞给两个槽。

## 命中门禁

`--unity-materials-check` 四条：材质数等于槽数、每 LOD 每 Renderer 槽 0 与槽 1 路径不同（比路径，不比 instanceID）、槽 0 的 BaseMap/BumpMap 对、槽 1 的 BaseMap 对且 alphaClip/cutoff 0.33/renderQueue 2450、贴图 SHA 与 manifest 一致。

## 修复

manifest 加 `slots[]`（旧字段保留）；Unity 按槽建两个材质，槽 1 开 Alpha Clipping、Cutoff 0.33；LOD0–3 的 8 个 Renderer 全部按 `[槽0, 槽1]` 赋值；槽序与 glb primitive 顺序、UE 的 `materialNamesLod0` 三方对齐。

## 修复后证据

树叶绿色、边缘镂空、天空盒正常（after）。修复前是整片铜色（before）。

## 回归断言

```bash
python tools/run_pcg_chain.py --unity-materials-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer" --manifest schemas\tree_manifest_multi.json
# 期望：UNITY-MATERIALS-CHECK PASS assets=2 slots=[2, 2] textures=6
```

反验：把某槽 1 路径改成槽 0 → 断言 1 必须 FAIL；关掉槽 1 的 alpha clip → 断言 3 必须 FAIL。

## 为什么内置导入器不会报

它按"这个资产有 2 个材质槽"导入，不会检查两个槽是不是同一个材质、贴图是不是用对了、alpha 模式和约定是否一致。这些是跨引擎语义，导入器不管。
