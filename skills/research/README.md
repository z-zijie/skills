# research

自主研究技能系列。围绕一条可证伪的研究问题，把 agent 变成能隔夜自主运行的研究程序：立题、调研、搭基建，然后假设-实验-审数-理论化循环，最后成文与复盘。

## 流程

```
question ─→ survey ─→ harness(冻结) ─→ logbook(初始化)
                                        │
          ┌─────────────────────────────▼─────────────────────┐
          │  hypothesize(预注册) ─→ experiment(一改一测)         │
          │        ▲                     │                     │
          │        │                     ▼                     │
          │  theorize(每批/平台期) ←── vet(三态裁决)             │
          └────────────────────────────────────────────────────┘
                                        │
                          report(成文) ─→ reflect(复盘)
```

[autoresearch](autoresearch/SKILL.md) 是唯一的人工入口（总控编排），其余十一个是被编排的纪律原语，也可单用。

[iteration](../iteration/SKILL.md) 不属于这条流程的任何一个阶段，它是 harness 在 Ascend NPU 上的专用实现：单 kernel 的"测量→优化"闭环。研究要优化的是 kernel 时，测量口径与消融纪律走它；研究要回答的是别的机制时，用 harness 的通用版。

## 效率模型

研究吞吐 ≈ 迭代频率 × 每轮出有价值假设的概率 × 价值 − 成本。系列的三条对策：

- **宽进严出**：hypothesize 每轮生成机制多样的候选池（`candidates.md`），出口交给实验与审数的硬标准，生成阶段不自我审查。
- **并行提效**：独立假设一假设一 worktree 并行验证；best-of-N 的胜者复跑防赢家诅咒。
- **先验累积**：每轮的已证 / 已驳沉淀为先验（`model.md`），下一轮假设从先验出发，不复交学费。

## 技能清单

| 技能 | 职责 |
| --- | --- |
| [question](question/SKILL.md) | 立题：可证伪谓词、范围、预算、停止规则 |
| [survey](survey/SKILL.md) | 调研：一手来源，带引用的四栏纪要 |
| [harness](harness/SKILL.md) | 实验基建：指标、测量、噪声底、冻结、回归门 |
| [iteration](../iteration/SKILL.md) | Ascend NPU 单 kernel 迭代：直调 bench、刷 L2、重复取中位数、逐条消融 |
| [logbook](logbook/SKILL.md) | 台账：一实验一行，预注册，跨轮记忆与恢复 |
| [hypothesize](hypothesize/SKILL.md) | 假设：机制句、候选池（宽进严出）、前提攻击 |
| [experiment](experiment/SKILL.md) | 实验：一改一测、实验即提交、崩溃政策 |
| [vet](vet/SKILL.md) | 审数：七问、三态裁决 |
| [theorize](theorize/SKILL.md) | 理论化：证据分级、最小模型、先验沉淀 |
| [report](report/SKILL.md) | 成文：证据分级报告、复现指令 |
| [reflect](reflect/SKILL.md) | 复盘：教训按最高层级编码回结构 |
| [autoresearch](autoresearch/SKILL.md) | 总控：阶段编排、自主性政策、停止谓词 |

## 系列术语

一词一义，全程复用：

- **机制句**：若机制 M 成立，则改动 D 应使指标 I 变化至少 δ（方向写明）。
- **冻结**：harness 通过灵敏度检验后锁定；改动即断代，历史结果不再可比。
- **噪声底**：同配置重复运行的实测波动，是 delta 的合格线。
- **预注册**：预测先于测量写进台账，跑完不许改。
- **三态裁决**：VERIFIED / NOT VERIFIED / INCONCLUSIVE，第三态不是 pass。
- **平台期**：连续多轮 keep 不了，触发换类别 / 组合 near-miss 协议。
- **near-miss**：差一点成立的实验轮次，组合改进的原料。
- **候选池**：滚动落盘的未测假设清单（`candidates.md`），宽进的载体。
- **先验**：已证 / 已驳沉淀出的机制模型与清单（`model.md`），下一轮假设的出发点。
- **赢家诅咒**：best-of-N 幸存者的优势含选择偏置，须单独复跑确认。

## 依赖（不随系列捆绑）

- **iteration**（本仓库顶层，与 research 并列）：harness 阶段要为 Ascend kernel 搭测量时指向它；它的直调 bench 就是 harness 所说的"一条命令出指标"的落地形态。
- **create-tool**（本仓库顶层）：harness 的测量脚本、experiment 的抽取工具、reflect 的层级 3、autoresearch 的工具化经济都指向它。造工具是通用纪律，不属于 research 域，故只引用不复制，避免同名冲突与两份漂移。
- 只装 research 系列时自带降级：各锚点句写明了工具的形状（单文件、一条命令、一行答案），步骤照常执行。
- 要完整体验，一并安装：`npx skills add https://gitcode.com/zhangzijie/skills.git -s create-tool`。
