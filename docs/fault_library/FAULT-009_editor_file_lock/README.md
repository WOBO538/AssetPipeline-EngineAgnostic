# FAULT-009 · 编辑器开着时跑无头落位，看着 PASS 其实没存盘

> 状态：✅ 已修（判据+操作规范）
> 证据：README §五；`save_current_level()` 返回值进断言

## 症状

UE 编辑器开着目标关卡，跑无头落位。断言全过，退出码 0。重开编辑器数实例，还是上一次的。

## 影响

"看起来 PASS 但没存住"的假结果。比直接报错更危险。

## 怎么发现

数量断言过了，但重开编辑器数字对不上。回去翻无头日志，看到写盘那步失败：

```
LogFileManager: Error Code 32
Failed to move ... to temp directory
```

## 根因层

环境。UE 编辑器拿着关卡文件，Windows 上无头进程写盘被文件锁挡住。

## 命中门禁

1. 链路启动前检查 `UnrealEditor.exe` 是否在跑；
2. `save_current_level()` 的返回值必须进断言，False 就是 FAIL；
3. "新进程重载关卡读回实例数 == placements"——持久化断言。

## 修复

跑链前关编辑器；把 save 的返回值当判据，不再只数内存里的实例。

## 修复后证据

关编辑器后重跑：save=True，新进程重载关卡读回 1892 == 1892。

## 回归断言

```bash
python tools/run_pcg_chain.py --real-multi --expect-assets 2 --expect-placements 1892
# 断言 6：新进程重载关卡读回实例数 == placements
```

## 为什么内置导入器不会报

跟导入没关系，是进程间的文件锁。任何想写关卡文件的脚本都会撞上。
