# FAULT-002 · Nanite 开着时面数报的是 fallback（1414 / 2354）

> 状态：✅ 已修（改为按口径取数，两个值都记录）
> 证据：`docs/tree_lods_ue.json`（`triCountNaniteOn` 与 `triCount` 并存）

## 症状

导入树资产后读 LOD0 面数，`mesh.get_num_triangles(0)` 报 conifer 1414、broadleaf 2354。源 glb 是 3370 / 3505，两边差一倍多。

## 影响

如果拿这个值去写验收表，会得出"UE 侧面数只有源的一半"的结论。要么误判资产被简化了，要么拿它当"优化成果"写进材料——两种都是错。

## 怎么发现

先把源 glb 用 accessor 逐 primitive 加了一遍：conifer 2160+1210=3370，broadleaf 1705+1800=3505。再对 UE 读回值，对不上，才想到去查 Nanite 开关。

## 根因层

引擎读取口径。Nanite 开着时，`get_num_triangles(0)` 返回的是 **Nanite fallback 网格**（给非 Nanite 路径和距离剔除用的简化版），不是 LOD0 的源数据。导入本身没错。

## 命中门禁

在 `--lod-check` 与 `docs/tree_lods_ue.json` 里把两个值分开记：`triCount`（临时关掉 Nanite 读到的真实 LOD0）与 `triCountNaniteOn`（默认读到的 fallback）。谁引用哪个值，必须写明。

## 修复

不是改数据，是改读法：在内存里临时把 Nanite 关掉、读 `get_num_triangles(0)`、再关回去，**不保存**，拿到真实的 3370 / 3505。

## 修复后证据

| 物种 | 源 glb | Nanite 默认读 | 关 Nanite 后读 |
|---|---|---|---|
| conifer | 3370 | 1414 | **3370** |
| broadleaf | 3505 | 2354 | **3505** |

## 回归断言

```bash
python tools/run_pcg_chain.py --lod-check
# 断言 2 里 LOD0 triCount 必须是 3370/3505（与源相等），不是 1414/2354
```

## 为什么内置导入器不会报

它不认为这是错的。fallback 网格就是它给 Nanite 的正常产物，API 返回它天经地义。要拿真实面数，得自己关掉再读，而且要记得关回去。
