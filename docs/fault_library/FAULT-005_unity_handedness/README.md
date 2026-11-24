# FAULT-005 · 同一个 glb 进 Unity，包围盒每轴差 7.4 cm

> 状态：✅ 已修（v1.7）
> 证据：README §三之九；`--unity-check` 读回 bounds

## 症状

glb 导进 Unity 后，读回来的包围盒与源数据每个轴都差约 0.074 m。方向是对的，尺寸和位置差一点。

## 影响

落位对不上、双端一致性断言全飘。差 7 cm 在 10 m 的树上看着不多，但它是"转换错了"的信号，不是精度噪声。

## 怎么发现

Unity 侧读回 bounds，和 glb 原始 bounds 逐轴对比，差 0.074 m，稳定复现。

## 根因层

坐标与单位。两件事叠在一起：Unity 是左手系，FBX 导入时会做一次轴向取反；FBX 导出默认还带单位缩放。不在导出端预先把坐标系处理掉，Unity 那边就会多取反一次（或者说少取反一次，取决于你从哪边数）。

## 命中门禁

`--unity-check`：Prefab bounds 与 manifest 期望逐轴误差 ≤ 2e-5 m；`--parity-check`：1,892 条实例 transform 逐条比。

## 修复

1. Blender 导出前先把根变换烘进网格（`parent_clear` + `transform_apply`），再沿世界 X 做预镜像、重算法线；
2. 导出用 `FBX_SCALE_ALL`，让几何真按米落地；
3. Unity 侧 yaw 取反（`unityYaw = -ue_yaw`），不再自己对轴向。

## 修复后证据

`bboxMaxDiffM = 0.0`；Unity 读回 triCount 3370/3505 与 bounds 都对上；1,892 条实例逐条对账 maxPos=0、maxYaw=0°、maxScale=1e-6。

## 回归断言

```bash
python tools/run_pcg_chain.py --unity-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer"
python tools/run_pcg_chain.py --parity-check
# 期望：UNITY-CHECK PASS；PARITY-CHECK PASS
```

## 为什么内置导入器不会报

它按自己的默认约定导入，不报错也不告诉你"你已经取反过一次了"。左右手和单位是跨引擎的约定问题，导入器不知道你要的是什么。
