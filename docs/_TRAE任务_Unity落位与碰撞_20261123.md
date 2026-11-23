# TRAE 任务单 6：Unity 端 placements 落位 + Collider（v1.8）

> 下发：2026-11-23 22:50（lead）
> 仓库：`D:\Projects\AssetPipeline-EngineAgnostic`
> 基线：**`f155c9a`**（v1.7 已交付并被 lead 独立复验 4/4：`--multi` 13/13、`--real-multi` 20/20、`--with-compile` 21/21、`--unity-check` 4/4）
> 目标版本号：v1.8

---

## 0. 一句话目标

把 manifest 里已有的 **1892 条 placements**（conifer 1324 + broadleaf 568）在 Unity 里**真正摆出来**，每个实例带 **Collider**，并且像 v1.7 一样**机器可判**——新增 `--unity-placements-check` 命令，Unity batchmode 落位 + 独立读回 + 4 条断言。

v1.7 已经把「模型 + 贴图 + Prefab」打通了，这一单只差「实例化 + 碰撞体 + 坐标换算」。

---

## 1. 数据源（不要自造字段）

`schemas/tree_manifest_multi.json` 里每个 asset 已有：

```json
"placements": {
  "space": "world", "unit": "m",
  "count": 1324,
  "items": [{"t": [0.8713, 96.4027, -0.2142], "r": [0.0, 0.0, 152.816], "s": [1.0415, 1.0415, 1.0415]}, ...]
}
```

- `t` = 世界坐标（米），**UE 坐标系**（Z 向上，X 前，Y 右）
- `r` = **角度制**欧拉角 `[roll, pitch, yaw]`，实测只有第 3 分量（yaw）非零
- `s` = 各轴等比缩放（三值相同）
- 两棵树合计 **1892**（1324 + 568），**这个数字和每棵树的分项数都必须断言**

---

## 2. 要做的事

### A. 坐标换算（唯一权威实现，写进工具函数并加注释/测试）

UE（Z-up，左手）→ Unity（Y-up，左手）：

```
unityPos = (ue_x, ue_z, ue_y)                 // 米，Unity 1 unit = 1 m
unityYaw = -ue_yaw（度）                       // Y 轴取反，避免镜像
unityScale = s（三值）
```

**必须做验证，不许拍脑袋**：换算完把全部 1892 个实例的世界包围盒算出来，跟 manifest 里 `t` 的包围盒对比。预期：两个轴向跨度和 UE 侧落位证据（`docs/placed_tree_multi_verify.json` 的 `bboxPlacedM`）一致（每轴误差 < 0.05 m）。**如果换算错，这一步就会以米级误差暴露**，不要用"容差放大"混过去。

### B. Unity 消费端落位（`unity/Editor/ManifestImporter.cs`）

1. 新建/复用场景 `Assets/Scenes/EAP_PlacedTrees.unity`
2. 父节点 `EAP_Placements`，每个 placement 实例化对应 prefab（`PrefabUtility.InstantiatePrefab`），设置 `position/rotation/scale`
3. 每个实例加 **MeshCollider**（`convex = true` 或按简单几何用 `BoxCollider`，二选一写清理由）；prefab 本体也带 Collider（否则实例化后再加性能差）
4. 场景保存 + 记录 `ApplyPrefabInstance`（不要产生 1892 个 prefab override，用 `PrefabUtility.RevertPrefabInstance` 或直接生成无 override 实例）
5. 新增读回入口 `EAP.ManifestImporter.VerifyPlacementsFromArgs`，写 `<manifest>.unity_placements_verify.json`：

```json
{"schema": "eap.unity_placements_verify/1", "scene": "Assets/Scenes/EAP_PlacedTrees.unity",
 "instances": {"SM_Tree_conifer_PCG_Multi": 1324, "SM_Tree_broadleaf_PCG_Multi": 568},
 "total": 1892,
 "bboxM": {"min": [...], "max": [...]},
 "colliders": 1892,
 "sampleMaterials": [{"instance": "SM_Tree_conifer_PCG_Multi[0]", "baseColor": "Assets/EAP/Trees/Textures/..."}]}
```

### C. 新增 `--unity-placements-check`（**独立命令，不要并进 `--unity-check` 的 4 条**）

```
python tools/run_pcg_chain.py --unity-placements-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer" --manifest schemas\tree_manifest_multi.json
```

4 条断言：

| # | 判据 | 数据来源 |
|---|---|---|
| 1 | 实例总数 == manifest placements 总和（1892），且每棵树的实例数 == 各自 `placements.count`（1324 / 568） | 读回 JSON `instances/total` |
| 2 | 世界包围盒与 manifest `t` 的包围盒（换算后）每轴误差 < 0.05 m | 读回 JSON `bboxM` |
| 3 | 每个实例都有 Collider（colliders == instances == 1892） | 读回 JSON `colliders` |
| 4 | 抽样实例的材质仍有 BaseColor 贴图（路径 `Assets/` 开头） | 读回 JSON `sampleMaterials` |

约束：
- 跑前检测 `Unity.exe` 进程（存在则 FAIL，提示关掉 Unity）
- 自愈问题（v1.7 的教训）：落位会重建场景，**反向验证必须加 `--unity-placements-verify-only`**（跳过落位只读回）
- 证据文件加进 `.gitignore`（`*.unity_placements_verify.json`）

---

## 3. 不许动（红线）

1. `--multi` **13**、`--real-multi` **20**、`--with-compile` **21**、`--unity-check` **4** —— 一条都不许变；
2. `--tree` 16 条（历史链路）不动；
3. v1.6 UE 侧行为不动（贴图三档、roughness 贴图×0.9、三个 SHA：`F4AF3FC2…`/`752173F0…`/`6D6D84CF…`）；
4. v1.7 资产不动（fbx、`assets/eap_textures/`、manifest 的 `engineTargets.Unity.modelPath`）；
5. manifest 只允许**新增可选字段**，老文件仍合法；
6. UE 关卡与落位（`PlacedTreeMulti`、1892）不动。

---

## 4. 自验命令（逐条贴原始输出）

```bash
python tools/run_pcg_chain.py --multi --expect-assets 20
python tools/run_pcg_chain.py --real-multi --expect-assets 2 --expect-placements 1892
python tools/run_pcg_chain.py --real-multi --with-compile --expect-assets 2 --expect-placements 1892
python tools/run_pcg_chain.py --unity-check --unity "D:\Program Files\unity AZ\2022.3.62f3c1\Editor\Unity.exe" --unity-project "D:\unity\EAP_Unity_Consumer" --manifest schemas\tree_manifest_multi.json
python tools/run_pcg_chain.py --unity-placements-check --unity "..." --unity-project "D:\unity\EAP_Unity_Consumer" --manifest schemas\tree_manifest_multi.json
python tools/validate_manifest.py schemas\tree_manifest_multi.json
python tools/validate_manifest.py schemas\manifest.example.json
```

**反向验证（必须清态 + 绕过自愈）**：删掉场景里的实例（或删场景文件），跑 `--unity-placements-check --unity-placements-verify-only` → 断言 1/2/3 必须 FAIL；恢复后回到 4/4。

---

## 5. 交付清单

- [ ] 坐标换算函数 + 换算验证（1892 个 t 的包围盒 vs UE 侧 bboxPlacedM，误差 < 0.05 m）
- [ ] Unity 场景 `Assets/Scenes/EAP_PlacedTrees.unity`（1892 实例 + Collider）
- [ ] `VerifyPlacementsFromArgs` 读回 JSON
- [ ] `--unity-placements-check` 4/4 PASS + `--unity-placements-verify-only`
- [ ] 老计数不变：13 / 20 / 21 / 4
- [ ] README：新增「三之十、Unity 端 placements 落位（v1.8）」+ 已知限制更新（还剩什么没做就写什么）
- [ ] board 更新
- [ ] 一次 commit（ASCII message），不提交生成物

---

## 6. 做不完怎么拆

优先保 **A（换算验证）+ B（场景 + 实例 + Collider）+ 断言 1/3**（能数清 1892、能证明有 Collider）；断言 2（bbox）和 4（材质抽样）做不完就写"未做"，不要写死数字凑 PASS。
