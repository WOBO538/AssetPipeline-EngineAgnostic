# FAULT-010 · 用 new_object 建的 ISM 组件，存关卡后消失

> 状态：✅ 已修（v1.1）
> 证据：README §五

## 症状

`unreal.new_object(InstancedStaticMeshComponent, outer=actor)` 建出组件，当场能读能写，实例也在。存关卡、重开，Actor 上没这个组件了。

## 影响

落位看似成功，实际没持久化。重开一次就白干，而且前面所有"读回 1892"的断言都没发现。

## 怎么发现

换成新进程重载关卡去数实例，数不到。

## 根因层

引擎 API。直接 new 出来的对象不在 Actor 的 `OwnedComponents` 里，序列化的时候不带上。

## 命中门禁

"新进程重载关卡读回实例数 == placements"这条持久化断言。

## 修复

改用 `SubobjectDataSubsystem.add_new_subobject`——就是 Details 面板里 Add Component 走的那个 API。

## 修复后证据

重载关卡读回 1892；`--real-multi` 断言 6 PASS。

## 回归断言

```bash
python tools/run_pcg_chain.py --real-multi --expect-placements 1892
```

## 为什么内置导入器不会报

不是导入路径。这是编辑器脚本建对象的坑，导入器不参与。
