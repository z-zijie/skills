---
name: iteration
description: Ascend NPU 单 kernel 的"测量→优化"迭代纪律：ASC 单文件直调 bench（bisheng 一次编出可执行文件，kernel-perf 直接 profile）、刷 L2 的采样协议、warmup 剔除、跨进程重复取中位数、一条一条消融并过精度门。当要给一个 kernel 做多轮性能优化、判断某次改动是否真有收益、或怀疑"变快了"的数字不可信时使用。
---

# 迭代

一轮迭代只回答一件事：这次改动是否让这个 kernel 更快。答案的可信度来自测量协议，不来自改动看起来是否更聪明。

## 1. 建直调链路

一个 `.asc` 文件装下 host 驱动与 device kernel，`bisheng` 一次编出可执行文件，kernel-perf 直接 profile 这个二进制。

```shell
bisheng -fPIC --npu-arch=dav-3510 -O3 --asc-aicore-lang \
  bench.asc -o bench -lm -ldl -lplatform -ltiling_api -lmsprofiler
```

- 链路里没有 wheel、pip、python3、装包。装包是这条链上唯一会静默失败、吞掉半小时的环节，一次也不要有。
- 实测编译 3 s 级（host + kernel 全量），改一行到出数字是秒级；这条速度就是迭代频率本身。
- 每条消融轴写成一个 `-D` 宏，默认值逐位复现基线。一个源文件覆盖整张矩阵，不按实验分叉文件。
- 骨架 [`references/bench-skeleton.asc`](references/bench-skeleton.asc) 可编可跑，内含 L2 刷、warmup、精度门。
- [`scripts/build.sh`](scripts/build.sh) 与 [`scripts/measure.sh`](scripts/measure.sh) 包掉两处环境陷阱：`bisheng` 找不到 `ASCEND_HOME_PATH` 时**退出码是 0**，产物存在与否才是判据；bench 二进制链接 CANN 运行时，非登录 shell 里必须 source 才跑得起来。

完成判据：`scripts/build.sh` 打印 `ok`，`scripts/measure.sh` 打印一行 `median=...` 与一行 `accuracy`，且同一二进制能被 kernel-perf 直接 profile。

## 2. 先立标尺

第一件事不是改代码，而是用**未改动的代码 + 最终探针**连跑，得到中位数与极差。此后一切"有没有收益"都对着这把尺子。

标尺由三个不可分割的身份构成：**二进制、探针（host driver）、cache 状态**。任一变了，之前的数作废——不是折算，是作废。

实测：同一份源码，只把探针从不刷 L2 换成刷 L2，数字从 4.589 掉到 3.638 µs；把这 20% 当成优化收益，会顺着假信号继续往错方向改代码。这是本流程最贵的一次教训。

完成判据：同代码同探针 ≥5 个样本，中位数、极差、逐进程中位数落盘，作为基线行。

## 3. 每次计时前刷 L2

Ascend950PR 的 L2 是 128 MiB，而小 case 的输入输出整块装得下。不刷 L2 测的是缓存命中态，不是评分态。

- 协议：每次计时前用一个远大于 L2 的缓冲跑一遍读 + 写。实测 96×1024×1024 fp16 = 201 MB，与 CannBench `cann_bench_cache_clean` 同形（`references/bench-skeleton.asc` 的 `flush_kernel`）。
- 实测偏差：同一二进制各跑 5 进程 × 10 次，`FLUSH=1` 合并中位数 11.82 µs，`FLUSH=0` 7.41 µs，差 37%。核越小偏差越大，因为数据段越容易整块驻留。
- 只在"确认刷 L2 真的生效"这一件事上关掉它：数字明显下降，说明这条确实在起作用。

完成判据：bench 默认 `FLUSH=1`；关掉后数字显著变快，二者都记录在案。

## 4. warmup 不进统计

首次 launch 付 TLB 建表与 I-cache 填充。实测首样本比稳态高 13%（7.21 vs 6.37 µs），与内核好坏无关。

- `WARMUP=5` 起步，样本从 warmup 之后开始计。
- 每换一个进程就要重新 warmup，跨进程合并统计里再剔除每个进程的首样本。

完成判据：每个进程的计时窗口都从 warmup 之后开始；合并统计里每个进程只贡献第 2..N 个样本。

## 5. 采样与裁决

单个数不下结论。同配置 ≥3 个独立进程 × ≥10 次计量，取合并中位数。

| 现象 | 实测 |
|---|---|
| 同一二进制 5 进程的逐进程中位数 | 11.32 / 11.46 / 12.04 / 12.73 / 12.16 µs（进程间极差 11.7%） |
| 同一源码另一组 5 进程 | 进程间极差 25.5% |
| 同上二进制合并中位数（n=45） | 11.82 µs，单样本 min 10.84 / max 14.72 |
| kernel-perf 同一二进制采 5 次 Duration | 7.65 / 7.65 / 7.73 / 7.73 / 7.80 µs（极差 2.0%） |
| 同上，pipe 占比 | scalar 9.55–10.18%、MTE2 52.50–52.93%（稳定到 1 个点） |
| 同上，逐核 spread | 13.8% – 19.4%（同一二进制抖 5.6 个点） |

- **方向一致**是判据：多次重复里中位数都朝同一方向、且跨过实测噪声，才算效应。
- 噪声就是上面那张表里的实测极差，没有第三个叫作"噪声容差"的宽容概念：跨过它算效应，跨不过就是没有。
- Duration 与 pipe 占比可以当判据；逐核 spread 只能当线索——它在同一二进制上就抖 5.6 个点。

完成判据：每次裁决附 ≥2 组独立重复、方向一致、明确写出跨没跨过噪声。

## 6. 一条一条消融

- 一次只动一个 `-D`，其余全默认。每条改动前后各取一组数，改动之间互不叠加。
- 改之前先记当前 pipe 画像（scalar / MTE2 / MTE3 / vec / spread），否则说不清"瓶颈有没有移位"。瓶颈从标量转到搬运，是比 Duration 更有信息量的信号。
- **精度是门，不是注脚**：每条都要过 `bad=0` 且 MERE 在阈值内。实测被它拦下两类静默错误：tile 越出 UB 预算时 10.5 万、12.6 万个错误元素；tile 长度没按向量宽度对齐时 1792 个。两类都是计时更快、结果更错——没有精度门的加速等于没测。
- 归零要接受：更多方向在可信标尺上就是 0。写"证伪"并回退，比留着一条没证实的改动便宜得多。

完成判据：每条一行——id、改动、中位数前后、精度、keep/discard、一句话教训。

## 7. 定稿才用评分口径

迭代期一律用 bench 的 host-event 或 kernel-perf Duration 做**相对**比较；CannBench 评分口径只在定稿时跑一次。

口径决定数的大小，混用即作废：同一份骨架，host-event 合并中位数 11.82 µs，kernel-perf `Duration` 7.73 µs——差的是计时区间把 kernel 之外的派发与同步也圈了进去。打榜工程上同源代码另有 host-event 4.04 µs 对评分 3.05–3.20 µs。这些差值是口径，不是优化空间。

完成判据：报告里每个数字都带口径与重复次数。

## 8. 把新踩的坑写回去

这个 skill 的每一条都是某次测量骗过人之后留下的。协议继续演进的方式是同一个循环：**被假数字骗一次 → 定位到协议缺了哪一环 → 补成一条可执行的规则**。

- 先判断该落在哪一层：改变默认动作的（warmup、刷 L2、重复次数、精度门）进 SKILL.md 的步骤；只是参照数据的（某台机器的噪声表、某条消融的实测值）进 [`references/measurement-protocol.md`](references/measurement-protocol.md)；只在特定瓶颈下才用得到的（消融轴、已证伪项）进 [`references/ablation-axes.md`](references/ablation-axes.md)。
- 一条规则只有在**实测复现过**之后才写进去，并带上数字。没有数字的经验会退化成"要小心"这类正确的废话，下一个人照样踩。
- 写进去之前按 [`writing-for-agents`](../writing-for-agents/SKILL.md) 过一遍：一个概念只留一处权威表述；给每一步配可核对的完成判据；用"刷 L2 / 立标尺 / 精度门"这类已有含义的词承载概念，不新造术语；删掉模型默认就会做的那类句子。
- 旧规则被新证据推翻时改写它，不叠加一条相反的——两条并存的规则等于没有规则。

完成判据：本轮新增或改写的每条规则，都能指到一次具体测量（时间、命令、数字），且落点在上面三层里选对了。

## 参考

- [`references/measurement-protocol.md`](references/measurement-protocol.md)：环境、命令模板、重复矩阵、噪声实测全表、口径对照
- [`references/bench-skeleton.asc`](references/bench-skeleton.asc)：已验证的直调骨架（编译 3.5 s、默认 `bad=0`、含 L2 刷与精度门）
- [`scripts/build.sh`](scripts/build.sh)、[`scripts/measure.sh`](scripts/measure.sh)：已验证的编译与重复矩阵脚本
- [`references/ablation-axes.md`](references/ablation-axes.md)：按瓶颈分类的消融轴清单、已证伪项、工具链陷阱
- [`kernel-perf`](../kernel-perf/SKILL.md)：pipe 画像与逐核分布的采集工具，本 skill 只定测量纪律，采集走它
