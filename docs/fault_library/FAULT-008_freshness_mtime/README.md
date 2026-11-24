# FAULT-008 · 换台机器后门禁报"证据不新鲜"，可证据是刚生成的

> 状态：✅ 已修（v1.11）
> 证据：`docs/_lead复核_v111_新鲜度门禁_20261124.md`

## 症状

在本地跑 `--parity-check` 一直 PASS。换台机器 clone 仓库后再跑，直接 FAIL：`证据早于 manifest（不新鲜，unity）`。可 Unity 证据明明是刚生成的。

## 影响

门禁在 clone 后不可用。更麻烦的是它会诱导人去改文件时间戳糊过去——那正好毁掉这条门禁的全部意义。

## 怎么发现

clone 下来看时间戳：所有文件 mtime 都是 checkout 那一刻，manifest 和证据的先后顺序变成随机的，取决于 checkout 顺序。

## 根因层

验收口径。用文件 mtime 判新鲜度，mtime 是不可移植的元数据。

## 命中门禁

`--parity-check` 的前置检查。

## 修复

v1.11 改成比较 JSON 内部时间戳：`manifest.generatedAt`、UE 证据的 `when`、Unity 证据的 `generatedAt`。都是 ISO8601 带时区，跨时区偏移也能算（`Z` / `-05:00`）。缺字段直接 FAIL，提示"缺内部时间戳，无法判定新鲜度"。`EAP_PARITY_IGNORE_FRESHNESS=1` 只留给测试。

## 修复后证据

clone 后按序跑 `--real-multi` → `--unity-placements-check` → `--parity-check`：PASS，输出里带三个绝对时刻。

## 回归断言

```bash
python tools/run_pcg_chain.py --parity-check
# 期望：PARITY-CHECK PASS（内部时间戳：manifest < ue < unity）
```

反验：先把 manifest 换成旧版、证据不动 → 必须 FAIL 并打印三个时间戳。

## 为什么内置导入器不会报

这条门禁不是导入器的一部分，是自建验收逻辑的口径 bug。
