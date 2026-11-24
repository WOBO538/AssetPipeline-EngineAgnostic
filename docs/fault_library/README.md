# 跨引擎资产故障库（Fault Library）

> 一句话：同一份 manifest 契约，在两个引擎里出过的真实故障。每个案例八段，可复现、可回归。
> 用途：作品集 NO.10 的主证据；面试最容易被追问、也最好讲的一段。

## 索引（12 个真实案例，全部整理完）

| # | 案例 | 症状一句话 | 根因层 | 状态 |
|:--:|---|---|---|---|
| 001 | [无头编译假 PASS](FAULT-001_nullrhi_whitemodel/README.md) | -nullrhi 全绿，进编辑器是白模 | 验收口径 | ✅ 已修（v1.3） |
| 002 | [Nanite fallback 面数](FAULT-002_nanite_fallback/README.md) | 面数报 1414/2354，源是 3370/3505 | 引擎读取 | ✅ 改口径 |
| 003 | [法线贴图 sRGB](FAULT-003_normal_srgb/README.md) | 法线当颜色采样，光照方向错 | 贴图通道 | ✅ 已修 |
| 004 | [FBX SHA 漂移](FAULT-004_fbx_sha_drift/README.md) | 同源连导两次差 80 字节 | 导出端 | ✅ 已修 |
| 005 | [Unity 手性/单位](FAULT-005_unity_handedness/README.md) | 包围盒每轴差 7.4 cm | 坐标/单位 | ✅ 已修 |
| 006 | [双材质槽共用树皮](FAULT-006_dual_slots/README.md) | Unity 树铜色、叶子实心 | 契约 | ✅ 已修（v1.13） |
| 007 | [LOD0 顶点分裂](FAULT-007_lod0_vertex/README.md) | UE 顶点比 glb 多 1/3 个 | 引擎导入 | ✅ 改判据 |
| 008 | [新鲜度用 mtime](FAULT-008_freshness_mtime/README.md) | clone 后门禁误报"证据不新鲜" | 验收口径 | ✅ 已修（v1.11） |
| 009 | [编辑器文件锁](FAULT-009_editor_file_lock/README.md) | 编辑器开着，落位没存盘却 PASS | 环境 | ✅ 已修 |
| 010 | [ISM 组件不入 OwnedComponents](FAULT-010_ism_owned_components/README.md) | 建出的 ISM 组件存关卡后消失 | 引擎 API | ✅ 已修 |
| 011 | [Unity 只认 Assets/](FAULT-011_unity_assets_path/README.md) | 工程外贴图读出 null，材质是空的 | 引擎 API | ✅ 已修 |
| 012 | [PCG 点级 metadata 读不到](FAULT-012_pcg_metadata/README.md) | 点属性没有 Python 访问器 | API 边界 | ✅ 绕开（改口径） |

## 每个案例的固定八段

症状 / 影响 / 怎么发现 / 根因层 / 命中门禁 / 修复 / 修复后证据 / 回归断言，最后一段「为什么内置导入器不会报」。

文风按 `_文风公约.md`：第一人称、短句、先动作后结论、允许承认判断错。

## 与门禁的对应

| 门禁 | 能拦的案例 |
|---|---|
| `test_validator.py` 9/9 | 面数/法线/贴图 2 的幂/通道/依赖 |
| `--with-compile`（RHI 复检） | 001 |
| `--lod-check` 4/4 | 007、002 |
| `--unity-materials-check` 4/4 | 006、003 |
| `--unity-check` 4/4 | 003、005、011 |
| `--parity-check` 4/4 | 005、008 |
| 持久化断言（新进程重载） | 009、010 |

## 面试 90 秒讲法

1. 抛症状："Unity 里树是铜色，UE 正常"；
2. 说根因层："契约里少了槽 1 的贴图，不是光照"；
3. 给证据：before/after 图 + `--unity-materials-check` 输出；
4. 补一句为什么导入器不报："它只保证单引擎导入，不管跨引擎语义"；
5. 收尾："这类故障记了 12 个，每个都有防回归断言"。
