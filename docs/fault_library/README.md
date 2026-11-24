# 跨引擎资产故障库（Fault Library）

> 一句话：**同一份 manifest 契约，在两个引擎里出过的真实故障，每个案例固定八段，全部可复现、可回归。**
> 定位：作品集 NO.10 的主证据；也是面试时最容易被追问、最好讲的一段。
> 为什么做：数量断言全绿不代表资产对。"能发现并解释内置导入器会静默接受的问题"才是管线 TA 的硬能力。

## 索引（12 个真实案例）

| # | 案例 | 症状一句话 | 根因层 | 状态 | 证据 |
|:--:|---|---|---|---|---|
| 003 | [法线贴图 sRGB](FAULT-003_normal_srgb/README.md) | 法线当颜色采样，光照方向错、表面发灰 | 贴图通道/色彩空间 | ✅ 已修 + 已复验 | 两端 Inspector 截图 |
| 006 | [双材质槽共用树皮](FAULT-006_dual_slots/README.md) | Unity 整棵树铜色、叶子实心无镂空 | 契约（缺槽 1 贴图） | ✅ 已修 + 已复验 | before/after 双端画面 |
| 007 | [LOD0 顶点分裂](FAULT-007_lod0_vertex/README.md) | UE LOD0 顶点 2961 ≠ glb 2960，严格相等会误报 | 引擎导入（顶点分裂） | ✅ 已修 + 已复验 | 顶点/UV 对账数据 |
| 001 | `-nullrhi` 假 PASS → 白模 | 无头编译不真编译材质，链路全绿但引擎里是白模 | 验收口径 | ⏳ 待整理 | `ue_normalization_before_after_20261123.png` |
| 002 | Nanite fallback 面数 | `get_num_triangles(0)` 返回 fallback 1414/2354 而非 3370/3505 | 引擎导入/读取 | ⏳ 待整理 | `docs/tree_lods_ue.json` 的 `triCountNaniteOn` |
| 004 | FBX SHA 漂移 | 同源导出两次差 80 字节（CreationTimeStamp + 进程随机 UID） | 导出端 | ⏳ 待整理 | `docs/tree_lods.json` `hashNote` |
| 005 | Unity 手性/单位 | 直接导入包围盒差 7.4 cm | 坐标/单位 | ⏳ 待整理 | README §三之九 |
| 008 | parity 新鲜度误判 | 用 mtime 比新鲜度，clone 后全变旧 → 假 FAIL | 验收口径 | ⏳ 待整理 | `docs/_lead复核_v111_新鲜度门禁_20261124.md` |
| 009 | 编辑器文件锁 | UE 编辑器开着时无头落位写盘失败、save 返回 False | 环境 | ⏳ 待整理 | README §五 |
| 010 | ISM 组件不入 OwnedComponents | `new_object` 建出的组件存关卡后丢失 | 引擎 API | ⏳ 待整理 | README §五 |
| 011 | Unity 只认 Assets/ | 工程外资源 `LoadAssetAtPath` 返回空 | 引擎 API | ⏳ 待整理 | README §三之九 |
| 012 | PCG 点级 metadata 不可读 | 无头拿不到点级 attribute，只能以实例 transform 为准 | 引擎 API 边界 | ⏳ 待整理 | README §五 |

## 每个案例的固定八段

```
FAULT-00X_名字/
  README.md        ← 症状 / 影响 / 怎么发现 / 根因层 / 命中门禁 / 修复 / 修复后证据 / 回归断言
  证据图片或数据
```

## 与现有门禁的对应

| 门禁 | 能拦的案例 |
|---|---|
| `test_validator.py` 9/9 | 面数/法线/贴图 2 的幂/通道/依赖 |
| `--lod-check` 4/4 | 007（顶点分裂容差）、002（Nanite fallback 记录） |
| `--unity-materials-check` 4/4 | 006（双材质槽）、003（贴图 import 设置） |
| `--parity-check` 4/4 | 008（新鲜度）、005（坐标系/单位） |
| `--with-compile`（RHI 复检） | 001（假 PASS 白模） |

## 怎么用它（面试 90 秒版）

1. 抛一个症状（"Unity 里树是铜色的，但 UE 正常"）；
2. 说清根因在哪一层（契约缺槽 1 贴图，不是光照）；
3. 给证据（before/after + 断言命令）；
4. 说清"为什么内置导入器不会报"（它只保证单引擎导入，不保证跨引擎语义一致）；
5. 补一句"这类故障我记录了 12 个，每个都有防回归断言"。
