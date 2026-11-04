# DataFlow IR → 性能：神经网络建模思路调研

> 请求：*"是否可以设计一个神经网络，输入是 DataFlow 的 IR，输出是估计的性能？采集大量数据训练。deepresearch 一下这个思路，看看有没有类似的 paper"*
> 时间：2026-11-04

## 结论先说

**这条路线有大量成熟先例，但在我现在这套 IR 上直接套用会失败——不是因为模型不够强或数据不够多，而是因为 IR 里缺了最关键的那个特征。**

东八区时间 2026-11-04 的 M11 留出验证给了决定性证据：

| 算子 | 解析模型误差 | 主导耗时项 |
|---|---|---|
| matmul_leakyrelu_**basic** | **−4.8%** | 搬运/计算（可被 IR 表达）|
| matmul_leakyrelu_**advanced** | **−85.2%** | **aic_scalar 34µs / 39µs（87%）** |

advanced 算子的 IR 描述和 basic 几乎一样（都是 copy + cube + vec），
但它的耗时 87% 在标量指令上。我的 IR 里**没有字段能表达"这个节点有多少索引计算/循环开销"**，
所以对神经网络来说，这两个节点的输入特征相同、输出却差 34 倍。

**这是表征问题（representational），不是容量问题或数据问题。** 喂进去的特征里没有答案，
再大的网络也学不出来。

---

## 一、文献里已有的工作

这个方向不新，已形成几个清晰的分支。

### 1.1 学习型 cost model 用于编译器搜索（最成熟）

| 工作 | 做法 | 与本任务的关系 |
|---|---|---|
| **TVM / Ansor** | 用 GNN 编码 tensor program 的计算图，预测吞吐，替代昂贵实测来指导搜索 | 最直接的对标：IR → GNN → 性能 |
| **Ithemal**（MIT） | 把汇编 basic block 转成 token 序列，用 RNN/Transformer 预测吞吐，误差可到个位数 % | 证明"指令级特征 → 性能"可行，**但它的输入是真实指令序列** |
| **TLP / NLTSP** | 用嵌套循环树编码 tensor program，再回归 | 强调输入表示要能反映循环结构 |
| **Compiler World Models** | 学习 tensor program 搜索的隐动力学 | 更偏搜索加速而非绝对性能 |

关键观察：**这些工作的输入都包含了足够的结构信息**。Ithemal 直接吃指令，
Ansor/TLP 吃完整的循环嵌套 + 访存模式。它们的成功**依赖于输入能区分不同的成本来源**。

### 1.2 Dataflow 加速器专用

- **LLMulator**：面向 dataflow 加速器、支持输入自适应控制流的泛化代价模型。
  最贴近"任意 DataFlow"这个目标的一篇。
- **Sample-Efficient Mapspace Optimization**：贝叶斯学习做 DSE，强调**样本效率**。

### 1.3 跨硬件迁移

- **TCL**（Continual Learning for cross-hardware tensor program optimization）：
  换硬件时用持续学习复用已有模型，而不是从零训。
- 多篇 transfer-learning 工作表明：**换硬件后需要的不是重训，是少量校准 + 微调**。

### 1.4 昇腾生态内已有的东西（值得先看，别重复造）

- **MindStudio-Ops-Modeling**（华为官方）：算子级性能建模工具。
- **MindStudio-Modeling**：基于 PyTorch 的大模型推理性能仿真。
- **torch_npu `_inductor` 的 CostModel 特性**：编译后端里的代价模型。

**这几件事很重要**：官方工具已经做过一轮，而且 MindStudio-Modeling 的自述里
明确承认了精度局限（融合算子如 SwiGlu/AddRmsNorm 无法建模、HCCL 通信与理论带宽差距显著、
FRACTAL_NZ 等硬件特性缺失）。**这和我 M11 撞到的是同一类墙——表征覆盖不到的部分就预测不准。**

---

## 二、用我的实测数据评估可行性

### 2.1 数据量：远远不够做"泛化到任意 DataFlow"

我现在有：

| 来源 | 行数 | 独立配置数 |
|---|---|---|
| MTE2 核数/尺寸扫描 | 650 | ~65 |
| M1 三通路 | 153 | ~17 |
| M2 Cube | 50 | 7 |
| VEC 律 | 20 | 20 |
| 密集网格 | — | 38 |
| **合计** | **873 行** | **~150 个配置** |

而且这 150 个配置只来自**5 个我自己构造的合成 kernel**、**2 种硬件单元**（AIV/AIC）。

学习型模型要泛化的是**算子结构空间**，不是某个 kernel 内部的参数插值。
150 个配置、5 个 kernel 的多样性，离这个目标差得远。
Ansor 那类工作动辄需要**数十万到数百万**个实测样本（或用仿真器生成）。

### 2.2 关键矛盾：我的解析模型已经在"简单"情形达标了

M11 的 basic 算子，解析模型 **−4.8% 达标**，而且：

| 分量 | 来源 |
|---|---|
| MTE1 载入 2.64µs | M5 独立标定（5 点）|
| CUBE 3.17µs | M2 独立标定（reps 1–16）|
| VEC 2.89µs | M6 独立标定（B=4–56）|
| handshake 0.42µs | M3 差分测量 |
| 偏移/拖尾 | M10（1320 次发射）|
| 启动开销 1.81µs | **唯一对这个算子拟合的值** |

六个独立标定 + 一个拟合值就达标了。**这种情况下换成神经网络，收益是负的**：
- 需要量级更多的数据
- 失去可解释性（现在我能说出 87% 花在 scalar 上，神经网络给不出这句话）
- 失去外推保证（解析式在标定范围内至少行为可预期）

### 2.3 神经网络真正能帮的地方：补上解析模型表达不了的部分

反过来看 advanced 算子的失败：**不是解析方法不行，是我的 IR 特征不够。**
这恰恰是神经网络**可能**有优势的地方——如果输入特征足够丰富：

```
现在:  node = {kind, pipe, bytes, copies, deps}
        ↑ 无法区分"一次简单 copy"和"一次带复杂索引的 copy"

需要:  node = {..., instr_count, loop_depth, index_terms, stride_pattern,
              branch_count, unroll_factor, ...}
```

**神经网络不是用来替代物理解析式的，而是用来学"解析式算不出来的残差"**——
这是文献里 hybrid / residual modeling 的常见做法，也是我认为唯一值得走的方向。

---

## 三、建议的路线（分阶段，先验证再投入）

### 阶段 0（必须先做，成本低）：修 IR 表征

在做任何神经网络之前，先让 IR 能表达标量成本：

1. 给 node 加字段：`instr_est`（标量指令条数估计）、`loop_depth`、`index_terms`
2. 用**已有的 4 个算子**验证这些字段能否区分 basic（0.97µs scalar）和 advanced（34µs scalar）
3. 如果加完字段，**一个简单的线性回归就能把 scalar 预测到可接受误差**，那就不需要神经网络

**这一步可能直接解决问题，且成本极低。** 我建议先做这个。

### 阶段 1（如果阶段 0 不够）：残差学习

```
predicted_time = analytic_model(IR)  +  NN(IR_features)
                    ↑ 已达 −4.8%              ↑ 只学剩下的部分
```

这样神经网络只需拟合**残差**，样本效率高得多，且不会破坏已有的 −4.8% 精度。

### 阶段 2：数据采集基建

若要真正泛化到任意 DataFlow，需要：
- 自动生成多样化的 DataFlow（算子结构随机组合，不只是参数扫描）
- 每个 flow 自动实测（msprof + 硬件计数器，我已有这套 pipeline）
- 目标量级：**数千到数万个独立配置**，覆盖多算子结构、多通路口径、多核数

### 阶段 3：模型选型

按文献经验：
- **IR 是图 → GNN**（Ansor/TLP 路线）
- **IR 是序列/指令 → Transformer**（Ithemal 路线）
- 我的 IR 是 DAG + 节点属性，**GNN 更合适**
- 换硬件时用**持续学习/迁移**（TCL 路线），而不是重训

### 阶段 4：验证纪律

沿用我这次的留出验证方法：**必须用从未参与训练的算子验证**，
而且要包含不同"成本类型"的算子（搬运型、计算型、scalar 型），
否则会重演 M11 的教训——在 basic 上 −4.8%，在 advanced 上 −85%。

---

## 四、我的判断

**思路成立，但不该现在做。**

1. **该先修 IR 表征**。M11 证明当前 IR 无法表达占 87% 的成本项，
   这是任何模型（解析或神经）都绕不过的前提。
2. **解析模型在可达范围内已经很好**（−4.8%），换神经网络在简单情形是负收益。
3. **真正的价值在残差学习**：用神经网络学解析式算不出的部分，而不是替代它。
4. **数据量是硬门槛**：150 个配置 → 需要数千到数万。这是一项独立的数据工程。
5. **先看昇腾官方工具**（MindStudio-Ops-Modeling / torch_npu CostModel），
   避免重复造轮子；它们的公开局限（融合算子、HCCL、FRACTAL_NZ）和我撞的墙一致。

**最小可行的下一步**：给 IR 加标量指令特征，用现有 4 个算子验证能否把
advanced 的 34µs scalar 预测出来。如果成功，可能不需要神经网络就能把 −85% 拉回 ±20%。
如果失败，再上残差学习才有意义。

---

## 参考文献

- [Ithemal: Accurate, Portable and Fast Basic Block Throughput Estimation using Deep Neural Networks](https://dspace.mit.edu/entities/publication/8ce86b0c-2b46-4a33-86a9-23d90fcd0f12)
- [LLMulator: Generalizable Cost Modeling for Dataflow Accelerators with Input-Adaptive Control Flow](https://dl.acm.org/doi/10.1145/3725843.3756027)
- [NLTSP: A cost model for tensor program tuning using nested loop trees](https://www.sciencedirect.com/science/article/abs/pii/S138376212400242X)
- [TCL: Enabling Fast and Efficient Cross-Hardware Tensor Program Optimization via Continual Learning](https://browse-export.arxiv.org/pdf/2604.12891)
- [Toward Compiler World Models: Learning Latent Dynamics for Efficient Tensor Program Search](https://search.arxiv.org/details.jsp?qid=1781812260397ler_nCnN_-148292743&byDate=1&r=pdf/2606.09312)
- [Sample-Efficient Mapspace Optimization for DNN Accelerators with Bayesian Learning](https://scholar.google.ch/citations?citation_for_view=fY3IOd8AAAAJ:UeHWp8X0CEIC&hl=fr&user=fY3IOd8AAAAJ&view_op=view_citation)
- [MindStudio-Ops-Modeling 文档](https://mindstudio-docs-master.readthedocs.io/zh-cn/latest/msopmodeling/)
- [torch_npu _inductor CostModel 特性介绍](https://github.com/ascend/pytorch/blob/790544fa2bd145944935a684760e21e2940ad182/torch_npu/_inductor/docs/feature/costmodel/overview.md)
- [MindStudio-Modeling（昇腾推理性能仿真）](https://atomgit.com/mjsz11/MindStudio-Modeling)
