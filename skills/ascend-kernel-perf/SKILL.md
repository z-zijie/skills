---
name: ascend-kernel-perf
description: 在昇腾 NPU（含 Ascend950PR / dav-3510）上对指定 kernel 做硬件细节级的性能分析：给一个可执行脚本和 kernel 名，产出每核每流水的占用、带宽、cache 命中、bank 冲突、Rooffline 归属与逐指令时间线，并把指标换算成占硬件峰值的百分比。同时自带对抗校验（critic）与采集配置搜索，保证报出来的数字经得起质疑。当用户要"分析某个 kernel 的性能""看某个算子慢在哪""拿 kernel 的详细性能指标""对比两次 kernel 性能"时使用。
---

# 昇腾 Kernel 硬件级性能分析

一条命令，把一个可执行脚本里指定 kernel 的全部硬件计数拉出来，换算成"占峰值多少"的可判读形式，再用一个不信任数字的批判者验一遍。

## 用法

```shell
# 最常用：给定二进制 + kernel 名，采集 + 分析 + 校验
python3 scripts/kernel_perf.py profile \
    --app ./vector_add --kernel add_kernel --out ./perf_out

# 校验这次采集和报告（质疑者视角，13 条恒等式/一致性/物理边界检查）
python3 scripts/critic.py --out ./perf_out --report ./perf_out/report.md

# 不知道哪套采集参数最好时，让搜索自己找（critic 作 reward 模型）
python3 scripts/collect_optimize.py --app ./vector_add --kernel add_kernel --out ./opt

# 已采过一遍，只重新解析（秒级）
python3 scripts/kernel_perf.py analyze --out ./perf_out

# 机器可读全量指标
python3 scripts/kernel_perf.py profile --app ./x --kernel foo --out ./o --json

# 优化前后对比
python3 scripts/kernel_perf.py diff ./before ./after

# 不需要 NPU 的自测（30 项，含反例）
python3 scripts/selftest.py
```

`--app` 后面可以带程序自己的参数。`--kernel` 支持子串匹配（`add_kernel` 匹配 `_Z10add_kernelIfEvPhS0_S0_llj`）。

## 前置

- 昇腾 NPU 一台，CANN 已装（脚本自己 source `set_env.sh`，不依赖登录 shell）
- `msopprof` 在 `$ASCEND_HOME_PATH/tools/msopprof/bin`
- 采集建议独占设备：别人的任务会让计数抖动，脚本会先检测并警告

## 它采什么

`msopprof` 一次采集拿 7 组 AIC/AIV 计数（CSV）+ 一份 `visualize_data.bin`（逐核明细表）。后者是 `msopprof` 自己 HTML 报表的数据源，本工具直接解它，因此能拿到 CSV 里没有的东西：

| 来源 | 内容 |
|---|---|
| `PipeUtilization.csv` | 每核每流水占用时间/占比、MTE2/MTE3 活跃带宽 |
| `Memory.csv` | GM↔UB/L1/L0C 数据量、`*_bw_usage_rate(%)`＝相对硬件峰值 |
| `MemoryUB.csv` `MemoryL0.csv` | UB / L0A / L0B / L0C 读写带宽 |
| `L2Cache.csv` | close/far × hit/miss/victim 六类计数、读写命中率 |
| `ResourceConflictRatio.csv` | bank 冲突（stu/ldu/sfu）、各流水 wait 占比 |
| `ArithmeticUtilization.csv` | cube/vector 计算占比、cube 浮点/整型指令数 |
| `visualize_data.bin` | 逐核 L2 命中明细、iCache 命中、8 条 memory_path 的带宽、Pipe 表的 wait cycles、UB GM/Vector 四向带宽、Roofline 归属与官方 advice |
| `trace.json`（可选） | 逐流水时间线事件（`PipeTimeline`/`InstrTimeline`） |

容器格式、单位约定、以及每个 metric 集合在 dav-3510 上的可用性，见 `references/dav3510-notes.md`。

## 输出

`<out>/report.md`（人读）＋ `<out>/metrics.json`（全量，含每个核的原始值）＋ `<out>/capture/`（原始采集，归档保留）＋ `<out>/collect.log`。

report.md 分九段：

1. **结论**：耗时、block dim、主导流水、Roofline 归属、最高带宽峰值占比
2. **流水占用**：每条流水的平均占用与活跃核数
3. **带宽与峰值占比**：每个通道除以反解出的峰值
4. **Cache 行为**：L2 读写命中、iCache miss
5. **冲突与等待**：bank 冲突、各流水 wait 占比与累计 wait cycles
6. **核间负载**：离散度 `(max-min)/mean`
7. **内存通路**：8 条 memory_path 的带宽与请求数
8. **逐核明细**：每核时间、主导流水、带宽、命中率
9. **逐指令时间线**（采集了才出现）

## 为什么数字可信

三个机制，缺一不可：

**峰值反解，不用 datasheet 值。** `Memory.csv` 同时给"搬了多少"和"占峰值百分之几"，联立反解出 `msopprof` 实际用的分母（实测 163.13 GB/s）。平台 ini 里的 `ddr_rate`/`l2_rate` 是无量纲等级，不能当分母——工具只把 ini 值作为参考值单列，从不参与百分比计算。

**单位按实测校准。** `*_datas(KB)` 是 KiB、`*(GB/s)` 是 GiB/s。用十进制 GB 会让每个百分比偏 4.9%，足以把"还有余量"读成"接近极限"。反解过程留在 `references/measurements.md`。

**批判者会否决。** `critic.py` 跑 13 条检查：4 条恒等式（ratio==time/total、cycles/time==freq、带宽单位、峰值回代）、4 条跨源一致性（跨表时间、visualize vs CSV 核数与命中率、CSV 内部）、2 条物理边界（占比 ≤100%、流水时间合计有界）、3 条覆盖（采集非空、advice 存在、报告含逐核明细）。任何一条失败，报告里的数字都不可信。

`collect_optimize.py` 把批判者当 reward 模型：reward = 通过数 − 3×失败数 + 覆盖度。失败项权重压过任何覆盖度收益，所以"数据多但错"的配置永远赢不了。实测结果见 `references/measurements.md`。

## 判读

阈值与算子类型预期见 `references/interpretation.md`。核心三条：

- 主导流水 >60% 且峰值占比 >70% → 接近硬件极限，优化要看算法
- 没有任何流水 >50% → 流水线气泡，查 `*_wait_ratio` 与 double buffer
- 核间耗时差 >30% → tiling 切分问题，与流水优化无关

**别按 op_type 猜瓶颈**：实测一个 `cube` 类型的 matmul，cube 流水只占 0.08%、scalar 占 92.5%（小 shape 下全花在标量准备）。归类一律按实测占比。

## 默认不碰的项

- `TimelineDetail` 在 dav-3510 上直接失败；`Source`/`PCSampling` 需要 `-g` 重编。要时间线用 `--metrics` 显式指定 `PipeTimeline`。
- 一次采集不超过 7 组 metric：11 组合并会让所有计数变成 `NA` 却仍返回 0。工具会把它标成 `no data`。
- 报告里的每个数字都能追到一条 CSV 字段、visualize 的一个 JSON 字段，或一个 ini 键。
