# 实测记录（Ascend950PR / dav-3510）

本文件保存工具结论背后的原始数字。每次复测都追加，不覆盖——阈值如果漂了，原因要能追到具体一次采集。

环境：`Ascend950PR_9579`，CANN `9.3.0~weekly.20261009.01`，`cube_freq=vec_freq=1650 MHz`，28 AIC / 56 AIV，UB 248 KiB，L1 512 KiB，L0C 256 KiB，L2 128 MiB。

## 采集 1 — vector_add（2026-11-04 14:36）

被测二进制：`cann-samples/build/Samples/0_Introduction/vector_add/vector_add`
参数：`msopprof --warm-up=2`，默认 7 组 metric。kernel `_Z10add_kernelIfEvPhS0_S0_llj`，409600 float32。

```
Op Type: vector   Task Duration: 9.965 us   Block Dim: 56
Current Freq: 1650   Rated Freq: 1650
```

| 流水 | 平均占比 |
|---|---:|
| AIV MTE2 | 51.84% |
| AIV scalar | 23.89% |
| AIV MTE3 | 17.74% |
| AIV vec | 8.26% |

- GM→UB 实测 28.41 GB/s，`GM_to_UB_bw_usage_rate` 平均 17.4%，反解峰值 **163.13 GB/s**
- UB 读(vector) 20.99、写(vector) 20.50 GB/s
- L2 读命中 0.99%、写命中 59.52%，读 close miss 合计 2365
- iCache 命中 100%（逐核表 `iCache Total` 141/141）
- bank 冲突：stu 3.26%、ldu 2.23%、sfu 0%
- 等待：Vector 43.73%、MTE3 49.47%
- 核间耗时 1.453–2.873 us，均值 1.974 us，**离散度 71.9%**

厂商 advice：`MTE2 bandwidth utilization lower than 80% when active`、`aivector compute usage lower than 20%`。

## 采集 2 — matmul mxfp4 base（2026-11-04 14:50）

被测二进制：`matmul_tutorial_mxfp4_base 100 50 200`，kernel `_ZN6Kernel26QuantMatmulMxfp4BaseKernelEmmmPhS0_S0_S0_S0_`。

```
Op Type: cube   Task Duration: 3.051 us   Block Dim: 28
```

| 流水 | 平均占比 |
|---|---:|
| AIC scalar | 92.51% |
| AIC fixpipe | 2.42% |
| AIC MTE2 | 1.07% |
| AIC MTE1 | 0.36% |
| AIC MTE3 | 0.28% |
| AIC cube | 0.08% |

- 反解峰值：`GM_to_L1` 174.77 GB/s、`L0C_to_GM` 139.81 GB/s
- L2 读命中 89.34%（cube 侧）

**这个样本的价值**：它是"cube 算子"但 cube 流水几乎不忙（0.08%），92.5% 花在标量上。说明算子类型和实际瓶颈是两件事——报告必须按实测占比归类，不能按 op_type 猜。

## 采集 3 — 11 组 metric 合并（失败样本，2026-11-04 14:43）

`--aic-metrics` 同时给 11 组（core7 + BasicInfo + Roofline + Occupancy + KernelScale）：

```
msopprof 返回 0，日志打印 "All task success"
OpBasicInfo 有 Task Duration = 6.936 us、Block Dim = 56
但全部 7 份 CSV 的所有计数列都是 NA
```

**这是最危险的失败形态**：退出码 0、日志成功、CSV 结构完整、`Task Duration` 还有值。只有逐列检查"有没有任何一个非零流水占比"才能识别。工具用 `collection_nonempty` 检查捕获它，`roofline.class` 标为 `no data (all pipe counters empty)`。

结论：一次采集不要超过 7 组 metric。

## 各 metric 集合的可用性（2026-11-04 14:37–14:44）

对同一个 `vector_add` 逐个单独采集：

| metric | rc | 耗时 | 结果 |
|---|---:|---:|---|
| Default | 0 | 22s | 7 份 CSV 全套 |
| BasicInfo | 0 | 35s | 仅 OpBasicInfo.csv |
| Roofline | 0 | 35s | 7 份 CSV + `0x0D` roofline 记录（`latency bound:pipeline caused`） |
| Occupancy | 0 | 37s | OpBasicInfo + visualize_data（含逐核 advice） |
| KernelScale | 0 | 35s | 7 份 CSV |
| Source | 0 | 36s | 需 `-g`；否则 "Failed to parse pc to code" |
| PCSampling | 0 | 37s | 同上，hot spot 计算失败 |
| MemoryDetail | 0 | 10s | 7 份 CSV，但 Task Duration 4.311us 明显异常（replay 语义不同） |
| TimelineDetail | 0 | 24s | **失败**："Parse kernel data failed"，CSV 全空 |
| PipeTimeline | 0 | 45s | `trace.json`，618 个 `X` 事件，仅 `core1.veccore0` |
| InstrTimeline | 0 | 55s | `trace.json`，71 个事件（GET_BUF/RLS_BUF/MOV_UB_TO_OUT_ALIGN_V2） |

`PipeTimeline` 的 trace 只包含单个 core 的 SCALAR/MTE2/VECTOR/MTE3 累计（42.4 / 22.0 / 4.5 / 9.0 us），不是全核时间线——报告里要标明"取到的是哪个 core"。

## 恒等式校验结果（工具 `critic.py` 每次运行都重算）

| 检查 | 实测结果 |
|---|---|
| `ratio == time/total_time` | 224 对，0 处不符（容差 1e-3） |
| `cycles/time == freq` | 56 组，最大偏差 0.0 MHz |
| KiB→GiB 带宽换算 | 56 对，最大相对误差 **3.06e-07** |
| 反解峰值回代 vendor % | 56 次，最大绝对误差 **3.55e-15** 个百分点 |
| 跨表 `aiv_time` 一致 | 280 个值，0 处不符 |
| visualize 核数 vs CSV 行数 | 56 vs 56 |
| 流水时间合计 / 核时间 | 最大 1.073（流水可重叠，故允许 >1） |

最大误差都在 1e-7 量级，说明这几条是真正的恒等式而非近似，可以当断言用。

## 单位反解过程（留档）

`Memory.csv` 首行：`aiv_time=2.267879 us`、`GM_to_UB_datas(KB)=57.375`、`aiv_gm_to_ub_bw(GB/s)=24.126980`、`GM_to_UB_bw_usage_rate(%)=14.790032`。

| 假设 | 算得 GB/s | 与公布值之比 |
|---|---:|---:|
| KB=1000 B, GB=1e9 | 25.2990 | 1.0486 |
| KB=1024 B, GB=1e9 | 25.9061 | 1.0737 |
| **KB=1024 B, GB=1024³** | **24.1270** | **1.000000** |
| KB=1000 B, GB=1024³ | 23.5615 | 0.9766 |

只有 KiB + GiB 完全吻合。用 GB=1e9 会让每个峰值占比偏 4.9%——足以把"还有余量"误判成"接近极限"。

## 配置搜索（collect_optimize.py，2026-11-04 15:03）

对同一个 `vector_add` 跑 6 个候选配置，reward = 通过数 − 3×失败数 + 覆盖度/1000（上限 5）。critic 为 reward 模型。

| 配置 | replay | launch | reward | pass | fail | coverage |
|---|---|---|---:|---:|---:|---:|
| **core7+roofline** | kernel | 1 | **18.87** | 15 | 0 | 3874 |
| core7 | kernel | 1 | 18.86 | 15 | 0 | 3864 |
| core7-launch5 | kernel | 5 | 18.86 | 15 | 0 | 3864 |
| core7+occupancy | kernel | 1 | 18.86 | 15 | 0 | 3864 |
| core7+timeline | kernel | 1 | 18.86 | 15 | 0 | 3864 |
| core7-appreplay | **application** | 1 | — | — | — | 采集为空 |

三个结论：

1. **`--replay-mode=application` 在本机采不到数据**（0 秒返回空）。这条不能当作"Kernel replay 失效时的兜底"，至少 dav-3510 + CANN 9.3.0 上不成立。`kernel_perf.py` 的默认值因此保持 `kernel`。
2. `core7+roofline` 以 coverage 3874 vs 3864 胜出：多花一次采集（约 11s），换来厂商 Roofline 判定和 8 条 memory_path 明细。已设为工具默认 metric 集合。
3. `launch5`（5 轮取均值）的 coverage 与单轮完全相同，耗时也相同（11s）——多次 launch 并没有带来更多字段，只影响数值平滑。默认仍用 1 轮，需要稳定性时再手动加。

设备当时有 1–2 个其他进程，critic 的 15 条检查全部通过，说明这些恒等式对计数抖动不敏感（它们是算术关系，不是阈值判断）——这正是用恒等式而不是用"经验范围"做校验的理由。
