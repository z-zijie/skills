# 样例：对 cann-samples 的 vector_add 跑一遍

这不是示意，是 2026-11-04 在 Ascend950PR 上的实际运行记录。命令、输出、产物结构都按真实结果抄录，数字可以拿去复核。

## 被测对象

```
/workspace/cann-samples/build/Samples/0_Introduction/vector_add/vector_add
kernel: _Z10add_kernelIfEvPhS0_S0_llj   （--kernel add_kernel 子串匹配）
```

## 一条命令

```shell
python3 scripts/kernel_perf.py profile \
    --app /workspace/cann-samples/build/Samples/0_Introduction/vector_add/vector_add \
    --kernel add_kernel \
    --out ./demo \
    --metrics=PipeUtilization,ArithmeticUtilization,Memory,MemoryUB,MemoryL0,L2Cache,ResourceConflictRatio,Roofline,PipeTimeline
```

`PipeTimeline` 与核心组互斥，工具自动拆成两趟：先采 8 组核心指标，再单独采 `PipeTimeline`。日志：

```
[warn] 设备上有 2 个其他进程，采集数据可能抖动
[info] 分 2 趟采集（这些 metric 集合无法合并）→ ./demo/capture
[info]   第 1 趟: ['PipeUtilization', 'ArithmeticUtilization', 'Memory', 'MemoryUB',
                   'MemoryL0', 'L2Cache', 'ResourceConflictRatio', 'Roofline']
[info]   第 2 趟: ['PipeTimeline']
```

## 产出什么

四种东西，用途不同：

| 产物 | 内容 | 谁用 |
|---|---|---|
| `report.md` | 13 节人读报告，所有指标带置信度 | 人看结论 |
| `metrics.json` | 全量结构化数据，含每核原始值 | Agent / 脚本再度分析 |
| `capture/pass0_core/OPPROF_*/` | msopprof 原始 CSV + `visualize_data.bin` | 复核、归档 |
| `capture/pass1_pipetimeline/` + `capture/extra/` | `trace.json` 与 `probe/`（region map） | block 时间轴、逐指令 |

没有 CSV/JSON 二选一：**原始 CSV 保留**，报告与 JSON 是在其上算出来的。

## 实际报出来的东西

### 结论（第 1 节）

```
耗时 4.285 us，Block Dim 56，频率 1650 MHz
Roofline 归属：memory bound（主导流水 aiv_mte2 60.15%）
厂商判定：latency bound:pipeline caused
最高带宽峰值占比：10.9%
```

### 关键指标（第 2 节）

| 指标 | 值 | 说明 |
|---|---:|---|
| MFU | n/a | 没有 cube 指令，vector 算子不适用 |
| MAC usage | n/a | cube 流水未活动 |
| 受限因素 | aiv_mte2 | MTE2 占 60.1%，最忙的单元 |
| Cube 理论峰值 | 378.47 TFLOPS | 28 AIC × 1650 MHz × 16³ MAC × 2 |

MFU 报 `n/a` 而不是 0：vector 算子本来就没有矩阵乘，给 0 会被误读成"效率极低"。

### 流水（第 3 节）

| 流水 | 平均占用 |
|---|---:|
| AIV MTE2 (GM→UB) | 60.15% |
| AIV scalar | 26.27% |
| AIV MTE3 (UB→GM) | 11.86% |
| Vector 计算 | 5.16% |

数据搬运占大头、vector 计算只占 5%——elementwise 算子在大 shape 下的典型形态，瓶颈在搬不在算。

### block 时间轴（第 7 节，调度层）

这是 Triaged 的"多核启动不同步/拖尾"那一层：

```
采集到 12 个 block 的时间轴（来源 trace.json (PipeTimeline)）
首个 block 启动 0.000 us，最后一个启动 0.864 us
```

| 指标 | 值 | 含义 |
|---|---:|---|
| 启动偏移 (launch skew) | 0.864 us (20.2%) | 这段时间只有部分核在工作 |
| 拖尾 (tail) | 0.753 us (17.6%) | 最先完成到最慢完成的间隔 |
| block 耗时范围 | 1.181–1.839 us | 离散度 45.0% |

**关键**：启动偏移占 kernel 时长 20%，这部分损失与流水线优化无关。56 个 block 只采到 12 个 subcore 的时间轴（PipeTimeline 只插桩部分核），所以这是下界，报告里明确写了。

### 内存通路与跨 DIE（第 9 节）

vector_add 上 8 条通路里 12/13/14/15 与 UB 四向带宽逐字节相同，0/1 是 GM 读写。跨 die 通路全为 3.3 GB/s 以下的小值。

cube kernel（matmul）上则是另一种形态：跨 die 通路占 60.6%，其中 path 8 单项 36.2 GB/s。同一套工具，同一个字段，能看出两类算子的搬运结构差异。

## 采集覆盖度（实测）

| 类别 | 本次拿到 | 说明 |
|---|---|---|
| 流水占用 | ✅ 4 条 | MTE2/scalar/MTE3/vector |
| 带宽 | ✅ 8 条通路 + 峰值占比 | 反解峰值 163 GB/s |
| Cache | ✅ L2 读/写命中、iCache、DCache | 写命中有 CV 14% 的抖动 |
| 冲突 | ✅ bank 冲突 + 各流水 wait | |
| block 时间轴 | ✅ 12/56 核 | 启动偏移、拖尾、逐块耗时 |
| 逐指令 | ⚠️ 需 `--with-instructions` | 另起一趟，1–4 分钟 |
| 跨 die | ✅ 通路级带宽 | path 归属是推断 |

## 拿不到什么（一并说明）

| 项 | 原因 |
|---|---|
| sector / coalescing 效率 | CANN 无对应计数器 |
| Outstanding / snoop | 全部 metric 组下均无输出通路 |
| Load/Store 指令占比 | 需 `--dump=true`，而它要求 `TimelineDetail`，在 dav-3510 上直接失败 |
| VF 内部（PUSHQ/RVECEX/…） | 仅 cannsim 仿真 trace 有；本 skill 限定上板实采 |
| 全 56 核的时间轴 | PipeTimeline 只插桩部分核 |

这些都写在报告与 `metric_catalogue.py` 里，标 `不可用` 而不是估一个值。
