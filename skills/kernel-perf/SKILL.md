---
name: kernel-perf
description: 在 Ascend NPU（含 Ascend950PR / dav-3510）上对指定 kernel 做上板实采的性能分析，目标是对齐 NVIDIA NCU 的信息密度：逐核逐流水的占用与等待、全带宽通路、L2/iCache、bank 冲突、MFU/MAC usage 等关键指标、以及逐指令耗时与 PC→opcode 归属。每个指标按「怎么来的」分 A–E 五级置信度，并用重复采集的变异系数校准。当用户要"分析某个 kernel 的性能""看某个算子慢在哪""拿 kernel 的详细性能指标""对比两次 kernel 性能""算一下 MFU"时使用。
---

# Ascend Kernel 上板性能采集

给一个可执行文件和 kernel 名，把这颗芯片上能采到的性能数据全部拉下来，换算成可判读的形式，并标明每个数字有多可信。

只走上板实采（`msopprof`），不跑 npusim 仿真。

## 快速开始

```shell
# 标准采集 + 分析 + 校验
python3 scripts/kernel_perf.py profile --app ./vector_add --kernel add_kernel --out ./out

# 加上逐指令耗时（慢，另起一趟，1–4 分钟）
python3 scripts/kernel_perf.py profile --app ./vector_add --kernel add_kernel --out ./out \
    --with-instructions --instr-pipes="vector|mte2|mte3"

# 校验这次采集和报告（18 条检查，退出码可当门禁）
python3 scripts/critic.py --out ./out --report ./out/report.md

# 稳定性：同一 kernel 重复采集，量化每个指标的抖动
python3 scripts/stability.py --app ./vector_add --kernel add_kernel --runs 5

# 列出全部指标及其产出口径与置信度
python3 scripts/metric_catalogue.py

# 优化前后对比
python3 scripts/kernel_perf.py diff ./before ./after

# 不需要 NPU 的自测（54 项，含反例）
python3 scripts/selftest.py
```

## 采什么

一次采集（必要时分多趟）拿到的东西：

| 层 | 内容 | 主要来源 |
|---|---|---|
| 身份 | kernel 名、耗时、Block Dim、频率、SoC | `OpBasicInfo.csv` |
| 流水 | 每核每流水的忙时间/占比/活跃带宽 | `PipeUtilization.csv` |
| 等待 | 各流水 wait 占比 + 逐核累计 wait cycles | `ResourceConflictRatio.csv`、`visualize_data.bin` |
| 冲突 | store/load bank 冲突、SFU 冲突 | `ResourceConflictRatio.csv` |
| 带宽 | GM↔UB、UB↔GM、主存读写、L1、L0A/B/C | `Memory*.csv` |
| 通路 | 8 条 memory_path 的带宽与请求数（CSV 不含） | `visualize_data.bin` 0x08 |
| Cache | L2 close/far × hit/miss/victim、iCache、逐核命中 | `L2Cache.csv`、`visualize_data.bin` |
| 计算 | cube 指令数、浮点/整型指令数 | `ArithmeticUtilization.csv` |
| 关键指标 | MFU、MAC usage、算术强度、受限因素 | 由上面推导 |
| 指令 | 逐 opcode 耗时、逐 PC 耗时、流水归属 | `trace.json` + `dfx_region_map.txt` |

## 三个支柱

### 1. 稳定且准确

同一 kernel 连采 5 次，量化每个指标的变异系数（`stability.py`，结果写进报告）。

实测（vector_add，warm-up=5，launch-count=3）：

| 指标 | CV | 判读 |
|---|---:|---|
| 耗时 | 1.84% | 稳定 |
| MTE2 占用 | 1.38% | 稳定 |
| Vector 占用 | 0.82% | 稳定 |
| GM→UB 带宽 | 0.82% | 稳定 |
| L2 **读**命中 | 0.57% | 稳定 |
| L2 **写**命中 | **14.36%** | **不可单独引用** |

结论：流水与带宽类指标重复性好到可以直接比较优化前后；Cache 写侧不是。

### 2. 全面

指标目录（`metric_catalogue.py`）枚举 49 项，每项写明来源文件与字段。覆盖度对照：

- CANN 自身 `AI_CORE_CALCULATION_FORMULA` 定义了 74 个可计算指标
- `msopprof` 的 CSV 只暴露其中 7 个的命名列
- 本工具额外从 `visualize_data.bin` 解出 34 项 CSV 里没有的行（UB 四向带宽、8 条通路、逐核 L2、wait cycles…）

**已知拿不到的**（如实标注，不猜）：
- 部分 PMU 计数器（`vec_bankgroup_cflt_ratio`、`*_iq_full_ratio`、`control_flow_*`）在 `msopprof` 任何 metric 组下都没有输出通路，属于工具未暴露而非采集失败
- 源码行级归属需要内核带 `-g` 重编，且显示的是内联展开后的头文件行，不是用户 kernel 行

### 3. 信息分级

每个指标带 A–E 置信度，按「怎么来的」划分，用实测 CV 校准（CV>10% 自动降为 E）：

| 级 | 含义 | 例 |
|---|---|---|
| A | 直接计数器，重复一致到末位 | 耗时、cycles、cube 指令数、wait cycles |
| B | 由 A 精确推导，无采样无模型假设 | 各流水占比、带宽、bank 冲突、核间离散度 |
| C | 采样统计，短 kernel 有混叠风险 | L2 读命中、逐指令耗时、时间线 |
| D | 依赖理论常量，不是实测 | MFU、MAC usage、算术强度 |
| E | 已知不稳定或无意义 | **L2 写命中率**（CV 14%） |

报告第 9 节列出本报告里所有 E 级指标及其原因，避免读者误用。

## 关键指标（第 2 节）

| 指标 | 口径 | 本机参考值 |
|---|---|---|
| **MFU** | 达成 TFLOPS ÷ 理论峰值 | 峰值 378.47 TFLOPS（28 AIC × 1650 MHz × 16³ MAC × 2） |
| **MAC usage** | cube 流水占用，即 MAC 发射机会比例 | >70% 表示 cube 被乘加填满 |
| 算术强度 | FLOPs ÷ 主存字节 | 结合峰值带宽判断 compute/memory bound |

MFU 一律标 D 级：分母是理论峰值，用于横向比较，不是"实测算力"。

**别按算子类型猜瓶颈**：实测一个 `cube` 类型的 matmul，cube 流水只占 0.08%、AIC scalar 占 92.5%。归类一律按实测占比。

## 逐指令耗时

需要 `--with-instructions`。产出：

- 按 opcode 聚合的耗时与占比
- 按流水聚合
- 最热指令点（PC 偏移 + opcode + 流水）

原理：`trace.json` 的 `args.pc_addr` 是绝对地址，减去 `pc_start_addr.txt` 得到 kernel 内偏移，再与 `dfx_region_map.txt` 的 `pc=` 对齐，就得到「这条指令属于哪个 opcode、哪条流水」。

报告会明确写出**覆盖率**——已归属耗时占 kernel 总时间的百分比。低于 50% 时给出警告：那描述的是被插桩区间，不是整个 kernel。

## 约束（实测）

- **采集分趟**：`InstrTimeline` 与 `PipeTimeline` 互斥；`InstrTimeline` 和 8 个核心组混采会挂起。工具自动拆趟（`plan_passes`）。
- **一次不超过 8 组**：11 组会让所有计数变 `NA` 却仍返回 0，靠 `collection_nonempty` 检查识别。
- **`--instr-timeline-pipe`** 只接受 `cube|fixp|vector|mte1|mte2|mte3`，给 `scalar` 直接被拒。
- **`dfx_region_map.txt` 会被 msopprof 后续步骤删除**：工具在采集后立即复制到 `probe/`。
- **`TimelineDetail` 在 dav-3510 上直接失败**，不要用。
- **应用级 `msprof`（非 `op`）在本机不可用**：自带 Python 的 `_ctypes` 不匹配，采集到数据但无法解析。kernel 级不受影响。
- **`biprof` 不是上板工具**：它是 `cannsim` 仿真器的包，需要 `plotly`（未安装）。`msprof op` 本身就是 `msopprof` 的包装。

## 判读

阈值见 `references/interpretation.md`；实测数据见 `references/measurements.md`；平台常量见 `references/platform-constants.md`；容器格式与能力边界见 `references/dav3510-notes.md`。

三条核心：

- 主导流水 >60% 且峰值占比 >70% → 接近硬件极限，优化看算法
- 没有任何流水 >50% → 流水线气泡，查 wait 占比与 double buffer
- 核间耗时差 >30% → tiling 切分问题，与流水优化无关
