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
| **M1** | AIV 全通路 | MTE2 / MTE3 / VEC / SCALAR | 进行中：核数曲线扫描 + 四通路标定 |
| **M2** | AIC 全通路 | MTE1 / MTE2 / MTE3 / MAC / **Fixpipe** / SCALAR | 待做：Cube kernel + `aic_*` 计数器 |
| **M3** | MIX 协同 | AIC↔AIV 的 KFC / CrossCore 同步开销 | 待做：Mix kernel，AIC+AIV 同时活跃 |
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

## M1（当前）

1. **核数标定**（进行中）：硬件计数器扫 B=1..56 × S=32/64KB × K=1/2，
   得到 per-byte 成本随 CoreNum 的变化。已知趋势：
   - B≤21：每核成本≈恒定 3400 cyc/64KB（≈31 GB/s/核），**核之间几乎独立**
   - B>21：开始上升，B=56 时约 4100 cyc/64KB
2. **MTE3 标定**：UB→GM 的 copyout，同样用计数器。
3. **VEC 标定**：不同 dtype 的 per-element 成本。
4. 目标：AIV 单核/多核 flow 误差 <5%。

## M2（AIC）

`aic_total_cycles` 已可用；`PipeUtilization` 组提供 `aic_mac_time`、
`aic_mte1_time`、`aic_fixpipe_time` 等分通路时间与占比。
需要写 Cube kernel（matmul）来标定 MAC 与 Fixpipe。

## M3（MIX）

MIX 模式下 AIC 与 AIV 协同（CV 融合），引入 CrossCore 同步。
要标定的是**同步/握手开销**，它不是字节数的函数。

## M4（泛化）

用真实算子验证端到端误差，而不是只用合成 microbenchmark。
