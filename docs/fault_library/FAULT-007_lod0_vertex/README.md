# FAULT-007 · LOD0 顶点比源 glb 多 1 个 / 3 个

> 状态：✅ 已修（改判据）、已复验（v1.12.1）
> 证据：`docs/tree_lods.json`、`docs/tree_lods_ue.json`、`--lod-check` 输出

## 症状

UE 里 LOD0 的渲染顶点数是 2961 / 4707，源 glb 是 2960 / 4704。三角面数倒是一样（3370 / 3505）。

## 影响

判据怎么写都不舒服：写"严格相等"会假 FAIL；只看面数又会在"换了个网格但面数碰巧一样"时漏判。

## 怎么发现

给 LOD 补断言时把三处口径摆出来对比，才发现差在顶点分裂：

| 口径 | conifer | broadleaf |
|---|---|---|
| glb accessor（逐 primitive 求和） | 2960 | 4704 |
| UE MeshDescription 顶点数 | 2960 | 4704 |
| UE 渲染顶点数 | 2961 | 4707 |

## 根因层

引擎导入。法线和 UV 接缝处会分裂顶点，渲染顶点数比 MeshDescription 多。这是正常行为，不是 bug。

## 命中门禁

`--lod-check` 断言 2：LOD0 三角面数等于源；顶点数在 `[源, ceil(源×1.01)]` 之间并打印真实 Δ 和上界（conifer 2990 / broadleaf 4752）；材质名逐字相等；UV 通道数严格相等；UV 包围盒逐轴差 < 0.02。另外拿 MeshDescription 顶点数等于 glb 顶点数当铁证（2960/4704 完全相等）。

## 修复

不是改数据，是改判据：给容差，同时用 MeshDescription 证明是同一个网格；再用反验证明断言有牙。

## 修复后证据

```
[2/4] conifer LOD0 tri=3370/3370 vert=2961（源 2960，Δ=+1，上界 2990）材质名=... → PASS
[2/4] broadleaf LOD0 tri=3505/3505 vert=4707（源 4704，Δ=+3，上界 4752）… → PASS
```

## 回归断言

```bash
python tools/run_pcg_chain.py --lod-check
# 期望：LOD-CHECK PASS（UE 4 级 / LOD0 对源 / Unity 同级一致 / fbx SHA 可追溯）
```

反验：把 `source.vertexCount` 改成 1000 → 只挂断言 2；把 UE LOD0 顶点改成 3108 → 只挂断言 2。

## 为什么内置导入器不会报

它不会告诉你顶点多 3 个是接缝分裂还是换了网格。要区分这两件事，得自己定义口径（MeshDescription + 容差 + 反验）。
