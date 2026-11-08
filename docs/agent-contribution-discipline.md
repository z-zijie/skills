# Agent 协作的贡献纪律

本仓库把外部 Agent 的贡献当作一条需要设计的工作流，而不是靠 Review 临时裁剪：先约定范围，再让 PR 自证，最后由可信的自动化做门槛。规则分布在 `CONTRIBUTING.md`、`AGENTS.md`、`.github/` 和 `docs/`；本页说明规则的形状、关键取舍和维护方式。

## 1. 散弹式修改从哪里来

外部 Agent 的散弹式修改通常不是恶意，而是以下条件的自然结果：

1. **任务粒度错位**：人对 Agent 说"顺手修一下这个"，Agent 会把沿途看到的问题一起修掉；没有 accepted Issue 的 PR，等于没有事先约定"只做这一件事"。
2. **缺少仓库级指令**：Agent 只看到代码，看不到"本仓库不接受顺带重构、格式化、依赖升级"这类约定，默认策略是"能改就改"。
3. **局部最优**：Agent 倾向于让当前测试或演示跑通，容易改公共接口、配置、依赖来"让它工作"，而不是做最小改动。
4. **范围没有成本**：多改文件不触发任何检查、也不会被要求拆分时，Agent 没有理由克制。
5. **Reviewer 信息不足**：PR 没有 Scope、Evidence、AI 声明时，Reviewer 只能从 diff 反推意图，追问和返工随之增加。

对应的纪律：**用 Issue 约定粒度，用 AGENTS.md 给出仓库约束，用 PR 模板要求 Scope，用 CI 让越界有成本，用 AI Usage 让 Reviewer 知道该看哪里。**

## 2. 三道闸门

| 闸门 | 规则 | 执行位置 |
|---|---|---|
| **入口** | 外部贡献先有 accepted Issue；受保护路径不能直接改 | `CONTRIBUTING.md`、`.github/ISSUE_TEMPLATE/`、`.github/workflows/pr-policy.yml` |
| **自证** | 每个 PR 填 Motivation / Scope / Changes / Testing / AI Usage，带 Issue 引用、测试设计和 AI 声明 | `.github/PULL_REQUEST_TEMPLATE.md`、`docs/review-checklist.md`、`pr-policy.yml` |
| **可信门槛** | policy 从默认分支运行；CODEOWNERS 要求 owner review；RSI 改动跑完整检查 | `.github/workflows/pr-policy.yml`、`.github/CODEOWNERS`、`.github/workflows/rsi-checks.yml` |

两个可作为分支保护 required check 的名字：

- `policy`：`PR policy` workflow 的 `policy` job；所有非 draft PR 运行，检查模板段落、外部作者的 Issue 引用、AI 声明块、`Declared scope` / `Out of scope`、受保护路径和宽改提示。
- `checks passed`：`RSI checks` workflow 的聚合 job；PR 触碰 `RSI/**` 时跑完整 scaffold gates，否则快速成功。

## 3. 规则在哪里

| 文件 | 拥有什么 |
|---|---|
| `CONTRIBUTING.md` | 外部贡献入口：Issue-first、接受/不接受、AI 参与声明、证据要求、受保护路径、关闭规则 |
| `AGENTS.md` / `CLAUDE.md` | 根 agent standing orders；`CLAUDE.md` 是 `AGENTS.md` 的符号链接 |
| `RSI/AGENTS.md` | RSI 子树：冻结 evaluator、写入边界、canonical vocabulary、测试命令 |
| `skills/AGENTS.md` | skill 作者规则：触发描述、脚本验证、output contract、references |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR 自证结构 |
| `.github/ISSUE_TEMPLATE/` | Bug / Feature / Task 三型 Issue |
| `.github/CODEOWNERS` | 政策、docs、skills、RSI evaluator/schemas 的 owner review |
| `.github/workflows/pr-policy.yml` | `policy` 检查 |
| `.github/workflows/rsi-checks.yml` | `checks passed` 聚合检查 |
| `docs/review-checklist.md` | Reviewer 对照表 |
| `docs/decisions/` | 持久决策记录 |
| `docs/maintainer-setup.md` | 标签、分支保护、required checks 的手动设置 |

## 4. 关键取舍

- **外部作者 Issue 强制，owner 与 owner 的 automation account 豁免。** 前者是纪律的目标对象；后者是维护者的日常通道。模板段落、Scope 和 AI 声明对所有人都必填。
- **`policy` 从默认分支运行，只读 API。** 它用 `pull_request_target` 读 PR body 和 changed files，不 checkout/执行 PR 代码；外部 PR 因此不能通过修改 workflow 绕过检查。代价是 policy 变更要合并进默认分支才生效，这正是想要的。
- **受保护路径采用自动闸门 + CODEOWNERS 双保险。** 外部作者改 `.github/`、`AGENTS.md`、`CLAUDE.md`、`CONTRIBUTING.md`、`RSI/evaluator/`、`RSI/schemas/` 会被 `policy` 直接失败；CODEOWNERS 在任何情况下都要求 owner review。
- **`RSI checks` 是聚合 check。** RSI 路径变化跑完整检查，其余 PR 快速成功；这样 docs-only PR 也能得到同一个 required check 结果。
- **不引入加权审批、Project 生命周期、i18n 配对、per-file 100% coverage。** 这些是 deepseek-harness 在团队和 monorepo 规模下的解；本仓库是单人维护，维护成本大于收益。
- **不配置 auto-merge。** 每个 PR 由人合并；Agent（包括 `zijie-super-agent`）不得合并自己的 PR。
- **仓库设置不进 PR。** 标签和分支保护不能用代码提交；步骤在 `docs/maintainer-setup.md`。

## 5. 来源：deepseek-harness

设计参考了 `deepseek-ai/deepseek-harness`（`master` @ `fcc0ad4`，2026-11-04，MIT）。它当前不接受外部 PR；对内用以下机制让 agent 贡献可审查：

| 机制 | deepseek-harness 的做法 | 本仓库采用 |
|---|---|---|
| 入口 | 关闭外部 PR；Issue/讨论先于代码 | Issue-first，保留外部 PR |
| 指令 | 根 + 17 个子树/专题 AGENTS.md（共 18 个）；词数预算；`CLAUDE.md` 符号链接 | 根 + `RSI/` + `skills/` 三份 |
| 工作流 | 22 个仓库内 skill（pre-push、PR description、code review） | 精简版：PR 模板 + review checklist |
| PR 自证 | 四段 + Test design + Proof + AI Usage 五阶段 | 四段 + Scope + 简化 AI 声明 |
| 评审 | 2 分加权审批、所有权加成、`/delegate` | CODEOWNERS + 1 个 owner approval |
| 可信自动化 | Policy workflows 从 base 分支跑；pinned SHA | `policy` 从默认分支跑、只读 API |
| CI/证据 | 窄钩子 + 全 CI + 测试分级 + change-scope | 聚合 check；change-scope 尚未做 |
| 事实源 | Agent Notes + 文档预算 + gates | 简化版 `docs/decisions/` |
| 规模 | Project 生命周期、i18n 配对、32 个 workflow | 不采用：与单人维护规模不匹配 |

主要来源：

- `deepseek-harness:CONTRIBUTING.md`、`AGENTS.md`、`docs/AGENTS.md`
- `.agents/skills/dsh-pre-push-checks`、`dsh-pr-description`、`dsh-code-review`
- `.github/issue-management/`、`.github/review-ownership/`
- `docs/testing.md`、`docs/defensive-patterns/README.md`、`scripts/change-scope.ts`
- `.agents/notes/README.md`

## 6. 验证与维护

验证 `policy`：按 `docs/maintainer-setup.md` 开三个测试 PR——只改 `docs/` 应通过；删掉 `## Testing` 应失败；非 trusted 账号改 `.github/` 应失败。

改规则：改 `.github/`、`AGENTS.md`、`CONTRIBUTING.md` 等受保护路径的 PR 单独提，从默认分支生效，并由 owner review；不要在正在被检查的 PR 里改检查本身。

剩余缺口：

- `change-scope`：还没有列出改动路径的脚本；`policy` 目前只检查受保护路径和宽改阈值，不能严格比对"声明范围 vs 实际路径"。
- `skills/` 层：没有统一的 frontmatter/脚本自动校验，靠 CODEOWNERS + review。
- 文档 gate：没有链接/预算检查；文档量上来后再加。

观察指标：

- 无 Issue 的外部 PR 数（目标 0）。
- 外部 PR 平均变更文件数 / 触碰的顶层目录数。
- Reviewer 为搞清楚 scope/evidence 而追问的次数。
- 同一 PR 的返工轮次。
- `policy` / `checks passed` 的失败原因分布。

## 7. 参考

- 本仓库：`CONTRIBUTING.md`、`AGENTS.md`、`.github/`、`docs/review-checklist.md`、`docs/maintainer-setup.md`、`docs/decisions/2026-11-08-agent-contribution-discipline.md`。
- deepseek-harness：`deepseek-ai/deepseek-harness`，`master` @ `fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`（2026-11-04，MIT）。
