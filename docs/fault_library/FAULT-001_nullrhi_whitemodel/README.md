# FAULT-001 · 无头编译假 PASS，进了编辑器是白模

> 状态：✅ 已修（v1.3）
> 证据：`docs/ue_normalization_before_after_20261123.png`

## 症状

-nullrhi 跑完材质编译，日志没有 Failed to compile，断言全绿，链路判 PASS。打开 UE 编辑器，树是白模：材质没真正编译，贴图也没生效。

## 影响

无头链路给了假 PASS。资产过了检查，进编辑器不能用。这种错最怕的是没人打开编辑器看，于是它一直留在仓库里当"已完成"。

## 怎么发现

我一开始以为是贴图没挂。打开树逐项看材质和贴图，才发现是白模。回头查无头日志，确实没有编译失败。

## 根因层

-nullrhi 下 UE 不真正编译材质，材质图写错也不报错。原链路只证明流程跑完了。

## 命中门禁

原门禁看进程结果和日志。没有失败日志、断言全绿，就放行。它没检查材质编译有没有发生。

## 修复

链路末尾加了一次带 RHI 的无头复检，抓 Failed to compile Material。后来又补成三条件一起算：进程退出码 0、日志无 Failed to compile、MIC 数量等于材质槽数。任一不满足，直接 FAIL。

## 修复后证据

带 RHI 复检跑完，failed=0；编辑器里树是绿色，树皮有层次。

## 回归断言

```bash
python tools/run_pcg_chain.py --real-multi --with-compile --expect-assets 2 --expect-placements 1892
# 期望：CHAIN PASS，日志无 Failed to compile
```

## 为什么内置导入器不会报

导入完成不代表材质编译发生。无头能验数据，不能验"编译真的发生了"。
