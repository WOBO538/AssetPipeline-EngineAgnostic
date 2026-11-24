# FAULT-003 · 法线贴图被当颜色采样

> 状态：✅ 已修、已复验（2026-11-24）
> 证据：`ue_inspector.png`、`unity_inspector.png`

## 症状

法线贴图按颜色空间采样了。光照方向感不对，表面发灰，材质看着脏。两端都可能中招：UE 是 sRGB 没关，Unity 是贴图类型不是 Normal map。

## 影响

数量断言全绿——面数、槽数、贴图路径全对——但画面是错的。属于"数据对、画面错"。

## 怎么发现

无头验不了这个。进编辑器看 Inspector：UE 看压缩设置和 sRGB 勾选，Unity 看 Texture Type。第一次是顺手看了一下，才发现 UE 那边 sRGB 还勾着。

## 根因层

贴图通道与色彩空间。manifest 里每张贴图都有 srgb 字段，消费端得按字段设 import 设置。漏一个就静默错。

## 命中门禁

v1.6 断言：UE 要 TC_Normalmap + srgb=False，Unity 要 TextureImporterType.NormalMap。另外留了人工项：两端 Inspector 截图。

## 修复

manifest 里把 Normal 的 srgb 写成 false；UE 导入时设 TC_Normalmap、关 sRGB；Unity 设成 Normal map 类型（DXT5nm）。

## 修复后证据

UE：压缩设置"法线贴图（BC5）"、sRGB 未勾选、1024²。Unity：Texture Type = Normal map、DXT5nm。两张截图在目录里。

## 回归断言

```bash
python tools/run_pcg_chain.py --unity-check --unity "<Unity.exe>" --unity-project "D:\unity\EAP_Unity_Consumer"
# 期望：UNITY-CHECK PASS ... textures=4/4
```

换资产源时要重过一遍：UE 压缩设置 / sRGB；Unity Texture Type / sRGB。

## 为什么内置导入器不会报

它只保证"这张图能导入"，不管"它被按什么语义使用"。两端默认值不一样（Unity 默认 sRGB 开、Normal map 要显式指定），所以得由契约规定、两端各自断言。
