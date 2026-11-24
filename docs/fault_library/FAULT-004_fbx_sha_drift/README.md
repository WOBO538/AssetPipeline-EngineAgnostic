# FAULT-004 · 同一个 glb 连导两次，fbx 的 SHA 不一样

> 状态：✅ 已修（v1.12）
> 证据：`docs/tree_lods.json`（6 个 LOD fbx 的 SHA256 与 `hashNote`）

## 症状

同一个源 glb，连续导出两次 fbx，SHA256 不同。差异约 80 字节、分散在 20 处。

## 影响

LOD 链的判据是"源没变，LOD 就不该变"。SHA 每次都变，就没法用哈希做回归，也证明不了产物可复现。更糟的是有人会开始"容忍 SHA 抖动"，容忍到最后等于没有判据。

## 怎么发现

把导出跑两遍，比 SHA。第一次以为是 Blender 版本问题，看了二进制 diff 才发现差异集中在文件头的时间戳和对象 UID 上。

## 根因层

导出端（Blender FBX 插件的确定性）。两处随机源：

1. `export_fbx_bin` 里用 `datetime.datetime.now()` 写 `CreationTimeStamp`（时/分/秒/毫秒）；
2. `fbx_utils._key_to_uuid()` 用 Python 的 `hash(key)` 生成对象 UID，而字符串 hash 每个进程的种子都不同。

## 命中门禁

`make_tree_lods.py --self-check-sha`：连续导出两遍，6 个 LOD fbx 的 SHA 必须全一致。入库只记原始 SHA，不做"归一化后再比"。

## 修复

在进程内 monkeypatch 两处：`datetime.datetime.now()` 返回固定时间；`_key_to_uuid` 改成用 `sha256(repr(key))` 派生 UUID。`PYTHONHASHSEED=0` 单独设置**不管用**，只对第二处里的 hash 有影响，第一处时间戳照旧。

## 修复后证据

两次导出逐字节一致；conifer LOD1-3 与 broadleaf LOD1-3 的 SHA 入库，之后每次重跑都对得上，`git status` 里 0 个 fbx 变更。

## 回归断言

```bash
python tools/make_tree_lods.py --self-check-sha
# 期望：6 个 SHA 全一致，problems=0
```

## 为什么内置导入器不会报

它只管读文件。文件里写的是"现在几点"和随机 UID，它完全接受。跨进程可复现不是导入器的职责。
