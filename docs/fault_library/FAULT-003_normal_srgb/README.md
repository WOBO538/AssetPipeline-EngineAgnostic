# FAULT-003 · 法线贴图被当颜色采样（sRGB）

> 状态：✅ 已修复、已复验（2026-11-24）
> 证据：`ue_inspector.png`（UE 纹理资产详情）、`unity_inspector.png`（Unity Import Settings）

## 1. 症状

- UE：法线贴图资产被按颜色空间采样，光照方向感错、表面发灰/发亮，材质"看起来脏"。
- Unity：贴图导入类型不是 Normal map，光照同样不对。
- **关键点：所有数量断言（面数/槽数/贴图数/路径）全绿**——肉眼才看得出。

## 2. 影响

材质观感错，但链路判据不报警 → 属于"数据对、画面错"的典型跨引擎语义故障。

## 3. 怎么发现

无头断言只验"贴图挂上了"，不验"按什么色彩空间采样"。只能在编辑器里看 Inspector：
UE 看 **压缩设置**（应为 `TC_Normalmap`/BC5）与 **sRGB 是否勾选**（必须不勾）；Unity 看 **Texture Type**（应为 Normal map）。

## 4. 根因层

**贴图通道 / 色彩空间**（契约层）：manifest 里每张贴图带 `srgb` 字段，消费端必须按字段设 import 设置；漏一个就静默错。

## 5. 命中门禁

- v1.6 断言：UE `TC_Normalmap` + `srgb=False`；Unity `TextureImporterType.NormalMap`。
- 人工项：两端 Inspector 截图（已归档，见证据）。

## 6. 修复

- manifest：`textures.Normal.srgb = false`（契约显式化）。
- UE：导入时设 `TC_Normalmap`、关 sRGB（BC5 压缩）。
- Unity：`TextureImporterType.NormalMap`（自动 DXT5nm）。

## 7. 修复后证据（本目录）

| 图 | 内容 |
|---|---|
| `ue_inspector.png` | UE：压缩设置 = 法线贴图（BC5）、sRGB 未勾选、格式 BC5、1024²、Mip 11 |
| `unity_inspector.png` | Unity：Texture Type = Normal map、DXT5nm、1024×1024 |

## 8. 回归断言

```bash
python tools/run_pcg_chain.py --unity-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer"
# 期望：UNITY-CHECK PASS ... textures=4/4
```

外加人工清单（每次换资产源都要过一遍）：UE 压缩设置 / sRGB；Unity Texture Type / sRGB。

## 9. 为什么内置导入器不会报

引擎导入器只保证"这张图能导入"，不保证"它被按什么语义使用"；跨引擎时，两端默认值不同（Unity 默认 sRGB on、Normal map 需显式指定），所以必须由契约层规定并在两端断言。
