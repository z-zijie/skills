# dfir — DataFlow IR + 性能估算器

**目标**：给定任意 DataFlow（AIV / AIC / MIX），估算运行时间误差 < 5%。

## 为什么用硬件计数器

同一配置重复发射的离散度：

| 观测量 | 跨发射 CV |
|---|---|
| 墙钟时间 | 7–14% |
| `aiv_total_cycles` / `aic_total_cycles` | **1.2–4.2%** |

墙钟时间下 5% 不可达（已证明：完美模型在同一评分下也只有 8.5% 误差）。
所以 IR 的时间单位是**周期**，不是微秒。

## 结构

```
ir.py          IR 定义（node/flow）、解析、拓扑排序
costs.py       各通路成本模型；MTE2 已标定，其余 calibrated=False
estimate.py    pipe/stream 调度 + 关键路径估算，输出 per-pipe 与 bottleneck
calibrate.py   从计数器 CSV 重新拟合 MTE2Cost，带 LOO-CV
verify.py      端到端：IR → 估算 vs 实测
ROADMAP.md     里程碑
calibration-bsweep.csv  标定用原始数据（65 配置 × 9 次发射）
```

## MTE2 已标定模型（M0）

```
cycles = B*( t_setup + S*r1*(1+(B/k1)^p1) + (K-1)*(c_extra + S*r2*(1+(B/k2)^p2)) )
```

参数（`calibrate.py` 拟合，63 点）：

| 参数 | 值 | 含义 |
|---|---|---|
| t_setup | 1788 | 每核固定开销（不随字节变） |
| r1, k1, p1 | 0.01608, 26, 1.20 | 第一份拷贝的每字节成本及其随核数的增长 |
| c_extra | 230 | 每多一份拷贝的固定部分 |
| r2, k2, p2 | 0.00674, 12, 1.20 | 追加拷贝的每字节成本（随核数增长更快） |

**精度**：样本内 MAPE 3.78%，**LOO 交叉验证 3.91%**，中位数 2.09%，75% 的点在 ±5% 内。

### 两个非平凡的结构发现

1. **t_setup 按核计一次，不按字节**。B=4 时用 S=32K 和 64K 两点相减可精确解出：
   rate=0.0194 cyc/B、t0=2006 cyc。纯 bandwidth 模型会把小包算错几倍。
2. **追加拷贝比第一份"便宜"但有下限**。K=2/K=1 的耗时比只有 1.2–1.6（不是 2.0），
   且该比值随 B 上升、随 S 下降 —— 说明追加拷贝有一个**与字节无关**的部分（c_extra）。

### 已知未建模：拓扑断点

`K=2, S=32KB, B≥52` 出现 +175k 周期的台阶（B=48→52，K=1 在同一位置只涨 3k）。
这是**资源/拓扑阈值**，不是字节与核数的光滑函数。当前做法是**显式排除并标注**，
不把它混进平滑模型里（否则会同时污染两侧的拟合）。建模它是 M1 的后续工作。

## 用法

```python
from ir import parse
from estimate import estimate

flow = parse({
  "name": "load_compute_store", "cores": 56,
  "nodes": [
    {"id": "in",  "kind": "copy",    "pipe": "MTE2", "bytes": 102400},
    {"id": "op",  "kind": "compute", "pipe": "VEC",  "elem": 25600, "deps": ["in"]},
    {"id": "out", "kind": "copy",    "pipe": "MTE3", "bytes": 102400, "deps": ["op"]},
  ]})
r = estimate(flow)
print(r.us, r.bottleneck, r.warnings)
```

`r.warnings` 会明确列出 flow 用到的**未标定通路**——5% 的结论不会被错误地延伸到未测量的引擎上。

## 当前精度边界

- **MTE2 单通路（AIV）**：3.9% LOO，达标
- **MTE3 / VEC / AIC / Fixpipe**：未标定，估算仅作占位
- **多通路重叠（pipe/stream 调度）**：逻辑已实现，但没有实验验证
- **MIX（AIC+AIV 协同）**：未开始
