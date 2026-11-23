# TRAE 任务单 · EAP 多资产 manifest（W2 PCG 最小闭环最后一格）

> 交给 TRAE 直接执行；执行完把「自验命令的实际输出」贴回来，不要只贴结论。
> 仓库：`D:\Projects\AssetPipeline-EngineAgnostic`（有 git，每单元一次 commit，message 用英文）。
> 写这单的人：lead；验收人：lead（会重跑下面的命令并看 diff）。

---

## 0. 背景（只读，不需要改）

- 项目是「PCG/AIGC 生成 → Python 中台 → UE5/Unity 双端消费」。
- 现在链路只支持 **1 个网格 = 1 个 asset + N 条 placements**（见 `docs/manifest-spec.md`、`schemas/manifest.schema.json`）。
- W2 的验收缺口：**一份 20 个资产的 manifest 要通过校验**。所以要把「多网格」打通：一个散点文件里混了几种网格（比如 3 种树），每种网格出一个 asset，各自带自己的 placements。
- 已有的 9/14 条断言链路必须**不回归**：`python tools/run_pcg_chain.py --tree` 仍是全 PASS。

---

## 1. 要改/新增的文件

| 文件 | 动作 | 要点 |
|---|---|---|
| `tools/pcg_to_manifest.py` | 改 | 输入散点 JSON 里若出现多个不同 mesh，输出 `assets[]` 多条；每条 asset 只带属于它的 placements。**单网格输入必须保持现在的输出字节级等价**（改完先拿 `docs/pcg_forest_pilot.json` 跑一遍和 `schemas/pcg_manifest.json` diff，允许只有时间戳/生成时间字段不同） |
| `tools/make_multi_asset_fixture.py` | 新增 | 生成**合成**散点 fixture：20 个不同网格名（`SM_Prop_A01..A20` 之类）、总实例 ≥ 100、坐标在 0–20000 cm 范围、yaw 0–360、scale 0.8–1.2；输出 `docs/pcg_multi_fixture.json`。**不要用随机种子让结果不可复现**，固定 seed 并写进文件 |
| `tools/validate_manifest.py` | 改（如需） | 确保多资产 manifest 在现有 schema 下 errors=0；新增一条「每个 asset 的 `instance_count` 与 `placements.count` 相等」若还没有 |
| `ue/Content/Python/place_manifest.py` | 改 | `place()` 支持多资产：一个 manifest 里多个 asset → 每个 asset 一个 ISM 组件（挂在同一 Actor 下），组件名可预测（如 `EAP_Asset_<index>`）；**幂等**：重跑先清空再建。`verify()` 读回后逐 asset 对比计数与 transform（位置误差 < 1e-3 m、旋转误差 < 0.1°、缩放误差 < 1e-3） |
| `tools/run_pcg_chain.py` | 改 | 加 `--multi` 模式：fixture → manifest → validate → UE 落位 → 读回校验；断言里至少加 4 条：`assets==20`、`placements_total` 与 fixture 计数一致、每个 asset 读回数量一致、坐标误差达标。不加 `--multi` 时行为**完全不变** |
| `README.md` + `board/status.json` | 改 | README 加一节「多资产 manifest」写清命令与断言数；board 里把「双树种混排（多资产 ISM）」标 done 日期 |

---

## 2. 不许动的东西

1. **manifest v1.1 已有字段的语义**（`assets[].placements`、单位 m、`instance_count` 规则）——只许加字段，不许改口径。要加字段必须先写进 `docs/manifest-spec.md`。
2. **UE 无头不生成 PCG 的结论**：`PCGComponent.generate()` 在 `-unattended -nullrhi` 下无效，这条已实测，不要试图"修"它。
3. 已有断言（9 条 / `--tree` 时 14 条）一条都不许删、不许改判据来让新代码通过。
4. 不许伪造输出：跑不过就贴 traceback 和你的判断，别改断言数字。

---

## 3. 自验命令（必须逐条跑，输出贴回来）

```powershell
cd D:\Projects\AssetPipeline-EngineAgnostic

# 1) 不回归：老链路
python tools/run_pcg_chain.py --tree --expect-span-m 200

# 2) 合成 fixture → 多资产 manifest
python tools/make_multi_asset_fixture.py --out docs/pcg_multi_fixture.json
python tools/pcg_to_manifest.py --scatter docs/pcg_multi_fixture.json --out schemas/pcg_manifest_multi.json
python tools/validate_manifest.py schemas/pcg_manifest_multi.json

# 3) 多资产链路（含 UE 无头落位 + 读回）
python tools/run_pcg_chain.py --multi --expect-assets 20
```

期望：第 1 条与现在一致（全 PASS）；第 2、3 条末行打印 `CHAIN PASS assets=20 placements=<N>`，且 `python tools/validate_manifest.py` 输出 `errors=0`。

---

## 4. 环境（已装好，别自己升级）

- UE：`C:\Program Files\Epic Games\UE_5.7`；工程：`D:\UE_Projects\VegetationPCG`
- Python 3.11/3.12 均可（仓库 `requirements.txt` 只有两条）
- 拿不到 UE（`Engine\Binaries\Win64\UnrealEditor-Cmd.exe` 不存在）→ 先只交付「fixture + manifest + validate + 断言」三件，UE 那步留一个 `--skip-ue` 开关并说明，别硬等。

---

## 5. 交付清单

- [ ] 代码 commit 一次（英文 message，如 `feat: multi-asset manifest (20 assets) end-to-end`）
- [ ] 上面 4 条命令的实际输出（原样粘贴）
- [ ] `schemas/pcg_manifest_multi.json` 落盘
- [ ] README 新增小节 + `board/status.json` 里对应项打勾
- [ ] 已知问题写进 README 的"已知限制"小节，不要藏

---

*任务单由 lead 于 2026-11-23 18:50 生成。任何一条需求你觉得没法在现有 schema 下实现，先回一条说明，不要自己改 schema。*
