# Unity 消费端 · 空工程实跑记录（2026-11-23 21:34）

> 目的：把「Unity 端能自动读同一份 manifest 并建材质」从**没跑过**变成**跑过 + 有证据**。
> 结论：**通路跑通（ok=2 failed=0，URP Lit）**；模型 / 贴图 / placements 未导入（原因见 §5，属数据侧缺口，不是链路问题）。

## 1. 环境

| 项 | 值 |
|---|---|
| Unity 编辑器 | **2022.3.62f3c1**（LTS，`D:\Program Files\unity AZ\2022.3.62f3c1`） |
| Unity Hub | 3.3.7（编辑器通过「定位」注册） |
| 工程 | `D:\unity\EAP_Unity_Consumer`，模板 **URP Empty Template**（`com.unity.render-pipelines.universal: 14.0.12`） |
| 消费端脚本 | `Assets\Editor\ManifestImporter.cs`（管线自适应版：先找 `Universal Render Pipeline/Lit`，找不到回退 `Standard`） |
| 依赖包 | `com.unity.nuget.newtonsoft-json: 3.2.1`（写进 `Packages\manifest.json`） |
| manifest | `D:\Projects\AssetPipeline-EngineAgnostic\schemas\tree_manifest_multi.json`（v1.1，2 assets） |

## 2. 操作

菜单 **Tools → EAP → Import Manifest…** → 选上面那份 manifest。脚本按 manifest 所在目录反推仓库根（`<repo>/schemas/x.json` → `<repo>`），再用 `engineTargets.Unity` 建材质。

## 3. 结果（Console 原文）

```
[EAP] pipeline=URP shader=Universal Render Pipeline/Lit
Invalid AssetDatabase path: D:/Projects/AssetPipeline-EngineAgnostic/assets/raw/trees/conifer.glb. Use path relative to the project folder.
[EAP] 模型未找到，只输出材质: D:/Projects/AssetPipeline-EngineAgnostic/assets/raw/trees/conifer.glb
Invalid AssetDatabase path: D:/Projects/AssetPipeline-EngineAgnostic/assets/raw/trees/broadleaf.glb. Use path relative to the project folder.
[EAP] 模型未找到，只输出材质: D:/Projects/AssetPipeline-EngineAgnostic/assets/raw/trees/broadleaf.glb
[EAP] 完成 ok=2 failed=0 → D:/Projects/AssetPipeline-EngineAgnostic/schemas/tree_manifest_multi.unity_report.md
```

报告文件 `schemas/tree_manifest_multi.unity_report.md`：

```
# EAP Unity report  2026-11-23T21:34:18
- pipeline: URP  shader: Universal Render Pipeline/Lit
- manifest: D:/Projects/AssetPipeline-EngineAgnostic/schemas/tree_manifest_multi.json
- manifestVersion: 1.1  assets: 2
- WARN SM_Tree_conifer_PCG_Multi 模型未找到（Unity 原生不认 .glb）… → 只输出材质
- OK   SM_Tree_conifer_PCG_Multi   mat=Assets/EAP/Trees/M_SM_Tree_conifer_PCG_Multi.mat   prefab=<none: 模型未导入>
- WARN SM_Tree_broadleaf_PCG_Multi 模型未找到（Unity 原生不认 .glb）… → 只输出材质
- OK   SM_Tree_broadleaf_PCG_Multi mat=Assets/EAP/Trees/M_SM_Tree_broadleaf_PCG_Multi.mat prefab=<none: 模型未导入>
```

## 4. 产物核验（磁盘实测，不只看 Console）

| 产物 | 大小 | 证据 |
|---|---|---|
| `Assets/EAP/Trees/M_SM_Tree_conifer_PCG_Multi.mat` | 3,637 B | 存在，21:34:19 |
| `Assets/EAP/Trees/M_SM_Tree_broadleaf_PCG_Multi.mat` | 3,640 B | 存在，21:34:19 |
| 两个 `.mat` 引用的 shader | guid `933532a4fcc9baf4fa0491de14d08ed7` | 与 `Library/PackageCache/com.unity.render-pipelines.universal@14.0.12/Shaders/Lit.shader.meta` 的 guid **完全一致** ⇒ 确实是 URP Lit，不是回退 Standard |

## 5. 这轮**没有**做到的三件事（都属数据侧缺口）

| 缺什么 | 根因 | 归属 |
|---|---|---|
| 贴图没挂上（`_BaseMap`/`_BumpMap` 为空） | 两棵树的贴图**内嵌在 glb 里**，manifest 里 `textures = {}`（`material.template = "embedded"`），Unity 侧无图可挂 | 需要先解决 glb 导入，再把内嵌贴图导出成 PNG/资产 |
| 模型 / Prefab 没生成 | **Unity 原生不支持 .glb**（UE 支持，Unity 不支持），`AssetDatabase.LoadAssetAtPath` 报 `Invalid AssetDatabase path`，只能建材质 | 二选一：① 装 `glTFast`/UnityGLTF 插件；② Blender 把 glb 转 fbx，manifest 的 Unity 目标指 fbx |
| 1892 条 placements 没落地 | 依赖 Prefab 存在 | 同上解决后再做 |

> Console 里那两条 `Invalid AssetDatabase path` 是**上面第二条的必然结果**，不是脚本 bug；脚本已按"模型缺失 → 只出材质 + 写 WARN"设计。

## 6. 下一步（按优先级）

1. **Unity 端吃 glb**：优先 **Blender 转 fbx**（不引入第三方包、Unity 原生支持）；manifest 需要加一个可选字段（例如 `engineTargets.Unity.modelPathOverride`），并在校验器里放行——属 manifest v1.2 的小扩展。
2. glb 内嵌贴图导出 PNG → manifest 的 `textures` 填真实路径 → 材质贴图断言（`_BaseMap`/`_BumpMap` 有值、Normal 贴图类型 = NormalMap）。
3. placements 落成 GameObject（复用 UE 侧同一份 `placements` 数组）。
4. 双端一致性断言（资产名 / 目标路径 / 包围盒三列一致）。
