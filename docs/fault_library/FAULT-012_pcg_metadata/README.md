# FAULT-012 · PCG 点级 metadata 没有 Python 访问器

> 状态：✅ 绕开（v1.1，出口改口径）
> 证据：README §五；`docs/pcg_forest_pilot.json`

## 症状

`get_generated_graph_output()` 能拿到 `PCGDataCollection`（tagged=1），但点级的 attribute 读不到——密度、seed 这些一个都拿不出来，翻文档也没有暴露的 Python 接口。

## 影响

想按点级属性做资产变体或校验，做不了。如果非要在出口里带上点属性，整条链会卡死在这里。

## 怎么发现

试了几种 API，拿不到；查文档和论坛也没有。

## 根因层

引擎 API 边界。不是写错了，是人家没开放。

## 命中门禁

出口设计上认这件事：以**实例 transform** 作为唯一真相，PCG 图参数单独导出成 JSON；manifest 只记 placements。

## 修复

不硬刚 API。导出端拆成两份数据：图参数（坡度/密度等规则参数）与实例 transform；落位验证只看实例。

## 修复后证据

`docs/pcg_forest_pilot.json` 1189 KB（图参数 + 1892 条散点）；`--real-multi` 1,892 实例落位并读回。

## 回归断言

```bash
python tools/run_pcg_chain.py --multi --expect-assets 20
python tools/run_pcg_chain.py --real-multi --expect-assets 2 --expect-placements 1892
```

## 为什么内置导入器不会报

无关导入器。这是 API 能力边界，只能改方案绕过去，然后在文档里写清楚"我没能力读点属性"。
