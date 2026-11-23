# TRAE 任务单 5：Unity 端吃真模型（glb → fbx + 内嵌贴图导出 + 消费端可读回）

> 下发：2026-11-23 21:55（lead）
> 仓库：`D:\Projects\AssetPipeline-EngineAgnostic`
> 基线：**`c18b8e2`**（v1.6 已交付：`--multi` 13/13、`--real-multi` 20/20、`--with-compile` 21/21）
> 目标版本号：v1.7

---

## 0. 一句话目标

让 **Unity 端把两棵树真的导入进来**（模型 + 贴图 + 材质 + Prefab），并且**机器可判**——新加一条 `--unity-check` 命令自动跑 Unity batchmode、读回产物、对 manifest 断言。

现在 Unity 侧只能建材质（`ok=2 failed=0`），**模型/Prefab/贴图全都不存在**，因为：① Unity 原生不认 `.glb`；② 两棵树的贴图内嵌在 glb 里，manifest 的 `textures = {}` 是空的。

---

## 1. 现状（改之前先确认）

| 位置 | 现状 |
|---|---|
| `schemas/tree_manifest_multi.json` | v1.1；`assets[0].model.path = assets/raw/trees/conifer.glb`；`textures = {}`；`engineTargets.Unity = {destPath: "Assets/EAP/Trees", sourceGlb: ...}`；`engineTargets.UE5.meshPath` 指向 UE 里已导入的网格 |
| `unity/Editor/ManifestImporter.cs` | 读 manifest 建 Material；模型读不到 → 只出材质 + WARN；**没有**读回/校验入口，**没有** placements 支持 |
| `tools/tree_to_manifest.py` | 把 glb 材质记成 `hasMetallicRoughnessTexture/hasNormalTexture` 布尔，`textures` 留空（`material.template="embedded"`） |
| `tools/validate_manifest.py` | 允许 `textures={}` 当 `template` 为 `none/embedded`；**不允许** embedded 同时带 textures 路径（改的时候注意别破坏这条兼容） |
| 本机环境 | Unity **2022.3.62f3c1**（`D:\Program Files\unity AZ\2022.3.62f3c1\Editor\Unity.exe`），个人版许可证已激活（2026-11-23）；工程 `D:\unity\EAP_Unity_Consumer`（URP 14.0.12）；Blender **5.2.3**（`D:\Apps\Blender 5.2\blender.exe`） |

---

## 2. 要做的四件事

### A. glb → fbx 转换工具（新增 `tools/glb_to_fbx.py`）

- 用 Blender 无头跑：
  ```
  "D:\Apps\Blender 5.2\blender.exe" -b --factory-startup --python tools\glb_to_fbx.py -- <in.glb> <out.fbx>
  ```
  （**注意：Blender 成功也会返回退出码 1**，判成功要看输出文件存在 + 脚本打印的 `FBX_OK <path>` 标记，不要用 returncode 判定）
- 转换要求：
  - 导入 glb（保留材质与内嵌贴图）→ 导出 fbx：
    `bpy.ops.export_scene.fbx(filepath=out, path_mode='COPY', embed_textures=True, apply_scale_options='FBX_SCALE_ALL', object_types={'MESH'}, use_selection=False)`
  - **贴图必须跟着走**：`embed_textures=True`，导出的 fbx 里要有内嵌图像；同时**另外**把 glb 的内嵌贴图导出成 PNG 到 `assets/eap_textures/<asset>_<slot>_<channel>.png`（见 B）
  - 顶点数 / 三角面数 / 包围盒必须与 glb 一致（误差：包围盒 < 1 cm，triCount 完全一致）——不一致算失败
- 产物：`assets/raw/trees/fbx/conifer.fbx`、`broadleaf.fbx`；报告 `docs/fbx_convert_report.json`（每棵树：源 glb、fbx 路径、triCount、bboxM、贴图文件列表、Blender 版本）
- **确定性**：转换跑两次，`docs/fbx_convert_report.json` 里的 triCount/bbox 必须逐字一致（fbx 二进制含时间戳，不要求字节级一致，但几何数字必须一致）

### B. 内嵌贴图导出 + manifest 填 `textures`

- 从 glb 里解出内嵌 PNG（glb 的 `images` + bufferView；`tools/tree_to_manifest.py` 已有 glb 解析骨架可复用）
- 落到 **新目录 `assets/eap_textures/`**（**不入 `.gitignore`**，要提交；别放 `assets/textures/`，那个被忽略了）
- 命名：`<assetId>_<slotIndex>_<channel>.png`，channel ∈ `BaseColor` / `Normal`
- manifest 的 `assets[].textures` 填：
  ```json
  "BaseColor": {"path": "assets/eap_textures/SM_Tree_conifer_PCG_Multi_0_BaseColor.png", "srgb": true,  "size": [1024,1024], "channels": "RGB", "source": "glb-embedded"},
  "Normal":    {"path": "assets/eap_textures/SM_Tree_conifer_PCG_Multi_0_Normal.png",    "srgb": false, "size": [1024,1024], "channels": "RGB", "source": "glb-embedded"}
  ```
- 校验器：`textures` 里 `srgb` 规则沿用现有（Normal 必须 false）；新增 `source` 字段允许值 `glb-embedded`；**`textures={}` + `template="embedded"` 的老 manifest 必须继续合法**

### C. manifest v1.2 小扩展（可选字段，老文件仍合法）

- `assets[].engineTargets.Unity.modelPath`：Unity 侧用的模型路径（指向 fbx），例如 `"assets/raw/trees/fbx/conifer.fbx"`
- `assets[].engineTargets.Unity.materialPathPrefix`：可选，默认 `Assets/EAP/Trees`
- 同步更新：`schemas/manifest.schema.json`、`docs/manifest-spec.md`（v1.2 段）、`tools/validate_manifest.py`（新字段可选、不报错）、`tools/tree_to_manifest.py`（生成时写入）
- **UE 侧不受影响**：`engineTargets.UE5` 与 `model.path`（glb）保持原样

### D. Unity 消费端升级 + 机器可判（`unity/Editor/ManifestImporter.cs`）

1. 模型路径优先级：`engineTargets.Unity.modelPath` > `model.path`；**找不到就照旧 WARN + 只出材质**（不要抛异常）
2. 模型存在时：实例化 → 贴 `textures` 里的贴图（BaseColor→`_BaseMap`、Normal→`_BumpMap`，Normal 的 TextureImporter 设 `textureType = NormalMap`）→ 存 Prefab 到 `<destPath>/<assetId>.prefab`
3. 新增读回入口（供 Python 断言用）：
   ```
   Unity.exe -batchmode -quit -projectPath <proj> -executeMethod EAP.ManifestImporter.VerifyFromArgs -eapManifest <manifest> -logFile <log>
   ```
   写出 `<manifest>.unity_verify.json`：
   ```json
   {"schema":"eap.unity_verify/1","pipeline":"URP",
    "assets":[{"id":"...","prefab":"Assets/EAP/Trees/SM_Tree_conifer_PCG_Multi.prefab",
               "prefabExists":true,"meshTriCount":3370,
               "boundsM":{"min":[...],"max":[...]},
               "shader":"Universal Render Pipeline/Lit",
               "textures":{"BaseColor":"Assets/eap_textures/....png","Normal":"....png"},
               "texturesApplied":{"BaseColor":true,"Normal":true}}]}
   ```
4. **不要**改 `Tools → EAP → Import Manifest…` 的菜单入口和报告文件名（`<manifest>.unity_report.md`）

### E. 新增 `--unity-check` 命令（独立，**不要**并进 `--real-multi` 的断言数）

```
python tools/run_pcg_chain.py --unity-check \
    --unity "D:\Program Files\unity AZ\2022.3.62f3c1\Editor\Unity.exe" \
    --unity-project "D:\unity\EAP_Unity_Consumer" \
    --manifest schemas\tree_manifest_multi.json
```
流程：断言"Unity GUI 没开着" → batchmode 导入 → batchmode 读回 → 4 条断言：

| # | 判据 | 数据来源 |
|---|---|---|
| 1 | Unity batchmode 退出码 == 0，且 report 里 `ok=2 failed=0` | 进程退出码 + `*.unity_report.md` |
| 2 | 两个 Prefab 文件真实存在（`Assets/EAP/Trees/<id>.prefab`） | 磁盘 |
| 3 | 读回：两个资产 BaseColor + Normal 贴图都非空且 `texturesApplied` 全 true | `*.unity_verify.json` |
| 4 | 读回：Prefab bounds 与 manifest `model.boundingBoxM` 每轴误差 < 0.05 m；meshTriCount == manifest triCount | 同上 |

约束：
- 跑之前必须检测 Unity 进程：`Unity.exe` 存在则直接 FAIL（提示"关掉 Unity 再来"），不要硬跑
- 新增证据文件加进 `.gitignore`（`*.unity_verify.json`、`*.unity_log.txt`）
- **`--multi` 13 条 / `--real-multi` 20 条 / `--with-compile` 21 条断言数一律不许变**；`--unity-check` 单独一条命令、单独计数

---

## 3. 不许动（红线）

1. `--multi` **13**、`--real-multi` **20**、`--with-compile` **21** 的断言数与判据；
2. `--tree` 的 16 条（历史链路，代码保留）；
3. UE 侧 v1.6 行为：贴图三档压缩 / `Roughness = 贴图×0.9` / `T_EAP_White_Linear` / roughness SHA（`F4AF3FC2…`、`752173F0…`、`6D6D84CF…`）**必须不变**；
4. 材质编译判据三条件（`exit==0` + 无 `Failed to compile Material` + MIC 数一致）；
5. 两棵树资产、`placements=1892`（conifer 1324 + broadleaf 568）、树高 10.236 m / 9.425 m；
6. manifest v1.1 老文件仍必须合法（新字段全可选）。

---

## 4. 自验命令（逐条贴原始输出）

```bash
# 1) 老断言不受影响（必须仍是 13/13）
python tools/run_pcg_chain.py --multi --expect-assets 20
# 2) 老断言不受影响（必须仍是 20/20，URP/UE 侧行为没变）
python tools/run_pcg_chain.py --real-multi --expect-assets 2 --expect-placements 1892
# 3) 严格模式不受影响（21/21）
python tools/run_pcg_chain.py --real-multi --with-compile --expect-assets 2 --expect-placements 1892
# 4) 本轮新目标：Unity 端 4/4
python tools/run_pcg_chain.py --unity-check --unity "D:\Program Files\unity AZ\2022.3.62f3c1\Editor\Unity.exe" --unity-project "D:\unity\EAP_Unity_Consumer" --manifest schemas\tree_manifest_multi.json
# 5) manifest 仍合法（新旧都要过）
python tools/validate_manifest.py schemas\tree_manifest_multi.json
python tools/validate_manifest.py schemas\manifest.example.json
```

**反向验证（必须做，且要清态）**：
- 把 Unity 工程里的 `Assets/EAP/Trees/*.prefab` 和 `Assets/eap_textures` 对应的导入资产删掉 → 跑 `--unity-check`，断言 2/3 必须 FAIL；
- 把 `engineTargets.Unity.modelPath` 临时删掉 → 断言 2 必须 FAIL（回落到"模型未找到"路径）。
- ⚠️ 教训（v1.6 踩过）：**读磁盘/资产状态的判据，必须先清掉上轮产物再验**，否则测的是历史。

---

## 5. 交付清单

- [ ] `tools/glb_to_fbx.py` + 两个 fbx（`assets/raw/trees/fbx/`）+ `docs/fbx_convert_report.json`（两次跑几何数字一致）
- [ ] `assets/eap_textures/*.png`（glb 内嵌贴图导出，已提交）+ manifest `textures` 填写
- [ ] manifest v1.2：`engineTargets.Unity.modelPath`（schema + spec + validator + tree_to_manifest 同步）
- [ ] Unity 消费端：模型导入 + 贴图映射 + Prefab + `VerifyFromArgs` 读回 JSON
- [ ] `--unity-check`：4/4 PASS，退出码 0
- [ ] 老命令计数不变：13 / 20 / 21、`--tree` 16、两份 manifest `errors=0`
- [ ] README 新增「三之九、Unity 端吃真模型（v1.7）」+ 已知限制更新（Unity 端 placements 仍未落地，留给任务单 6）
- [ ] `board/status.json`：Unity 相关 todo 更新（模型/贴图 done，placements 单独 todo）
- [ ] 一次 commit，ASCII message，写清改了什么 + 自验结果；不提交生成物（`*.unity_verify.json`、`*.unity_report.md` 已 gitignore）

---

## 6. 做不完怎么拆

优先保 **A+B+C+`--unity-check` 断言 1/2**（模型和 Prefab 出来就算这一轮的主目标）；断言 3/4（贴图映射读回、bounds 误差）做不完就明确写"未做"，不要写死数字硬凑 PASS。

---

## 7. 追加补丁（2026-11-23 22:05，lead 实测后补，**优先看这一节**）

lead 已用 batchmode 实测过 Unity 通路，**发现任务单原设计有一个必须修的漏洞**：

### 7.1 batchmode 本身可用（好消息）

```
Unity.exe -batchmode -quit -projectPath D:\unity\EAP_Unity_Consumer ^
  -executeMethod EAP.ManifestImporter.ImportFromArgs ^
  -eapManifest D:\Projects\AssetPipeline-EngineAgnostic\schemas\tree_manifest_multi.json -logFile <log>
→ exit=0，56 秒，日志有 [EAP] pipeline=URP
```
所以 `--unity-check` 用 batchmode 自验是成立的；但**跑之前必须确认 Unity GUI 没开**。

### 7.2 必须修的漏洞：Unity 读不到工程外的文件

同一份日志里出现：
```
Invalid AssetDatabase path: D:/Projects/AssetPipeline-EngineAgnostic/assets/eap_textures/SM_Tree_conifer_PCG_Multi_0_BaseColor.png. Use path relative to the project folder.
Invalid AssetDatabase path: D:/Projects/AssetPipeline-EngineAgnostic/assets/raw/trees/conifer.glb. Use path relative to the project folder.
```

`AssetDatabase.LoadAssetAtPath` **只认 `<UnityProject>/Assets/...` 下的路径**，manifest 里的 `assets/...` 是仓库相对路径，Unity 看不到。

**修法（消费端负责，不改 manifest 语义）**：导入前先把外部文件**复制进工程**再加载：
- 贴图：`<repo>/assets/eap_textures/*.png` → `<UnityProject>/Assets/EAP/Trees/Textures/<同名>`
- 模型：`<repo>/assets/raw/trees/fbx/*.fbx` → `<UnityProject>/Assets/EAP/Trees/Models/<同名>`
- 实现：`Directory.CreateDirectory` + `File.Copy(..., overwrite:true)` + `AssetDatabase.Refresh()`，再 `LoadAssetAtPath`。复制判定用**内容哈希**（文件没变就不覆盖、不弄脏工程）。
- 读回 JSON 里的贴图路径必须是 `Assets/...` 开头（断言 3 要检查这一点，防止又把绝对路径塞进来）。

### 7.3 断言 4 的单位提醒

manifest `model.boundingBoxM` 单位是**米**，Unity 也是 1 unit = 1 m（不缩放导入即可）；fbx 从 Blender 导出时若带 0.01 缩放，Prebab bounds 会差 100 倍 —— 这条断言会抓住它，别用"换算"绕过，要修导出设置（`apply_scale_options`）。

### 7.4 `--unity-check` 还要做两件事

1. 跑之前检查 `Unity.exe` 进程，存在就 FAIL 并提示"关掉 Unity 再来"；
2. 把 Unity 日志另存为 `<manifest>.unity_log.txt` 并加进 `.gitignore`，失败时把最后 40 行贴到 CHAIN FAIL 原因里（便于定位）。
