# 测量协议：命令与实测噪声

本文件的数字都是 Ascend950PR（dav-3510，CANN 9.3.0）上实测的，用作判据的参照。换机器、换 CANN 版本要重新测一遍再引用。

## 环境

```shell
# CANN 不进登录 shell，必须包一层
cann-run <命令>                       # = source /home/developer/Ascend/cann-9.3.0/set_env.sh; exec "$@"

# bisheng 还要求 ASCEND_HOME_PATH，并把编译器 bin 放进 PATH
source /home/developer/Ascend/cann-9.3.0/set_env.sh
export PATH=/home/developer/Ascend/cann-9.3.0/tools/bisheng_compiler/bin:$PATH
```

不 source 时的报错是 `bisheng: error: cannot find ASCEND_HOME_PATH`，而 `bisheng` 的退出码仍是 0——只看退出码会把失败当成功。判定编译成功一律看产物是否存在。

## 编译

```shell
time bisheng -fPIC --npu-arch=dav-3510 -O3 --asc-aicore-lang [其它 -D] \
  bench.asc -o bench -lm -ldl -lplatform -ltiling_api -lmsprofiler
```

实测 3.5 s（host + kernel 全量，[`bench-skeleton.asc`](bench-skeleton.asc) 原样编译）。失败信息在 stderr，`| grep -E "error"` 过滤后为空才是通过；`warning: 'cce_global' attribute ignored` 是常态，不影响产物。

## 计时

```shell
# 单进程：5 次 warmup + 10 次计量，每次计量前刷 201 MB L2
WARMUP=5 ITERS=10 FLUSH=1 ./bench
# [timing] 11.9 11.8 12.4 11.2 ...   每轮一次，单位 µs
# [result] median=8.79 us  min=7.15  max=13.33
# [accuracy] bad=0 / 1048576  worst_rel=4.865e-04  MERE=1.768e-04
# RESULT: PASS

# 重复矩阵（判据的来源）：5 个独立进程 × 10 次，剔除每进程首样本后合并
scripts/measure.sh ./bench 5 10
# procs=5 per-proc-median=[9.43, 8.97, 8.3, 8.16, 9.01] median=8.790 ...
# accuracy] bad=0 / 1048576  worst_rel=4.865e-04  MERE=1.768e-04
```

判据只看 `median` 与 `accuracy` 两行，不看单次 `[timing]` 极值。

`measure.sh` 自己 source CANN 环境（bench 二进制链接运行时库，非登录 shell 直接跑会 `cannot open shared object file: libplatform.so`），`build.sh` 则在产物缺失时返回非零——`bisheng` 自身遇到 `ASCEND_HOME_PATH` 未设也是退出 0。

## kernel-perf

直调二进制直接喂给 `--app`，不需要 python 探针——这正是直调链路的价值。

```shell
WARMUP=5 ITERS=10 /workspace/cannbench/bin/cnb-npu-lock \
  /workspace/cannbench/bin/cnb-env /workspace/cannbench/evalenv/bin/python \
  /root/kernel-perf/scripts/kernel_perf.py profile \
  --app ./bench --out prof/r1 --warmup 3 --launch-count 1
```

- 一次采集约 40–60 s（8 组 metric、多 pass），比 bench 的一次计时贵两个数量级。用它取 pipe 画像与 Duration 复核，不用它跑重复矩阵。
- 采集要拿 NPU 锁（`cnb-npu-lock`），一次只跑一个 NPU 任务。

### 块级时间戳：先确认它真的采到了

报告第 7 节（launch skew / tail / 逐块起止）依赖采集目录里的 `trace.json`。这份文件**不是每次都有**：

| 请求方式 | 结果 |
|---|---|
| 默认 metric 组 | 无 `trace.json`，第 7 节 `Unavailable` |
| `--with-timeline` | 只追加 `BasicInfo`，**仍无 `trace.json`**，第 7 节照旧 `Unavailable` |
| `--metrics=PipeUtilization,ResourceConflictRatio,PipeTimeline` | 实测产出 `trace.json`，第 7 节有内容 |
| `--metrics=PipeUtilization,PipeTimeline` | 实测**没有** `trace.json` |

用法上的结论：要块级数据显式写带上 `ResourceConflictRatio` 的那一组，并**在采完后先 `find <out> -name trace.json` 确认**。看不到文件就是没有数据，不要因为"我请求了 PipeTimeline"就假定第 7 节有值。

### 块级 skew 只能当线索

同一份二进制（本流程骨架），两次采集的第 7 节：

| 采集 | 覆盖 | Launch skew | Tail | 逐块时长范围 |
|---|---:|---:|---:|---|
| 一次 | 12 子核 | **0.000 µs** | 0.690 µs (12.3%) | 1.884–2.575 µs |
| 另一次 | 12 子核 | **0.619 µs (13.9%)** | 2.635 µs (59.0%) | 2.080–4.959 µs |

skew 从 0 跳到 0.619，而代码没变。直接原因是 `trace.json` 里所有被观测子核的 `min(ts)` 完全相同（0.0121），"最晚开始减最早开始"于是恒等于 0——**这是观测方式造成的零，不是"派发没有斜坡"**。同一批采集的第 8 节（全 56 核）给出 duration 3.305–4.125 µs、spread 22.68%，那里才是真实形状。

所以：

- 逐块起止**只覆盖 12/56 个子核**，且可能整体退化成一个原点；skew 当一个下界/线索读，不当结论。
- 想看核间不均衡，用第 8 节的 **per-core spread（全 56 核）**，配合 bench 的重复矩阵确认方向。
- 第 7 节真正稳的是 **tail** 与逐块时长范围，两者在两次采集里都给出了非零且可解释的值。

## 噪声实测全表

同一二进制、同一天、空载机器，全部在 NPU 锁内串行：

| 观测对象 | 样本 | 实测 |
|---|---|---|
| 逐进程中位数（刷 L2） | 5 进程 × 10 次 | 9.43 / 8.97 / 8.30 / 8.16 / 9.01 µs，进程间极差 14.3% |
| 合并中位数与单样本范围 | n=45 | 8.79 µs，min 7.15 / max 13.33 |
| 逐进程中位数（同源代码另一版构建） | 5 进程 | 5.17 / 6.45 / 5.81 / 4.87 / 6.19 µs，极差 25.5% |
| 首样本 vs 稳态（刷 L2，骨架默认 tiling） | 5 进程 × 10 次 | 首样本均值 12.94 µs，其余中位数 12.29 µs（+5.3%） |
| **批间漂移（同一二进制，三批）** | 每批 5 进程 × 10 次 | **9.65 / 8.86 / 9.21 µs（差 8.9%）** |
| 首样本 vs 稳态（刷 L2，另一版 tiling） | 50 样本 | 首样本均值 7.21 µs，其余中位数 6.37 µs（+13.4%） |
| kernel-perf Duration | 5 次采集 | 7.65 / 7.65 / 7.73 / 7.73 / 7.80 µs，极差 2.0% |
| kernel-perf scalar 占比 | 5 次采集 | 9.55–10.18%（±0.3 个点） |
| kernel-perf MTE2 占比 | 5 次采集 | 52.50–52.93%（±0.2 个点） |
| kernel-perf 逐核 spread | 5 次采集 | 13.8%–19.4%（±2.8 个点，抖） |

读法：

- **批间漂移比批内噪声大**：同一二进制三批测出 9.65 / 8.86 / 9.21 µs，差 8.9%，而源码一个字节没改。跨批比较是在把改动和漂移加在一起，必须改成同批交替的配对比较。
- **bench 的合并中位数**在同一批内要跨过约 10% 才算效应；跨不过就是没有。
- **kernel-perf 的 Duration 与 pipe 占比**稳得多（2% / 1 个点），复核 Duration 时优先信它。
- **逐核 spread** 在同一二进制上抖 5.6 个点，只能看趋势，不能当单轮判据。

## L2 刷的实测影响

同一二进制，`FLUSH` 开关各 5 进程 × 10 次：

| 配置 | 合并中位数 | min | max | n |
|---|---:|---:|---:|---:|
| `FLUSH=1`（每轮刷 201 MB） | 8.79 µs | 7.15 | 13.33 | 45 |
| `FLUSH=0` | 5.80 µs | 5.19 | 7.63 | 45 |

差 52%。被测 case 越小、数据段越整块装进 128 MiB L2，差距越大。上一个周期里"A/B 两次测出 20% 收益、其实只是换了探针"的物理来源就是这一条。

## 批间漂移实测（为什么必须成对比较）

同一二进制 `/tmp/v_default`（源码未改动），在两次「填充」之间测三批，填充用的是别的构建，模拟真实会话里跑过别的实验：

| 批 | 逐进程中位数 | 合并中位数 |
|---|---|---:|
| 批 1 | 9.52 / 9.53 / 9.66 / 9.84 / 9.51 | **9.65 µs** |
| （填充：另一构建，3 进程） | 13.61 / 14.16 / 14.09 | 13.99 µs |
| 批 2 | 10.02 / 7.95 / 9.02 / 9.00 / 8.54 | **8.86 µs** |
| （填充：另一构建，3 进程） | 8.66 / 8.62 / 8.50 | 8.48 µs |
| 批 3 | 9.90 / 9.96 / 7.95 / 9.20 / 9.23 | **9.21 µs** |

同一份代码，最大与最小相差 **8.9%**，比多数人要找的效应大。

周边证据（来自上一个优化周期，独立观测到同类现象）：
- 官方评分口径的跨 session 漂移在小 case 上达 **±0.16 µs**（3 µs 的 5%）。
- 一个 4 case 子集上同一 kernel 跑三次得 0.6684 / 0.6663 / 0.6636，而同一会话里跨批从 0.6384 漂到 0.6765（**6%**）。

三组的共同结论：**漂移是系统性的、随会话推进的，不是随机噪声，不会被多次平均消掉。** 只有同批交错能把两边共同的漂移减掉。

## 地板实测

`references/floor-probe.asc`，56 block，本机空载：

| 口径 | `FLOOR_MODE=0`（立即 return） | `FLOOR_MODE=1`（Init + 一个 64 元素 tile） |
|---|---:|---:|
| kernel-perf Duration | 2.07–2.58 µs | 8.81 µs |
| host-event（bench 骨架同款计时） | 4.08–4.14 µs | 8.81 µs |

block 数斜率（kernel-perf 口径，`FLOOR_MODE=0`）：

| blocks | Duration |
|---:|---:|
| 1 | 1.34 µs |
| 56 | 2.07 µs |

即约 **13 ns/block**，56 核的派发斜坡 ≈0.73 µs。两种口径都要记录：地板本身也带口径差（4.08 vs 2.07），拿一个去折算另一个会得到错的上界。

## 口径对照

| 口径 | 来源 | 用途 | 实测（同源代码，case1 1M fp16 exp） |
|---|---|---|---:|
| host-event | bench 的 `aclrtEvent` 计时 | 迭代期快信号 | 8.79 µs（5 进程 × 10 次的合并中位数，骨架默认 `TILES=4`） |
| kernel-perf | msopprof `Duration` | pipe 画像 + Duration 复核 | 7.73 µs（同骨架同参数，5 次采集中位数） |
| 评分 | CannBench per-case profiler | 定稿一次 | 3.05–3.20 µs（上一个周期在打榜工程上量的，非本骨架） |

host-event 比 kernel-perf 高，是因为它把 kernel 之外的派发与同步也圈进了计时区间，而 kernel-perf 只算设备侧 kernel 本体。评分口径更低，是因为打榜 harness 的计时窗口又换了一层。

同一份比较里只能出现一种口径：换口径就是换标尺，差值不是优化空间。

## 一轮完整消融的实测样例

骨架 + `scripts/measure.sh`，每个配置 5 进程 × 10 次、刷 L2、剔除首样本。两轮都跑在同一台空载机器上。

**第一轮，骨架还带一个耦合旋钮**（`-DPDEPTH` 同时决定 ping-pong 深度和 tile 大小）：

| 配置 | 实际 tile | 合并中位数 | 进程间极差 | 精度 |
|---|---:|---:|---:|---|
| 默认（`PDEPTH=8`） | 2368 | 11.34 µs | 8.2% | PASS |
| `-DPDEPTH=4` | 4736 | 8.76 µs | 23.2% | PASS |
| `-DTILES=1` | 8192 | 8.39 µs | 14.8% | PASS |
| `-DTILES=13` | 1472 | 14.58 µs | 12.1% | PASS |

看起来是"tile 越大越快"的两条独立收益（深度一条、趟数一条）。

**第二轮，删掉耦合旋钮后重跑**（只剩 `-DTILES`）：

| 配置 | 实际 tile | 合并中位数 | 进程间极差 | 精度 |
|---|---:|---:|---:|---|
| 默认（`TILES=4`） | 4688 | 9.00 µs | 6.1% | PASS |
| `-DTILES=1` | 8192 | 9.09 µs | 17.8% | PASS |
| `-DTILES=13` | 1472 | 14.47 µs | 15.1% | PASS |

真相是：**tile 从 2368 涨到 4688 是真收益，4688 再涨到 8192 是噪声内的持平**（9.00 对 9.09，进程间极差 6–18%）。第一轮那个"8.39 更好"是耦合旋钮的读数，不是第二条轴。

同一个默认二进制后来又量了一次（就是下面 L2 对照表里的 `FLUSH=1` 行）：8.79 µs，对 9.00 µs 差 2.3%。协议内部的复现性就在这个量级——判据要的门槛是 10% 以上，正是为了让这类抖动不进入裁决。

三条通用教训：

- 一个旋钮顺带改了另一个量时，两次实验测的是一件事，却记成两条收益。跑之前核对 `[cfg]` 行，除要测的字段外必须逐字相同。
- 单调趋势要警惕：真效应通常有拐点（这里在 tile ≈ 4–5K 附近），一路单调往往意味着另一条轴在背后推动。
- 极差大的那一次不一定是坏数据——`TILES=1` 的 17.8% 里有真实的 bimodal（进程间 7.88 到 9.56），它恰好说明这个配置对调度更敏感。
