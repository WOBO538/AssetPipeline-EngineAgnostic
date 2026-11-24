# FAULT-011 · Unity 只认 Assets/ 下的资源，工程外路径读出 null

> 状态：✅ 已修（v1.7）
> 证据：`--unity-check` 的 `texturesApplied` 读回

## 症状

用 `AssetDatabase.LoadAssetAtPath` 加载工程外的 fbx / 贴图，返回 null。Prefab 建出来了，材质槽是空的，读回 `texturesApplied=false`。

## 影响

消费端静默失败：不报错，只是贴图没挂上。最后出来的资产是灰的。

## 怎么发现

--unity-check 读回贴图状态全 false，路径字段为空。

## 根因层

引擎 API。Unity 的资源系统只认工程内 `Assets/` 下的相对路径，工程外的东西它不加载。

## 命中门禁

`--unity-check`：四张贴图 `texturesApplied` 全 true，且路径都在 `Assets/` 下。

## 修复

按内容哈希把 fbx 和贴图复制进 `Assets/EAP/...`，`AssetDatabase.Refresh()` 之后再加载。

## 修复后证据

`UNITY-CHECK PASS assets=2 prefabs=2 textures=4/4`，路径全 `Assets/`。

## 回归断言

```bash
python tools/run_pcg_chain.py --unity-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer"
```

## 为什么内置导入器不会报

它只处理工程内文件。你给它工程外路径，它安静地给你 null。
