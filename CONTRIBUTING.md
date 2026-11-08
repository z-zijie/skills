# 贡献指南

本仓库接受外部贡献，但**贡献从 Issue 开始，而不是从 Pull Request 开始**。

## 先提 Issue

- 任何代码或文档改动，请先在 Issues 用对应模板提交，并写清：要解决的问题、建议的改动范围、你打算如何验证。
- 维护者会在 Issue 中回复「接受 / 不接受 / 需要更多信息」。**没有获得接受的 PR 会被关闭**，并附上本指南链接。
- 小修小补（typo、失效链接）也请先开 Issue，或确保一个 PR 只做这一类改动。

## 我们接受什么

- 修复明确 bug，且带复现与回归测试。
- 新增或改进 skill：符合 `skills/writing-for-agents/SKILL.md` 的结构要求，带触发描述和自测用例。
- RSI：符合 `RSI/docs/permissions.md` 的写入边界；修改 `RSI/evaluator/` 的 PR 默认不接受。
- 文档：一个 PR 只改一个主题，不做顺带重排或格式化。

## 我们不接受什么

- 无 Issue，或 Issue 未被接受的 PR。
- 一个 PR 混多个无关改动（重构 + 功能 + 格式化 + 依赖升级）。
- 纯风格改写、大规模重命名、没有行为理由的目录搬迁。
- 把 CI/政策文件（`.github/`、`AGENTS.md`、`CONTRIBUTING.md`）的修改和业务改动混在同一 PR。
- 无法复现的「修复」、没有证据的性能优化。

## AI 参与声明

使用 AI/Agent 提交的 PR，必须在 PR 描述的 `## AI Usage` 中如实填写每个阶段的参与情况：问题拆解（breakdown）、方案设计（design）、验收测试（testing）、技术实现（implementation）、评审处理（review）。

| 级别 | 含义 | AI 参与度 |
|---|---|---|
| `hint` 人工独立 | 人做，AI 只给被动建议 | 0–10% |
| `assist` 人工主导 | 人给主要内容或关键决策，AI 完成部分 | 15–25% |
| `pair` 人工引导 | 人给关键方向或约束并多次修订，理解内部机制 | 30–50% |
| `copilot` 人工把关 | AI 完成整个任务，人只审查、选择或否决 | 55–85% |
| `auto` 模型自主 | AI 自主完成，人只设目标或接受结果 | 90–100% |

- 全局级别取各阶段中 AI 参与度最高的级别（忽略 `n/a`）。
- 人类贡献者仍然对提交内容负责；「Agent 写的」不是免责理由。
- AI 全自主提交可以接受，但 design/implementation 为 `auto` 的 PR 会被更严格地审查。

## 证据要求

- 行为修复：修复前失败、修复后通过的回归测试。
- 性能：变更前后数字 + 测量条件 + 可复现命令。
- 文档：链接检查和渲染检查说明。
- RSI：`cd RSI && python3 tests/run_all.py`，以及对应 skill 的回归用例。

## PR 要求

- 使用 `.github/PULL_REQUEST_TEMPLATE.md`，填完所有段落；`PR policy` 检查会验证：
  - `## Motivation` / `## Scope` / `## Changes` / `## Testing` / `## AI Usage` 是否齐全；
  - 是否链接 Issue（外部作者必填）；
  - 是否声明 `Declared scope` / `Out of scope`；
  - AI 声明块是否填写。
- **一个 PR 只做一件事**。声明范围之外的改动会被要求拆分。
- 外部作者的 PR 如果修改受保护路径（见下），会被 `PR policy` 直接拦下。
- 改动 RSI 的 PR 还需通过 `RSI checks`；两个检查都通过后才会进入 review。

## 受保护路径

以下路径的改动会影响仓库的规则或测量基线，外部 PR 不能直接修改：

- `.github/`（CI、政策、模板）
- `AGENTS.md`、`CLAUDE.md`、`CONTRIBUTING.md`
- `RSI/evaluator/`（冻结的评估器）
- `RSI/schemas/`（冻结的契约）

如果你认为必须修改这些路径，先在 Issue 中说明原因和验证方式；维护者会要求单独开 PR 并亲自 review。

## Review

- Reviewer 按 `.github/CODEOWNERS` 指定；没有 owner 的区域默认由维护者 review。
- PR 描述不完整（缺 Issue、缺测试、缺 AI Usage）会被打回，补齐后再 review。
- Review 只处理当前 diff；不要在同一 PR 里继续扩大范围。
- Review checklist 见 [`docs/review-checklist.md`](docs/review-checklist.md)。

## 关闭规则

- 30 天无响应，或与 Issue 结论不符的 PR 会被关闭；可以基于新 Issue 重新提交。
- 无 accepted Issue、或包含超出单一主题改动的 PR，会被直接关闭并附上本指南链接。

## 维护者设置

标签、分支保护、required checks 等仓库设置不能由代码提交完成，步骤见 [`docs/maintainer-setup.md`](docs/maintainer-setup.md)。
