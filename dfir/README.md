# dfir — DataFlow IR + 性能估算器

**目标**：给定任意 DataFlow（AIV / AIC / MIX），估算运行时间误差 < 5%。

## 为什么用硬件计数器

同一配置重复发射的离散度：

| 观测量 | 跨发射 CV |
|---|---|
| 墙钟时间 | 7–14% |
| `aiv_total_cycles` / `aic_total_cycles` | **1–5%** |

墙钟时间下 5% 不可达（已证明：完美模型在同一评分下也只有 8.5% 误差）。
所以 IR 的时间单位是**周期**，不是微秒。

## 结构

```
ir.py          IR 定义（node/flow）、解析、拓扑排序
costs.py       各通路成本模型
estimate.py    pipe/stream 调度 + 关键路径估算
calibrate.py   从计数器 CSV 重新拟合 MTE2Cost，带 LOO-CV
verify.py      MTE2 端到端：IR → 估算 vs 实测
verify_m1.py   AIV 流水线端到端
calibration-bsweep.csv / m1b.csv   标定原始数据
ROADMAP.md     里程碑
```

## 已完成标定的通路

| 通路 | 状态 | 精度 |
|---|---|---|
| **MTE2** (GM→UB) | ✅ | LOO **3.91%**，75% 点在 ±5% 内（63 点，B=1..56） |
| **MTE3** (UB→GM) | ⚠️ | 三点插值（B=14/28/56），**仅覆盖饱和写**，见下 |
| **VEC** | ⚠️ | 单点标定：fp32 Add 0.00523 cyc/elem（每核） |
| **AIC / Fixpipe** | ❌ | 未标定（M2） |

## MTE2 模型（M0，最成熟）

```
cycles = B*( t_setup + S*r1*(1+(B/k1)^p1) + (K-1)*(c_extra + S*r2*(1+(B/k2)^p2)) )
```

| 参数 | 值 |
|---|---|
| t_setup | 1788 |
| r1, k1, p1 | 0.01608, 26, 1.20 |
| c_extra | 230 |
| r2, k2, p2 | 0.00674, 12, 1.20 |

**两个非平凡结构发现**
1. **固定开销按核计一次，不按字节**（B=4 用两个尺寸相减可精确解出）
2. **追加拷贝比第一份便宜**（1.2–1.6 倍而非 2 倍），且含与字节无关的成分

**已知未建模**：`K=2, S=32KB, B≥52` 的 +175k 周期拓扑台阶，显式排除并标注。

## M1 状态：AIV 三通路已测，但流水线预测**不达标**

三个通路单独标定都做了，但把它们组合成真实流水线时误差 **174%**，而且方向很说明问题：

| K=4 @ B=56 | cycles |
|---|---|
| mode1 (MTE2 in + 4 MTE3 stores) | 1 470 822 |
| mode2 (2×MTE2 in + 4 VEC) | 682 344 |
| mode3 (完整流水线 load→vec→store) | 2 894 400 |
| **每次迭代** | **723 600** |
| 若三通路完全串行相加 | ~538 292 |

**流水线每次迭代比串行求和还贵**（723k vs 538k）——说明 MTE2 和 MTE3 同时活跃时
**争用同一条总线**，互相拖慢。估算器当前假设"同 pipe 串行、跨 pipe 完全并行"，
这个执行模型是错的，缺一个**跨 pipe 的总线争用项**。

这不是标定误差，是**执行模型的缺陷**。所以 M1 的结论是：
- 单通路标定：完成
- 多通路流水线预测：**未达标**，缺总线争用模型
- 修法：给 `estimate.py` 加一个"MTE2+MTE3 同时活跃时按共享带宽串行化"的规则，
  系数从 mode3 的数据标定

这个缺口已写入 ROADMAP 的 M1 后续项，不会在报告里含糊过去。

## 用法

```python
from ir import parse
from estimate import estimate

flow = parse({"name": "lc", "cores": 56, "nodes": [
  {"id": "in",  "kind": "copy",    "pipe": "MTE2", "bytes": 102400},
  {"id": "v",   "kind": "compute", "pipe": "VEC",  "elem": 25600, "deps": ["in"]},
  {"id": "out", "kind": "copy",    "pipe": "MTE3", "bytes": 102400, "deps": ["v"]},
]})
r = estimate(flow)
print(r.cycles, r.bottleneck, r.warnings)
```

`r.warnings` 明确列出 flow 用到的**未标定通路**，5% 结论不会被延伸到未测量的引擎。
