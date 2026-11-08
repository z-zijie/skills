<!-- 按 CONTRIBUTING.md 和 docs/review-checklist.md 填写；不完整的描述会被 PR policy 检查拦下。 -->

## Motivation

<!-- 链接同仓库 Issue：Fixes #N（解决问题）或 Refs #N（提供上下文）。外部作者的 PR 必须有被接受的 Issue。 -->

## Scope

- **Declared scope**（预期改动的路径/模块）：
- **Out of scope**（明确不做的事）：

## Changes

- Interface changes（命令/配置/公共接口；没有写 None）：
- Behavior changes（用户/Agent 可观察行为；没有写 None）：

## Testing

- Test design（为什么这些测试覆盖了上面的 Changes）：
- Test entries（每条：命令 + 结果）：

<details>
<summary>Proof</summary>

</details>

## AI Usage

<!-- 级别定义见 CONTRIBUTING.md 的「AI 参与声明」；全局级别取最高级。 -->
<!-- ai-declaration:start -->
**AI declaration**: `待填级别`（hint / assist / pair / copilot / auto）
<!-- ai-declaration:end -->

| 阶段 | 级别 | AI 参与度 | 决策记录 |
|:--|:--|--:|:--|
| breakdown 问题拆解 |  |  |  |
| design 方案设计 |  |  |  |
| testing 验收测试 |  |  |  |
| implementation 技术实现 |  |  |  |
| review 评审处理 | n/a 暂不适用 | — | 尚无已处理的评审意见。 |

## Checklist

- [ ] 已链接被接受的 Issue（外部作者必填）
- [ ] 一个 PR 只做一件事，变更路径没有超出 Declared scope
- [ ] 测试/证据已填写，且命令实际运行过
- [ ] 未修改受保护路径（`.github/`、`AGENTS.md`、`CLAUDE.md`、`CONTRIBUTING.md`、`RSI/evaluator/`、`RSI/schemas/`），或已按 CONTRIBUTING 走单独 Issue
- [ ] AI Usage 已如实填写
- [ ] 本地相关检查已通过
