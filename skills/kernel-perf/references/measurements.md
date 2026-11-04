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

## 稳定性测量（stability.py，2026-11-04 15:5x，3 次重复）

vector_add，`--warm-up=5`，`launch-count=3`，3 次独立采集：

| 指标 | 均值 | CV% | 极差% | 判读 |
|---|---:|---:|---:|---|
| 核忙时间 | 3.0993 us | 0.31 | 0.61 | 稳定 |
| MTE2 占用 | 61.21% | 0.69 | 1.34 | 稳定 |
| Vector 占用 | 5.19% | 0.21 | 0.42 | 稳定 |
| Scalar 占用 | 25.26% | 2.41 | 4.75 | 稳定 |
| GM→UB 带宽 | 17.8484 GiB/s | 0.20 | 0.41 | 稳定 |
| L2 读命中 | 0.6248% | 0.00 | 0.01 | 稳定 |
| **L2 写命中** | 76.9353% | **9.57** | **18.78** | 可用（接近阈值） |
| Vector 等待 | 60.62% | 2.22 | 4.26 | 稳定 |

两次独立测量（5 次与 3 次）都指出同一件事：**L2 写命中率是唯一抖动的指标**（CV 14.4% 与 9.6%），其余全部 <3%。这支撑了把 `l2_write_hit` 定为 E 级、把 `l2_read_hit` 定为 C 级的划分——同一张表里读侧和写侧的可信度完全不同。

## 指标覆盖率（2026-11-04 15:3x）

CANN 自带 `analysis/common_func/constant.py` 的 `AI_CORE_CALCULATION_FORMULA` 定义 **74** 个可计算指标。用 `msopprof` 默认 7 组采集后：

| 口径 | 数量 |
|---|---:|
| CSV 实际暴露的字段总数 | 122 |
| 其中能对应上 CANN 计算口径的命名指标 | **7 / 53** |
| 通过 `visualize_data.bin` 额外拿到的行 | 34（UB 四向带宽、8 条 memory_path、逐核 L2、wait cycles…） |

未打通的典型项：`vec_bankgroup_cflt_ratio`、`mte*_iq_full_ratio`、`control_flow_prediction_ratio`、`acess_stack_ratio`。这些在 `msopprof` 的任何 `--aic-metrics` 取值下都没有输出通路——是工具未暴露，不是采集失败，报告里如实标注而不猜测数值。

## 逐指令耗时（2026-11-04 15:36）

`--aic-metrics=InstrTimeline --instr-timeline-pipe="vector|mte2|mte3"`，vector_add（`-g` 编译）：

```
探针指令点 28 个，trace 事件 97 条，已归属耗时 1.423 us（kernel 4.2 us，覆盖 34%）
```

按 opcode 聚合：

| opcode | 累计 us | 占比 |
|---|---:|---:|
| `MOV_UB_TO_OUT_ALIGN_V2` | 1.3248 | 93.1% |
| `GET_BUF` | 0.0254 | 1.8% |
| `RLS_BUF` | 0.0254 | 1.8% |

按 PC 聚合后最热点是 `0x824` 的 `MOV_UB_TO_OUT_ALIGN_V2`——即写回搬运，与流水层面「MTE2 主导」的结论互补：MTE2 是**读**的主导，写侧真正的耗时集中在这一条指令上。

## 采集分趟的必要性（2026-11-04 15:4x）

| 组合 | 结果 |
|---|---|
| `InstrTimeline,PipeTimeline` | 立刻报错：`'PipeTimeline' and 'InstrTimeline' cannot be specified together` |
| `InstrTimeline` + 8 个核心组 | **挂起**（不报错，长时间无输出，最终被杀） |
| `InstrTimeline` 单独 | 正常，约 1–4 分钟，产出 25 MB `timeline.bin` + 189 MB `OperandRecord.bin` |

所以工具自动拆趟：核心组一趟、`InstrTimeline` 一趟、`PipeTimeline` 一趟。

## dfx_region_map.txt 的生命周期

`dfx_region_map.txt`（pc→pipe/opcode）由 `msopprof` 在采集阶段写出，但**后续解析步骤会把它删掉**。实测：同一 capture 在采集刚结束时存在，几分钟后再看已消失。工具在 `run_msopprof` 返回后立即把它和 `pc_start_addr.txt` 复制到 `capture/probe/`，指令级分析优先读这个副本。

## 双 DIE 与跨 die 通路（2026-11-04 16:3x）

platform_config 里 `[DieInfo] io_die_num=2`，确认 Ascend950PR 是双 die。L2 跨 die 交织，访问对端
L2 走 SIO。

`visualize_data.bin` 的 `core_memory_map[].memory_unit[]` 在不同 kernel 上暴露出不同的 `memory_path` 集合：

| 出现场景 | path id |
|---|---|
| vector 算子（vector_add） | 0, 1, 12, 13, 14, 15, 48, 52, 54 |
| cube 算子（matmul mxfp4） | 0, 1, 2, 6, 7, 8, 37, 38, 39, 40, 41 |

cube kernel 实测：

| path | 带宽 GB/s | 请求数 | 判定 |
|---:|---:|---:|---|
| 8 | 36.188 | 91 | 跨 die（推断） |
| 40 | 24.208 | 487 | GM 读（cube 侧） |
| 2 | 3.330 | 67 | 跨 die（推断） |
| 0 | 3.281 | 66 | GM 读（本 die） |
| 1 | 3.032 | 61 | GM 写（本 die） |
| 38 | 2.585 | 26 | 跨 die（推断） |
| 6 | 2.262 | 91 | 跨 die（推断） |
| 37 | 1.392 | 14 | 跨 die（推断） |
| 7 | 1.292 | 13 | 跨 die（推断） |
| 39, 41 | 0.000 | 0 | 恒 0，保留 |

跨 die 合计 47.05 GB/s，片内 30.52 GB/s，**跨 die 占 60.6%**。

vector 算子上这组跨 die path 几乎不出流量（<3.3 GB/s），说明影子取决于访存模式而非芯片固定行为。

**path id 含义 CANN 未公开文档**。命名依据：0/1 与 GM↔UB 主流量吻合；12–15 与逐核 `UB` 表的四向带宽
逐字节相同；39/41 在所有采集里恒 0；2/6/7/8/37/38 只在 cube kernel 出现且有流量。因此这些名字是
**推断**，工具的置信度给 C 而非 A。

## sector / coalescing / outstanding / snoop 的可用性

对 `msopprof`、`libmsprofiler.so`、各 profiler 插件逐个 strings 检索：

| 概念 | 结论 |
|---|---|
| `sector` / coalescing 效率 | 无任何计数器或字符串 |
| `outstanding` / `inflight` | `visualize_data.bin` 全文 0 命中 |
| `snoop` | 无 |
| `instr_popped`（Load/Store 占比） | **存在于 msopprof**，但 `--dump=true` 要求 `--aic-metrics=TimelineDetail`，而后者在 dav-3510 上报错 `Unexpected argument --dump, maybe in wrong soc platform` |

## Scalar Story 与 simd_vf_story 的观测项对照

读 `cann-samples/Samples/2_Performance/scalar_story` 与 `simd_vf_story` 后，逐条对照本工具：

| story 提到的观测点 | 本工具 | 说明 |
|---|---|---|
| 各流水 cycle 分布 | ✅ | PipeUtilization |
| Scalar 占比 | ✅ | scalar_ratio |
| icache miss（PC-Cycle 图） | ✅ 部分 | InstrTimeline 的 pc_addr+ts 可画；需 `--with-instructions` |
| dcache miss | ✅ | DCache 表（本次新增） |
| Load/Store 指令占比 | ❌ | 需 instr_popped dump，上板不可用 |
| 寄存器 spill | ❌ | 同上，由 Load/Store 占比间接推断 |
| 软件头开销 (cycle) | ⚠️ | 只能从 scalar 时间间接看 |
| VF count / PUSHQ VF cycles / RVECEX / RVECLD / RVECSU / PUSH_PB | ❌ | 仅 cannsim trace；上板只有 `aiv_vec_vf_ratio` 汇总 |
| MTE2/MTE3 sum_cycles | ✅ | 已有 |
| bank 冲突（vf_data_transform_story） | ✅ | ResourceConflictRatio |

## PC-vs-cycle（icache miss 诊断）实测（2026-11-04 16:4x）

InstrTimeline 的 `trace.json` 同时带 `args.pc_addr` 与 `ts`，这是 scalar_story「手段一」需要的两个字段。
`layers.analyse_pc_cycle()` 把「同一 PC 连续出现且间隔 ≥ 阈值」识别为停留（等价于 PC-Cycle 图上的水平线段）：

vector_add（`-g` 编译，71 事件，8 个不同 PC，跨度 0.7115 us）：

```
stalls = 0，total_stall_us = 0.0，stall_pct = 0.0
```

**这是诚实结果**：该 kernel 短、代码小，本来就没有 icache miss。工具如实报 0 而不是凑一个数。
要验证检测器本身有效，用合成轨迹（同一 PC、间隔 0.4us）→ 命中 1 处、占 80%，见 `selftest.py`。

阈值取绝对 0.05 us 而非「事件时长的倍数」：插桩事件的 `dur` 在纳秒级，比值法会把每一对都判成停留。
