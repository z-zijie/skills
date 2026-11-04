# DataFlow 性能建模 — 里程碑路线图

**终极目标**：给定任意 DataFlow（AIV / AIC / MIX，含 MTE2、MTE3、MTE1、Fixpipe、VEC、MAC 等通路），
性能估算误差 **< 5%**。

**方法**：一套 DataFlow 描述 IR + 基于该 IR 的、由硬件计数器标定的成本模型。

---

## 为什么用硬件计数器

实测（`data/hwclean.csv`）：同一配置重复发射时

| 观测量 | 跨发射离散度 |
|---|---|
| 墙钟时间 | 7–14% |
| `aiv_total_cycles` / `aic_total_cycles` | **1.2–4.2%** |

5% 的目标在墙钟时间下不可能达到（已证明：完美模型在同一评分下也只有 8.5% 误差）。
计数器把噪声底压到 1–4%，5% 才有意义。

---

## Milestone

| # | 范围 | 标定对象 | 状态 |
|---|---|---|---|
| **M0** | IR 骨架 + MTE2 单通路 | `aiv_total_cycles` (MTE2) | **已通**：IR 解析/调度/估算可跑，MTE2 满载 LOO 5.18% |
| **M1** | AIV 全通路 | MTE2 / MTE3 / VEC | **完成**：流水线 K≥4 误差 3–5.5%；引入 isolated/streaming 两套上下文 |
| **M2** | AIC 全通路 | MTE1 / MAC / **Fixpipe** | **完成**：reps 扫描 5 点，模型误差 ±0.2%；发现 AIC 计数器时间基与 AIV 不同 |
| **M3** | MIX 协同 | AIC↔AIV 的 CrossCore 同步开销 | **完成**：686 周期/次握手 |
| **M4** | 泛化验证 | 任意 flow | 待做：用真实算子（matmul+vector epilogue 等）做端到端 <5% 验证 |

---

## M0 已完成的内容

```
dfir/
  ir.py          IR 定义、解析、拓扑排序
  costs.py       各通路成本模型（MTE2 已标定，其余标 provisional + calibrated=False）
  estimate.py    pipe/stream 调度 + 关键路径估算，输出 per-pipe 与 bottleneck
```

IR 形态（JSON）：节点 = `{id, kind: copy|compute|barrier, pipe, bytes|elem, deps, stream}`，
flow = `{name, cores, nodes}`。执行模型：同一 pipe 串行、不同 pipe 可重叠、`deps` 强制顺序，
kernel 时间取关键路径。

**设计要点**：`CostModel.calibration_report()` 会明确标出哪些通路已标定。
估算器对使用未标定通路的 flow 会给出 warning —— 这样"5%"不会被错误地宣称到未测量的通路上。

---

## M1 结果（完成）

### 单通路标定
| 通路 | streaming 系数 @B=56 |
|---|---|
| MTE2 100KB | 305 006 cyc |
| MTE3 100KB | 405 141 cyc |
| VEC 25600 elems | 47 310 cyc |

三者相加 757 457，与流水线实测增量 757 457 **完全吻合** —— 这是模型正确的闭合检验。

### 核心结论：单通路系数不能直接组合

同一次传输在孤立场景和流水线里代价不同：

| @B=56 | 孤立 | 流水线 | 比值 |
|---|---|---|---|
| MTE2 | 423 880 | 305 006 | 0.72 |
| MTE3 | 505 269 | 405 141 | 0.80 |

流水线里更便宜，因为多条传输同时在飞能吃满总线。所以模型必须有 isolated /
streaming 两套系数，由 flow 形状自动选择（`per_pipe_copies > 1` → streaming）。

### 精度（B=56）
K=4 → +5.5%，K=8 → +3.0%。K=1 是 +33.7%，因为单次流程不存在 streaming，
用 streaming 系数会高估；真实算子很少是 K=1 的流水线，此边界已标注。

### 修掉的三类错误
1. 4 倍重复计数（K=4 launch 总和当单次 store）
2. 56 倍单位错误（全核求和未除核数）
3. 模型族错误（缺 isolated/streaming 区分，高估 34%）

### 方法论沉淀
- 用**稳态斜率**（K=8 − K=4）/4，不用带截距拟合：K=1 受启动效应主导，
  纳入拟合会让截距为负并污染所有系数
- 三 mode 增量互相校验，闭合性即模型正确性的证据

## M2（AIC）

`aic_total_cycles` 已可用；`PipeUtilization` 组提供 `aic_mac_time`、
`aic_mte1_time`、`aic_fixpipe_time` 等分通路时间与占比。
需要写 Cube kernel（matmul）来标定 MAC 与 Fixpipe。

## M2 结果（完成）

matmul reps 扫描（M=128,K=256,N=128，B=28）：

`aic_cycles = 98 730 + 46 994 × mmads`，5 个点误差全部 ≤0.2%。

| 发现 | 内容 |
|---|---|
| 固定成本按 launch 计 | 98 730 cyc（载入 A/B + 一次 Fixpipe），**不随 Mmad 数增长** |
| MAC 成本 | 46 994 cyc/Mmad = 0.0056 cyc/FLOP |
| Fixpipe | 0.48–0.54µs **恒定**，与 reps 无关，是一次性成本 |
| **计数器时间基不同** | AIC 45 900 cyc/us vs AIV 1 650 cyc/us，**换算错 28 倍** |

## M3（MIX）

MIX 模式下 AIC 与 AIV 协同（CV 融合），引入 CrossCore 同步。
要标定的是**同步/握手开销**，它不是字节数的函数。

## M3 结果（完成）

MIX kernel 里 AIC 与 AIV 由同一次 launch 驱动，通过 CrossCoreSetFlag /
CrossCoreWaitFlag 会合。用"带握手 / 不带握手"差分测得：

| iters | sync=1 | sync=0 | delta |
|---|---|---|---|
| 1 | 1 875 | 1 634 | +241 |
| 4 | 4 462 | 2 164 | +2 298 |

**686 AIV 周期/次握手**，已写入 `MixCost`，估算器按 flow 里 AIC↔AIV 的依赖对计数。

### 两个必须先修掉的测量陷阱
1. AIC 侧只翻 flag 不干活 → AIV 从不等待，同步差异测出来是 0。
   "测不出差异"被误当成"开销可忽略"。
2. Abs 操作未初始化的 VECCALC tensor、结果不写回 → 整个循环被死代码消除，
   测到的是纯循环开销。修法：MTE2 载入真实数据 + MTE3 写回。

## M4（泛化）

用真实算子验证端到端误差，而不是只用合成 microbenchmark。
