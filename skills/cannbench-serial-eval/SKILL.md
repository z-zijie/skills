---
name: cannbench-serial-eval
description: 在 Ascend950PR 单卡机器上跑 CannBench 算子评测时，用串行、逐用例的方式代替全量 run_evaluation.sh。当需要评测/打分 CannBench 算子、机器因并发跑多个算子而卡死或 hang、或要解释为什么不能用官方全量评测脚本时使用。
---

# CannBench 串行逐用例评测

Ascend950PR 只有一张卡，且**并发跑多个算子开发会让整机卡死**，恢复代价极大。官方的 `scripts/run_evaluation.sh` 默认把每个用例 fan-out 到多个 worker 进程，正是卡死的诱因之一。所以这台机器上的评测规则是：**严格串行 + 一个用例一个独立进程**，绝不打全量 harness。

本文给出落地方式。脚本在本 skill 的 `scripts/` 下，部署目标见第 1 节。

## 为什么不能打全量

- `run_evaluation.sh` → `kernel_eval.staged_eval` → `ProcessPoolCoordinator`，按 `processes-per-card`（默认 2）开并发子进程；多算子同时开发时，容器内存 cgroup 与单卡 profiler 资源都会被压爆。
- 实测过：并发 pi 轮次 + 多进程 profiler 让整机 load 飙到 64+，出现大量 OOM kill（rc=137），机器失去响应。

结论：**评测必须一次只测一个用例，且同一时刻只有一件事在用卡。** 慢一点没关系，卡死一次浪费的是几十分钟到几小时。

## 1. 两个必须同时具备的机制

只做"逐用例"不够——还得保证**任何路径都拿不到卡**才算串行。落地成两件事：

### 1.1 官方 harness 的逐用例入口

不要自己写一套"差不多的"精度/计时逻辑，那样算出来的分和榜单不可比。harness 自带单进程单用例入口：

```sh
python -m kernel_eval.cli eval-child \
  --bench-name cann --device-id 0 \
  --cases-file <一个只含该 case 的 JSON> \
  --output <结果 JSON> --reports-dir <目录> --task-dir <tasks> \
  --warmup 3 --repeat 5 --no-perf-batch-cases --profiler-level Level1
```

它跑的是官方 accuracy checker、官方 profiler 计时、官方 Eq.3 单例分。`--no-perf-batch-cases` 必须给：批量 profiler 需要多用例 fan-out，正是要避免的东西。

部署 `scripts/percase.py`（驱动上面的入口，一个 case 一个进程）到 `/workspace/cannbench/loop/scripts/percase.py`。

### 1.2 一把评测绕不过的锁

逐用例只是把并发度降到 1，还得保证**两个评测不会同时上卡**。锁落在评测入口，不落在环境包装器上：

- `scripts/cnb-npu-lock`：拿 `state/npu.lock` 再执行命令。**可重入**靠探测自己进程已打开的锁 fd（以及 `CNB_NPU_LOCK_HELD`），所以内层调用不会排在自己祖先后面。
- `scripts/evaluate.sh` / `scripts/percase_eval.sh` 开头加一道 self-lock 前导：`CNB_NPU_LOCK_HELD != 1` 时 `exec cnb-npu-lock "$0" "$@"`。这样"编译 → 安装 → 逐用例评测"整段是原子的，别人插不进来。

**不要**把锁塞进 `bin/cnb-env`。它被 build.sh、wheel 构建、各种嵌套调用复用，会在自己的子树里再次申请同一把锁而死等（实测踩过）。评测入口才是唯一的串行边界。

部署目标：`/workspace/cannbench/bin/cnb-npu-lock`，`scripts/percase_eval.sh`、`scripts/evaluate.sh`、`scripts/eval_op.sh` 到 `loop/scripts/`（或 `scripts/`）。`bin/cnb-env` 保持原样。

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
- 评测入口串行：两个 `evaluate.sh` 同时起、以及两个 `cnb-env python` 同时起，都严格排队，零重叠（实测 `A` 23:22:39–45、`B` 23:22:46–52）。

## 4. 注意

- 评测进程 OOM 被杀（rc=137）说明并发还是太多了，先确认没有第二件事在用卡。
- 不要用裸 `python3` 跑 torch（CANN 下会 `undefined symbol`）；走 `cnb-env`。
- 容器暴露 128 核但只有 32 核配额，`os.cpu_count()` 驱动的构建会开到 128 并行、把 32GB cgroup 撑爆（实测 83 次 OOM kill）。所以脚本里给 `CNB_CMAKE_JOBS` / `MAX_JOBS` 限到 8。
- 单个用例耗时通常几十秒；`all` 是 20 个用例，几分钟到十几分钟，属正常。
