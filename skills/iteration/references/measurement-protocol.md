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

实测 3.5 s（host + kernel 全量，`bench/k.asc` 含 `__simd_vf__` 向量体）。失败信息在 stderr，`| grep -E "error"` 过滤后为空才是通过；`warning: 'cce_global' attribute ignored` 是常态，不影响产物。

## 计时

```shell
# 单进程：5 次 warmup + 10 次计量，每次计量前刷 201 MB L2
WARMUP=5 ITERS=10 FLUSH=1 ./bench
# [timing] 11.9 11.4 ...    每轮一次，单位 µs
# [result] median=11.82 us  min=10.84  max=14.72
# [accuracy] bad=0 / 1048576 MERE=1.768e-04 -> PASS

# 重复矩阵（判据的来源）：5 个独立进程 × 10 次，剔除每进程首样本后合并
scripts/measure.sh ./bench 5 10
# procs=5 per-proc-median=[11.32, 11.46, 12.04, 12.73, 12.16] median=11.820 ...
# accuracy] bad=0 / 1048576  worst_rel=4.865e-04  MERE=1.768e-04
```

判据只看 `median` 与 `accuracy` 两行，不看单次 `[timing]` 极值。

`measure.sh` 自己 source CANN 环境（bench 二进制链接运行时库，非登录 shell 直接跑会 `cannot open shared object file: libplatform.so`），`build.sh` 则在产物缺失时返回非零——`bisheng` 自身遇到 `ASCEND_HOME_PATH` 未设也是退出 0。

## kernel-perf

```shell
WARMUP=5 ITERS=10 FLUSH=1 /workspace/cannbench/bin/cnb-npu-lock \
  /workspace/cannbench/bin/cnb-env /workspace/cannbench/evalenv/bin/python \
  /root/kernel-perf/scripts/kernel_perf.py profile \
  --app ./bench --out prof/r1 --warmup 3 --launch-count 1
```

- `--app` 直接指向上面编出的二进制；不需要 python 探针，这正是直调链路的价值。
- 一次采集约 40–60 s（8 组 metric、多 pass），比 bench 的一次计时贵两个数量级，所以 kernel-perf 只用来取 pipe 画像与 Duration 复核，不用来跑重复矩阵。
- 采集要拿 NPU 锁（`cnb-npu-lock`），且一次只跑一个 NPU 任务。

## 噪声实测全表

同一二进制、同一天、空载机器，全部在 NPU 锁内串行：

| 观测对象 | 样本 | 实测 |
|---|---|---|
| 逐进程中位数（刷 L2） | 5 进程 × 10 次 | 11.32 / 11.46 / 12.04 / 12.73 / 12.16 µs，进程间极差 11.7% |
| 合并中位数与单样本范围 | n=45 | 11.82 µs，min 10.84 / max 14.72 |
| 逐进程中位数（同一二进制，另一组） | 5 进程 | 极差 25.5% |
| 首样本 vs 稳态（刷 L2） | 50 样本 | 首样本均值 7.21 µs，其余中位数 6.37 µs（+13%） |
| kernel-perf Duration | 5 次采集 | 7.65 / 7.65 / 7.73 / 7.73 / 7.80 µs，极差 2.0% |
| kernel-perf scalar 占比 | 5 次采集 | 9.55–10.18%（±0.3 个点） |
| kernel-perf MTE2 占比 | 5 次采集 | 52.50–52.93%（±0.2 个点） |
| kernel-perf 逐核 spread | 5 次采集 | 13.8%–19.4%（±2.8 个点，抖） |

读法：

- **bench 的合并中位数**要跨过约 10% 才算效应；跨不过就是没有。
- **kernel-perf 的 Duration 与 pipe 占比**稳得多（2% / 1 个点），复核 Duration 时优先信它。
- **逐核 spread** 在同一二进制上抖 5.6 个点，只能看趋势，不能当单轮判据。

## L2 刷的实测影响

同一二进制，`FLUSH` 开关各 5 进程 × 10 次：

| 配置 | 合并中位数 | min | max | n |
|---|---:|---:|---:|---:|
| `FLUSH=1`（每轮刷 201 MB） | 11.82 µs | 10.84 | 14.72 | 45 |
| `FLUSH=0` | 7.41 µs | 6.36 | 8.79 | 45 |

差 37%。被测 case 越小、数据段越整块装进 128 MiB L2，差距越大。这也是"A/B 两次测出 20% 收益、其实只是换了探针"的物理来源。

## 口径对照

| 口径 | 来源 | 用途 | 实测（同源代码，case1 1M fp16 exp） |
|---|---|---|---:|
| host-event | bench 的 `aclrtEvent` 计时 | 迭代期快信号 | 11.82 µs（5 进程 × 10 次的合并中位数，骨架默认 tiling） |
| kernel-perf | msopprof `Duration` | pipe 画像 + Duration 复核 | 7.73 µs（同骨架同参数，5 次采集中位数） |
| 评分 | CannBench per-case profiler | 定稿一次 | 3.05–3.20 µs（打榜工程与 harness 自身口径） |

host-event 比 kernel-perf 高，是因为它把 kernel 之外的派发与同步也圈进了计时区间；kernel-perf 只算设备侧 kernel 本体。这 50% 的差不是优化空间，换口径就是换标尺。

三者绝对值不同，差值来自下发路径与探针开销，不是优化空间。**同一份比较里只能出现一种口径。**
