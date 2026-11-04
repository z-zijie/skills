# 指标判读

所有阈值来自 Ascend950PR（dav-3510）实测样本，并在 `references/measurements.md` 里保留了对应的原始数字。判读分三层，顺序不能颠倒。

## 第一层：先确认数据有效

采集本身可能失败而 `msopprof` 仍然返回 0。以下任一情况，先修采集，不要读指标：

| 现象 | 含义 | 处理 |
|---|---|---|
| 所有 `*_ratio` 都是 `NA`/空 | replay 没跑到 kernel | 减少 `--metrics` 组数；或 `--replay-mode=application` |
| `Block Dim` 远小于芯片核数（如 1） | tiling 只切了 1 块，或 kernel 没按核展开 | 查 tiling 代码，不是性能问题 |
| `Current Freq` 明显低于 `Rated Freq` | 降频，可能是温度/功耗墙 | 隔一会儿重采；对比时确认频率一致 |
| 同一 kernel 两次采集耗时差 >15% | 设备上有别的任务 | `npu-smi info` 确认独占 |

工具在 `roofline.class` 为 `no data (...)` 时会明确标注。

## 第二层：定位主导流水

`pipes[].mean_ratio_pct` 是跨核平均的流水占用。取最高的一条：

| 主导流水 | 含义 | 先查什么 |
|---|---|---|
| `vec` >60% | Vector 计算单元饱和 | 峰值占比 >70% 即接近极限；否则查 bank 冲突与 cast |
| `cube` >50% | Cube 饱和 | 看 `arithmetic.cube_fp_ratio`，算子类型是否用对了精度 |
| `mte2`/`aiv_mte2` >50% | GM→片上搬运受限 | 增大搬运粒度（≥16KB）、512B 对齐、L2 CacheMode |
| `mte3`/`aiv_mte3` >50% | 结果写回受限 | 合并写、减少中间结果落 GM |
| `scalar` >30% | 标量指令成为瓶颈 | 缩小 TilingData、减少循环内标量运算、核数别开太满 |
| 全部 <50% | 流水线气泡 | 查 `conflicts.*_wait`、double buffer 是否生效 |

`scalar` 高是一个容易漏的点：它意味着 address 计算/循环控制吃掉了主流水的时间，加大 tile 比不上减少标量指令。

## 第三层：看瓶颈证据

### 带宽峰值占比

`bandwidth[].peak_pct` 的分母是工具从采集数据反解出的真实峰值（不是 datasheet 值）。判读：

- `>80%`：该通路已经到顶，继续压这条通路没收益，改算法或换通路
- `50–80%`：还有余量，可继续优化搬运
- `<50%` 且该通路是主导：说明不是带宽受限，而是延迟/气泡（`msopprof` 也会给出 "bandwidth utilization lower than 80% when active"）

### 核间离散度

`imbalance.aiv.spread_pct = (max-min)/mean`：

- `<10%`：切分均匀
- `10–30%`：有尾块效应，通常是大 shape 除不尽
- `>30%`：切分策略有问题，此时优化流水收益会被不均衡吃掉

注意 `spread_pct` 大但总耗时很短时（<10us），主要是启动开销占比高，不是切分问题——看 `time_us` 绝对值再判断。

### bank 冲突

`conflicts.vec_stu_cflt` / `vec_ldu_cflt` / `vec_sfu_cflt`：

- `<5%`：可忽略
- `5–15%`：调 UB 地址对齐或加 padding
- `>15%`：UB 布局有系统性问题

### Cache

- L2 读命中率 `<50%`：数据复用没做出来，或 tile 太大把 L2 冲掉了
- L2 写命中率天然偏低（写回策略），不必单独优化
- iCache miss `>15%`：代码量太大（大量展开/内联），考虑缩小 kernel 或拆分

### 等待占比

`conflicts.vec_wait`、`*_mte*_wait` 与 `pipe_wait_cycles_total` 说明流水在等谁：

- Vector 等待高 + MTE2 占用低 → 搬运没提前发起，double buffer 没生效
- MTE3 等待高 → 输出队列满，写回跟不上
- 两条流水互相等待都高 → 队列深度不足，加大 buffer num

## 按算子类型的预期分布

| 类型 | 主导流水 | 健康区间 | 异常信号 |
|---|---|---|---|
| Elementwise（Add/Mul/Relu） | vec | 50–80% | MTE2 占比 > vec |
| Reduction | vec | 40–70% | scalar >20% |
| Activation（Softmax/Gelu） | vec | 60–85% | 大量 cast 指令 |
| MatMul | cube | 40–70% | vec 占比 > cube |
| 纯搬运（Transpose/Concat） | mte2+mte3 | 合计 >50% | vec >30% |

## 实测参照（Ascend950PR, CANN 9.3.0, 1650 MHz）

**vector_add**（56 核，9.965 us，409600 float32）：MTE2 51.8%、scalar 23.9%、MTE3 17.7%、vec 8.3%。GM→UB 峰值 163 GiB/s，实测 28.4 GiB/s（17.4%）。核间离散 71.9%（短 kernel 的启动开销主导）。厂商 advice：MTE2 活跃时带宽利用率低于 80%、vector 计算占用低于 20%。

**matmul mxfp4 base**（28 核，3.051 us）：AIC scalar 92.5%、fixpipe 2.4%。cube 仅 0.08%——kernel 在小 shape 下几乎全花在标量准备上，这是 cube 类算子在小 shape 的典型形态，也说明"cube 算子"不等于"cube 忙"。
