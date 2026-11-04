---
name: cannbench-serial-eval
description: 在 Ascend950PR 单卡机器上跑 CannBench 算子评测时，用逐用例、单进程的方式代替全量 run_evaluation.sh。当需要评测/打分 CannBench 算子、或要解释为什么不能用官方全量评测脚本时使用。
---

# CannBench 逐用例评测

单卡 Ascend950PR 上跑 CannBench 评测，用 harness 自带的单进程入口一个用例一个进程地跑，不打全量 `scripts/run_evaluation.sh`。本文给出落地方式；脚本在本 skill 的 `scripts/` 下，部署目标见第 1 节。

## 为什么不用全量 harness

- **正确性阶段会 fan-out。** `run_evaluation.sh` → `kernel_eval.staged_eval` 分三段跑：编译、正确性、性能。正确性阶段不开 profiler，`ProcessPoolCoordinator` 按 `processes-per-card`（默认 2）开并发子进程；性能阶段的批量采集也一次吃多个用例。
- **逐用例才拿得到可比的分。** harness 自带单进程单用例入口 `kernel_eval.cli eval-child`，它跑的就是官方 accuracy checker、官方 profiler 计时和官方 Eq.3 单例分。一个用例一个进程地驱动它，分数和榜单直接可比，不需要另写一套"差不多的"精度/计时逻辑。
- **主机内存比卡更容易被打爆。** 容器只给 32 GB（`/sys/fs/cgroup/memory/memory.limit_in_bytes`），而 `os.cpu_count()` 看到的是 128。并发起多个构建（每个默认开 128 并行）会把 cgroup 打穿：这台机器上实测过 83 次 OOM kill（`memory.oom_control` 里 `oom_kill 83`），表现为进程 `rc=137`。真正被打死的是宿主内存，不是卡。实测那次 4 个 pi 轮次被同时打死时，机器上**没有任何评测在跑**，所以它从来不是"卡被并发搞死"的证据。

## 1. 逐用例入口

部署 `scripts/percase.py`（驱动官方 `eval-child`，一个 case 一个进程）到 `/workspace/cannbench/loop/scripts/percase.py`。

它跑的入口是：

```sh
python -m kernel_eval.cli eval-child \
  --bench-name cann --device-id 0 \
  --cases-file <一个只含该 case 的 JSON> \
  --output <结果 JSON> --reports-dir <目录> --task-dir <tasks> \
  --warmup 3 --repeat 5 --no-perf-batch-cases --profiler-level Level1
```

`--no-perf-batch-cases` 必须给：批量采集把多个用例合并进一个 profiler 会话，那正是要避免的东西。

### 不要自己做 NPU 锁

**NPU runtime 本身按 Task 下发顺序排队**：同一张卡上多个进程各自下发 kernel，runtime 把它们排进同一个执行队列，一个跑完再跑下一个。用户态再加一把 `flock` 不改变卡的执行顺序，只是把等待提前到了进程入口。

实测（两个进程同时各跑一次 profiled matmul，同一个 capture 口径）：两个会话都正常完成、都写出完整 `kernel_details.csv`，没有报错、没有 hang。单跑时那个 matmul 是 2953 µs，并发时两边分别是 5842 / 5832 µs——正好翻倍。也就是说 runtime 把两个 kernel 串起来了，谁也没抢到并行，只是各自多等了一倍的时间。

所以：

- **不需要 `cnb-npu-lock` 这类用户态锁来保护机器。** 加锁的代价是排在后面的评测可能等上几十分钟，还要处理等待超时（旧实现 `CNB_NPU_LOCK_WAIT` 默认 7200 s）、可重入、锁 fd 不被内层关掉这些本来不存在的问题。
- **但性能数字仍然要一个一个测。** 并发时各自测到的 kernel 时间是被别人排队放大过的，不是真实性能。要的是"别同时测两个性能数字"，不是"拿锁把进程挡在门外"。
- **并发跑非性能用途是安全的。** 只想看能不能编译过、精度过不过时，多个进程一起跑不会弄坏机器，只是每个都会慢一些。

**不要**把锁塞进 `bin/cnb-env`。它被 build.sh、wheel 构建、各种嵌套调用复用，任何锁放进去都会在自己的子树里再次申请而死等（实测踩过）。现在连评测入口也不需要锁了。

部署时还要把旧用法一起清掉，否则锁会以另一种方式活下来：

- 删掉 `/workspace/cannbench/bin/cnb-npu-lock`，并从 `evaluate.sh` / `percase_eval.sh` 开头去掉 `exec cnb-npu-lock` 前导（本 skill 的脚本已经去掉）。
- 给 agent 的 prompt / 包装脚本里，凡是写了 `cnb-npu-lock <命令>` 的地方都改成直接跑命令。留着会变成"排队等一把已经没人在管理的锁"，比不加锁更难查。
- `loop/scripts/lib.sh` 里的 `npu_lock` / `npu_unlock` 若仍在被别的脚本调用，可以保留（它只是 `flock`，没有超时问题），但新代码不要再引入。

## 2. 怎么用

```sh
# 单用例：迭代时的快信号（默认）
/workspace/cannbench/loop/scripts/evaluate.sh <op_id> <project_dir> mytag 1

# 指定几个用例
/workspace/cannbench/loop/scripts/evaluate.sh <op_id> <project_dir> mytag 1,2,5

# 整算子全部用例（仍然一次一个进程）：只有这个给出可比的算子分
/workspace/cannbench/loop/scripts/evaluate.sh <op_id> <project_dir> mytag all
```

输出的 JSON 保持历史字段（`op/rc/score/pass_rate/avg_speedup`）：

- **采样跑**（`1` 或 `1,2,5`）：`score` 为 `null`，并带 `partial: true`。少跑用例拿不到真实算子分——全过奖励和各项分母都按整算子定义。此时看 `mean_perf_score` 做接受/回退判断。
- **全量跑**（`all`）：`score` 是有值的，等于官方 Eq.4 权重算出的算子分（编译 5 + 精度 20 + 性能 70·均值 + 全过 5）。

## 3. 验证过什么

- **整算子分对得上**：`swi_glu` 20 个用例逐用例跑完得 58.58，官方全量 harness 记录 58.36（差 0.2，profiler 噪声量级）。逐用例不是"另算一套分"。
- 聚合公式对得上：把全量 harness 的 per-case `perf_score` 代入 `5 + 20·通过率 + 70·均值 + 5`，复算结果与官方 `overall_score` 差 1.4e-14。
- 单用例也对得上：`swi_glu` case 1 全量 6.04 µs / 0.523，逐用例 5.98 µs / 0.527。
- **并发不 hang**：两个进程同时各跑一次 profiled matmul，都正常完成并各自产出完整 capture。runtime 按下发顺序排队，两个 kernel 串行执行。
- **并发评测也不 hang**：同一套逐用例入口，1 个 / 2 个 / 4 个算子同时跑，全部 `rc=0`、精度全过、每个 case 都产出完整 `kernel_details.csv`；4 个并发时 cgroup 内存峰值 21.8 GB（上限 32 GB）、load 5.5，设备始终健康。同一 case 的耗时：独占 4.23 µs，2 并发 5.69 µs，4 并发 6.00 µs——变慢但没崩。
- **去掉锁后脚本照常工作**：删掉 `cnb-npu-lock` 前导后，`evaluate.sh` / `percase_eval.sh` 单跑和两个并发都跑通，`pass_rate` 仍是 1.0。

## 4. 注意

- 评测进程 OOM 被杀（rc=137）几乎都是**宿主内存**不够：查 `/sys/fs/cgroup/memory/memory.oom_control` 里的 `oom_kill` 计数，并确认是不是有多个构建在并发跑。先限制编译并行度（`CNB_CMAKE_JOBS` / `CMAKE_BUILD_PARALLEL_LEVEL` / `MAX_JOBS`），别去找卡的问题。
- 不要用裸 `python3` 跑 torch（CANN 下会 `undefined symbol`）；走 `cnb-env`。
- 容器暴露 128 核但只有 32 核配额，`os.cpu_count()` 驱动的构建会开到 128 并行、把 32GB cgroup 撑爆。所以脚本里给 `CNB_CMAKE_JOBS` / `MAX_JOBS` 限到 8。
- 单个用例耗时通常几十秒；`all` 是 20 个用例，几分钟到十几分钟，属正常。
- 要对比两个版本的性能时，别让另一个 profiler 同时在跑，否则测到的是被排队放大的时间。
