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

## M1 状态：三通路标定完成，流水线 K≥4 在 5% 内

### 最终模型：区分 isolated / streaming 两种上下文

这是 M1 最重要的结论。**单通路系数不能直接组合**：

| @B=56 | 孤立单次 | 流水线内 | 比值 |
|---|---|---|---|
| MTE2 100KB | 423 880 | 305 006 | 0.72 |
| MTE3 100KB | 505 269 | 405 141 | 0.80 |
| VEC 25600 | 52 890 | 47 310 | 0.89 |

流水线里每次传输都**更便宜**，因为多条传输同时在飞时能吃满总线，而单次传输填不满。
所以模型必须有上下文字段：

- **isolated**：flow 里每个 pipe 只有 1 次传输 → 用单次系数
- **streaming**：每个 pipe 有多条在飞（如双缓冲预取）→ 用流水线系数

IR flow 的形状自动判定：`per_pipe_copies > 1` 即为 streaming。

### 精度（B=56）

| K | 实测 | 估算 | 误差 |
|---|---|---|---|
| 1 | 734 498 | 982 059 | +33.7% |
| 4 | 2 894 400 | 3 052 229 | **+5.5%** |
| 8 | 5 924 230 | 6 104 459 | **+3.0%** |

**K=1 是边界情况**：只有一次传输，谈不上 streaming，用 streaming 系数会高估。
真实算子很少有 K=1 的单次流水线，所以这个边界在 README 里标出来而不去掩盖。

### 过程中修掉的三类错误（都影响了精度）

1. **4 倍重复计数**：把 K=4 的 launch 总和当成"一次 store"的代价，导致流水线高估 3.75 倍
2. **56 倍单位错误**：`aiv_total_cycles` 是全核求和，per-element 成本没除以核数
3. **模型族错误**：单通路系数组合不出流水线，缺 isolated/streaming 区分，高估 34%

### 方法论

- 用**稳态斜率**（K=8 减 K=4 除以 4）而不是带截距的拟合：K=1 受启动效应主导，
  把 K=1 放进拟合会让截距变成负值并污染所有系数
- 三种 mode 的增量互相校验：pipeline 增量 − MTE3 增量 − VEC 增量 = MTE2 增量，
  算出来 305 006，与独立测得的 307 329 差 0.7% —— 这个闭合是模型正确的证据

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
