# FAULT-007 · LOD0 顶点数与源 glb 不等（顶点分裂）

> 状态：✅ 已修复（判据设计）、已复验（v1.12.1）
> 证据：`docs/tree_lods.json`（源 glb）、`docs/tree_lods_ue.json`（UE 读回）、`--lod-check` 断言 2 输出

## 1. 症状

- UE 里 LOD0 的**渲染顶点数** 2961 / 4707，源 glb 是 2960 / 4704，差 +1 / +3。
- 如果断言写成"UE 顶点必须严格等于源 glb"，会**假 FAIL**；如果只看三角面数（3370 == 3370）就算过，又会在"换了个网格但面数巧合相同"时**漏判**。

## 2. 影响

这是判据设计问题：**严格相等会误报，只看面数会漏判**。跨引擎导入时顶点数天然会变，必须找到"既能证明是同一个网格、又不误报"的判据。

## 3. 怎么发现

给 LOD 补断言时，先把三处口径摆出来对比：

| 口径 | conifer | broadleaf |
|---|---|---|
| glb accessor（按 primitive 求和） | 2960 | 4704 |
| UE **MeshDescription** 顶点数 | **2960（完全相等）** | **4704（完全相等）** |
| UE 渲染顶点数 | 2961（Δ+1） | 4707（Δ+3） |

## 4. 根因层

**引擎导入**：导入时为法线/UV 接缝做顶点分裂（render vertex split），渲染顶点数 > MeshDescription 顶点数。这不是错误，是引擎行为。

## 5. 命中门禁

`--lod-check` 断言 2（v1.12.1 补硬版）：

- LOD0 `triCount` == 源 glb（逐 primitive 求和后比较）；
- `vertexCount ∈ [src, ceil(src×1.01)]`，并**打印真实 Δ 与上界**（conifer 上界 2990、broadleaf 4752）；
- `materialNamesLod0` 与源**逐字相等**（不做归一化）；
- UV 通道数严格相等；UV 包围盒逐轴差 < 0.02；
- **以 UE MeshDescription 顶点数 == glb 顶点数作为"同一个网格"的铁证**（2960 / 4704 完全相等；UV min 也完全相等，含负值 V）。

## 6. 修复

不是改数据，是**改判据**：容差设计 + 铁证（MeshDescription）+ 反验（篡改源顶点到 1000、篡改 UE 顶点到 3108，都必须 FAIL）。

## 7. 修复后证据

```
[2/4] conifer LOD0 tri=3370/3370 vert=2961（源 2960，Δ=+1，上界 2990）材质名=['Bark_NormalTree','Leaves_Pine'] UV通道=1/1 UV 范围差=0.000000（阈值 0.02） → PASS
[2/4] broadleaf LOD0 tri=3505/3505 vert=4707（源 4704，Δ=+3，上界 4752）… → PASS
```

## 8. 回归断言

```bash
python tools/run_pcg_chain.py --lod-check
# 期望：LOD-CHECK PASS（UE 4 级 / LOD0=3370,3505 + 顶点/材质名/UV 对源 / Unity 同级三角面一致 / fbx SHA 可追溯）
```

反验：把 `docs/tree_lods.json` 的 `source.vertexCount` 改成 1000 → 只挂断言 2；把 UE LOD0 顶点改成 3108（超上界）→ 只挂断言 2。

## 9. 为什么内置导入器不会报

导入器不会告诉你"顶点多了 3 个是因为接缝分裂"还是"换了个网格"；它只负责导入。**把"看起来一样"变成"可断言的相等"**，需要自己定义口径（MeshDescription + 容差 + 反验）。
