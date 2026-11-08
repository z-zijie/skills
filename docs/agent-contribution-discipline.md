# Agent 协作的贡献纪律：deepseek-harness 调研与本仓库落地方案

> **调研对象**：`deepseek-ai/deepseek-harness`，`master` @ `fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`（2026-11-04，MIT）。
> **调研时间**：2026-11-08。
> **目的**：整理该仓库的贡献规则中与 Agent 协作有关的部分，回答两个问题：外部贡献者的 Agent 为什么会散弹式修改？怎样把这类修改挡在 Review 之前？并给出可直接在 `z-zijie/skills` 落地的方案。
> **引用约定**：文中 `deepseek-harness:<path>` 指该仓库内路径；所有结论基于上述快照，上游规则可能继续变化。

## 0. 先看结论

deepseek-harness 的做法可以压缩成一句话：**先管入口，再让 PR 自证，最后用可信的自动化做 Review 门槛；指令本身分层、按需加载。**

三个最值得先抄的机制：

| 机制 | 直接效果 | 本仓库是否值得立刻采用 |
|---|---|---|
| **入口管控**：外部贡献从 Issue 开始；没有 accepted Issue 的 PR 直接关闭（deepseek-harness 当前甚至完全不接受外部 PR） | 散弹式 PR 不进入 Review 队列，维护者不必先读代码再判断"想干什么" | **是**。这是性价比最高的动作 |
| **PR 自证**：模板 + 仓库内 skill 规定 Motivation / Changes / Testing / AI Usage，要求 Issue 链接、测试设计、证据和 AI 参与度申报（本仓库建议再加一个 Scope 段） | Reviewer 不用追问"改了什么、为什么、测了什么、谁做的决策" | **是** |
| **可信自动化 + 路径所有权**：策略检查从默认分支运行，PR 不能修改自己的规则；关键路径必须由 owner review | 外部 Agent 无法通过改 CI 来让自己的 PR 过关；关键区域不会被陌生 Agent 顺带改掉 | **部分采用**（先做 CODEOWNERS + 分支保护，再做策略工作流） |

三条判断，供取舍：

1. **deepseek-harness 是 monorepo + 内部团队**，它的规则密度、32 个 workflow、加权审批体系不适合原样搬进个人仓库。本报告第 7 节列出不建议照搬的部分。
2. **规则要能被 Agent 读到**：外部 Agent 不会读你的 wiki 或聊天记录，但会读根目录 `AGENTS.md`、`CLAUDE.md`、PR 模板、Issue 模板和 CI 失败信息。纪律要写在这些位置。
3. **规则要能被机器执行**：只写在文档里的规则是建议，会随 Agent 的发挥而失效；能在 PR 上自动失败的规则才是纪律。

本文建议的落地顺序是 **v0（一天）→ v1（一周）→ v2（多人后再考虑）**，见第 5 节。

## 1. 调研范围与方法

- 克隆方式：`git clone --depth 1 https://github.com/deepseek-ai/deepseek-harness.git`；快照 20,665 个文件。
- 读取了贡献流程相关的全部关键文件，按层归类：
  - 入口：`deepseek-harness:CONTRIBUTING.md`、`CONTRIBUTING.zh.md`、`.github/ISSUE_TEMPLATE/*`、`.github/issue-management/{README.md,policy.mjs,rules.mjs,config.json}`、`.github/workflows/issue-policy.yml`、`.github/workflows/issue-lifecycle.yml`。
  - 指令：根 `AGENTS.md`（`CLAUDE.md` 为符号链接）、18 个 `AGENTS.md`（根 + 子树/专题，另有快照夹具内的测试专用文件）、`deepseek-harness:.agents/skills/` 下 22 个 skill。
  - 工作流：`.agents/skills/dsh-pre-push-checks`、`dsh-pr-description`、`dsh-code-review`、`dsh-ci-test-reliability`、`dsh-client-ui-ux`、`dsh-doc`、`dsh-error-handling`、`dsh-prose-standard`、`dsh-archive-agent-notes`。
  - 决策记录：`deepseek-harness:.agents/notes/README.md`、`AGENTS.md`、`implemented/AGENTS.md`。
  - PR：`.github/pull_request_template.md`、`.github/review-ownership/{README.md,approval-policy.json}`。
  - 自动化：32 个 workflow，重点读 `ci.yml`、`ci-windows.yml`、`ci-python.yml`、`ci-master.yml`、`issue-policy.yml`、`pr-check-on-head-branch.yml`、`pr-check-after-approve.yml`、`pr-check-on-base-branch.yml`、`weighted-approval.yml`、`expected-filenames.yml`。
  - 证据与文档：`deepseek-harness:docs/testing.md`、`docs/development.md`、`docs/AGENTS.md`、`docs/defensive-patterns/README.md`、`scripts/change-scope.ts`、`lefthook.yml`、`Makefile`。
- 未做的事：没有运行该仓库的测试（本任务只做规则整理）；没有读取私有的 Discussions/Project 数据；没有评估该仓库的代码质量；没有读取外部 Action `deepseek-harness/deepseek-harness-check` 的实现，因此 Policy 检查的具体判定逻辑以本报告描述为边界。

## 2. deepseek-harness 的贡献纪律全景

在整理规则之前先诊断：**外部 Agent 的散弹式修改通常不是恶意，而是几个条件的自然结果。**

1. **任务粒度错位**：人对 Agent 说"顺手修一下这个"，Agent 会把沿途看到的问题一起修掉；没有 accepted Issue 的 PR，等于没有事先约定"只做这一件事"。
2. **缺少仓库级指令**：Agent 只看到代码，看不到"本仓库不接受顺带重构、格式化、依赖升级"这类约定，默认策略是"能改就改"。
3. **局部最优**：Agent 倾向于让当前测试或演示跑通，容易改公共接口、配置、依赖来"让它工作"，而不是做最小改动。
4. **范围没有成本**：多改文件不会触发任何检查、也不会被要求拆分时，Agent 没有理由克制。
5. **Reviewer 信息不足**：PR 没有 Scope、Evidence、AI 声明时，Reviewer 只能从 diff 反推意图，追问和返工随之增加，Review 吞吐进一步下降。

对应的纪律正好是：**用 Issue 约定粒度，用 AGENTS.md 给出仓库约束，用 PR 模板要求 Scope，用 CI 让越界有成本，用 AI Usage 让 Reviewer 知道该看哪里。** 下面按"一个外部改动从出现到被合并"的顺序，分七层看 deepseek-harness 怎么做的。

### 2.1 入口：外部贡献从 Issue 开始，不从 PR 开始

- `CONTRIBUTING.md` 明确写着：**当前无法接受外部 Pull Request**。外部参与渠道是 GitHub Discussions、生态插件（`dsh-plugin` topic）、博客/教程和答疑。中英文版本通过 `.i18n.yaml` 记录配对，并有翻译配对 gate。
- 这不是"懒"，而是一种入口策略：在维护团队很小的阶段，**把贡献分成"可接收的输入"和"不可接收的代码"**，避免用 Review 时间来替外部 Agent 做范围裁剪。
- 对内，Issue 是 PR 的前置条件：
  - **Issue 模板**只有三种：Bug（Summary / Reproduction / Current behavior / Expected behavior / Environment）、Feature（Motivation / Behavior）、Task（Summary / Deliverables）。字段少而具体，强制写清"问题"和"预期"。
  - **`Issue policy` 是 required check**（`issue-policy.yml`）：非 draft、人类作者、且有 review request 或已提交 review 的 PR，必须：
    - 至少引用一个**同仓库 Issue**：`Fixes #N` / `Closes #N` / `Resolves #N` / `Refs #N` 都行，但 HTML 注释、代码块、行内代码里的引用不算，PR 编号也不算；
    - 恰好一个 `kind/*` 标签；
    - 至少一个 `area/*` 标签；
    - 最多一个 `p0`–`p3` 标签；
    - 使用 resolving 关键字时，PR 的 Priority 要与被解决 Issue 的最高 Priority 一致；
    - Bot/App 作者的 PR 走豁免路径。
  - `issue-lifecycle.yml` 用 PR 事件驱动 Project 状态（`In progress` / `In review`）并写审计评论，但这是维护团队的内部看板自动化，不是外部贡献者必须跨过的门槛。
- 这一层的效果：**每个进入 Review 的 PR 都有可追溯的动机、类型和优先级**。Reviewer 不需要在 PR 里反推"这个 Agent 为什么改这些文件"。

### 2.2 指令层：AGENTS.md 是单一真源，并且分层

- 根 `AGENTS.md` 是 agent 的 standing orders：仓库布局、常用命令、本地检查策略、编码约定、文档规则、"Editing these instructions"。`CLAUDE.md` 是它的符号链接，避免两份真源。不同 Agent 工具的入口文件名可能不同；用符号链接或薄封装保持单一真源，不要复制成多份。
- 规模控制：根 `AGENTS.md` **2,069 词**，而 `docs/AGENTS.md` 给它定的预算上限是 **2,080 词**；子树 `AGENTS.md` 预算通常 ≤600 词（`packages/AGENTS.md` ≤810，`docs/AGENTS.md` ≤1,430）。根文件不是"把所有规则塞进去"，而是"只放每个会话都必须知道的规则，其余链接到 home"。
- 子树 `AGENTS.md` 按目录拆分：`docs/AGENTS.md`（文档标准）、`packages/AGENTS.md`（包作者规则）、`.github/AGENTS.md`（CI/Workflow 约定）、`scripts/AGENTS.md`、`snapshots/AGENTS.md`、`vendor/AGENTS.md`、`website/AGENTS.md`、`packages/client/AGENTS.md` 等，共 18 个（不含快照夹具里作为测试输入的 `AGENTS.md`）。
- `docs/AGENTS.md` 的 tier taxonomy 是这套体系的核心：**每个事实只有一个 home**。

| 层 | 只放什么 | 不放什么 |
|---|---|---|
| 根 `AGENTS.md` | 每个会话都要知道的 standing orders（1–3 行一条，链接到 home） | 故事、示例、情境流程、别处已写的规则 |
| 子树 `AGENTS.md` | 该子树特有的规则 | 根文件已覆盖的全局规则 |
| `docs/architecture.md` | 有序的系统地图 | 类型定义、包细节、决策理由、实现状态 |
| `docs/subsystems/` | 类型定义、语义、生成的 API 参考 | 行为叙述 |
| `.agents/notes/` | 决策记录：为什么、放弃了什么、需要什么验证 | 迁移计划、验收清单、实现走查 |
| `docs/defensive-patterns/` | Review 中强制执行的架构规则 | 类型定义、决策理由 |
| `docs/cookbook/` | 编号步骤的操作指南 | 设计理由（链接到 Agent Note） |
| 包 README | 该包的契约、限制、扩展点 | JSDoc/生成目录的复述 |
| `.agents/skills/` | 可复用的工作流和专门判断标准 | 产品/运行时契约 |

- 这个分层的实际作用：**Agent 只需要读它当前所在目录最近的那份 `AGENTS.md`**，不会在根文件里淹死；维护者改规则时知道该改哪一份，不会出现同一规则在三处漂移。
- 旁证：快照用例目录里还有 `agent-instructions-budget` 之类的测试夹具，说明这套指令体系本身有回归测试。

### 2.3 工作流层：把"该怎么改"写成可加载的 skill

deepseek-harness 把 agent 的操作流程做成仓库内的 22 个 skill（`.agents/skills/`），按需加载。与贡献纪律直接相关的四个：

| Skill | 管什么 | 关键要求 |
|---|---|---|
| `dsh-pre-push-checks` | push 前做什么 | 先 `git status` + `change-scope --base` 看完整改动面；按 surface 选**最小**的相关检查；文档 → `doc-sync`，UI → UI workflow，包行为 → owning test；**不要**默认跑全量；报告只写实际跑过的命令；force push 只用 `--force-with-lease`；push 后核对 remote ref 和 `gh pr checks` |
| `dsh-pr-description` | PR 描述怎么写 | 四段固定结构；Motivation 必须链接 Issue；Changes 分 interface / behavior；Testing 先写测试设计再列测试条目和 Proof；附 change-specific disclosures；AI Usage 五阶段表 |
| `dsh-code-review` | Reviewer 怎么审 | 先读 sources of truth（AGENTS.md、defensive patterns、relevant skills），再用 `change-scope` 确认 diff 范围，然后再读 diff；区分 blocker 和 suggestion；"一个被证实的 blocker 胜过一串 nit" |
| `dsh-archive-agent-notes` | 决策记录怎么归档 | 归档前检查完整性、链接、影子文件；归档后永久冻结 |

`.agents/skills/agent-experience` 虽然更偏模型侧产品，但它表达的同一条原则适用于仓库内的所有 skill：**默认最小上下文，按需展开，关键约束前置，输出有界**。这也是"为什么 skill 不塞进根 AGENTS.md"的答案。

### 2.4 PR 自证层：模板 + AI Usage，让 Reviewer 不用自己拼图

`.github/pull_request_template.md` 与 `dsh-pr-description` skill 共同规定 PR 描述必须有四段：

1. **`## Motivation`**：一句话说清问题；至少引用一个同仓库 Issue（`Fixes/Closes/Resolves` 或 `Refs`）。
2. **`## Changes`**：
   - Interface changes：命令、flag、配置键、环境变量、公共导出、协议、持久化格式；没有就写 `None`；
   - Behavior changes：用户/模型/开发者/系统可观察的行为，包括默认值、性能、时序、输出；没有就写 `None`；
   - 只写 **diff 能保证、测试能覆盖** 的行为。
3. **`## Testing`**：
   - **Test design**：按变更类型选择矩阵——行为变更用"场景 × 变更前 × 变更后 × 覆盖测试"；性能变更用测量条件（机器、次数、取值方式）；无行为变更的重构/迁移用逐字节对比、未变快照或新旧对比；纯文档用链接/双语/示例编译检查；
   - **Test entries**：每条测试一行，写命令或步骤、覆盖的行为、实际结果；明确是本地还是 CI；
   - 可审查的证据放进该条目的 `<details><summary>Proof</summary>` 块。
4. **`## AI Usage`**：五阶段（breakdown / design / testing / implementation / review）表格，填级别、AI 参与度、决策记录；顶部是 AI-DECLARATION 徽章块。

AI Usage 的级别与含义（来自 `dsh-pr-description`，规范版本 0.1.2）：

| 级别 | 含义 | AI 参与度 |
|---|---|---|
| `hint 人工独立` | 人做，AI 只给被动建议 | 0–10% |
| `assist 人工主导` | 人给主要内容或每个关键决策，AI 完成部分 | 15–25% |
| `pair 人工引导` | 人给关键方向或约束，并多次修订 AI 输出；人理解内部机制 | 30–50% |
| `copilot 人工把关` | AI 完成整个任务，人只做审查/选择/否决和关键澄清，不提供实质内容 | 55–85% |
| `auto 模型自主` | AI 自主完成，人只设目标或接受结果 | 90–100% |
| `n/a 暂不适用` | 该阶段不存在或尚未发生 | — |

此外还有几条关键的填写规则：

- 全局徽章级别 = 各阶段中 AI 参与度最高的级别（忽略 `n/a`）。
- 评分依据是 session/task 记录：人类在 subagent 会话里发的消息算人类参与；Agent 写给 subagent 的指令不算。
- 只统计最终进入这个 PR 的工作；人类要求回退/重做也算参与。
- breakdown/design/testing/implementation 阶段的"人类"是作者；review 阶段只统计**已处理**的评审意见；只处理了措辞类意见可以按类别处理。
- 流程性指令（分支、工作顺序、何时 commit/push、何时开 PR）不算任何阶段的人类参与。
- "review 评审处理"行在还没有处理任何评论时保持 `n/a`，即使有评论或评论在 merge 后到达。

**重要限定**：在仓库内可见的实现中，AI Usage 由 PR 模板和 skill 约束；`.github/issue-management/policy.test.mjs` 会检查模板与 skill 是否符合 AI-DECLARATION 规范版本，但 **PR 正文本身没有可见的 CI 校验**。这是"声明 + 社会约束"，不是机器强制。对本仓库来说，这反而是一个可以做得更轻或更严的选择点。

### 2.5 评审层：谁必须看、什么算过

deepseek-harness 没有用"必须 N 个 approve"这种朴素规则，而是用一套**加权审批**（`.github/review-ownership/README.md` + `weighted-approval.yml`）：

- 门槛是 **2 个审批点**（`approval-policy.json` 的 `requiredPoints`）。
- 有 write/admin 权限的 reviewer 才计数；`@07akioni`、`@imccyu`、`@tianyicui`、`@tianyicui-bot`、`@turtle1999`、`@turtle2099` 各 2 点，其他有写权限的 reviewer 默认 1 点。
- PR 作者自己 review 不算；没有 write 权限的人 review 不算。
- 作者信用：`min(100, 合并 PR 数) × 11 / 1000`，最多 1.1 点；只能作为补充，**不能单独满足 2 点**；bot 和人类作者同一规则。
- 所有权加成：如果 reviewer 通过 `git blame` 在 base/head 的 merge base 上拥有被修改/删除的**生产代码行**，其 1 点审批会按 `min(2, 1 + 4 × ownedLines / totalLines)` 加权（0% 为 1 点，12.5% 为 1.5 点，25% 及以上为 2 点）。
- `CHANGES_REQUESTED`（有写权限的 reviewer）和 draft 状态都会让状态保持 pending；分数够也不能过。
- `/delegate @username` 可以把自己的审批点委托给另一个有写权限的人；委托不改变"谁负责"。
- 策略变更只有在**合并进默认分支之后**才生效——防止一个不可信的 PR 改自己的审批规则。
- 安全实现：publisher 从默认分支 checkout，pinned SHA；不 checkout/执行 PR 代码；用专用 GitHub App 发布 check run；review-event workflow 无 `GITHUB_TOKEN` 权限，只把 PR 编号作为 run title 传过去；PR 评审和评论只当 API 数据读取并转义输出。

配套的是 **`Policy` 系列 workflow**（`.github/AGENTS.md` 有权威描述）：

| Check | 管什么 |
|---|---|
| `Policy / Master Change` | PR 相对 master/release 的 diff 检查 |
| `Policy / Pre-Merge Check` | 合并 base 后安装/构建，验证合并结果 |
| `Policy / Approve Check` | 审批状态检查 |
| `Policy / Reviewer Check` | 哪些文件需要谁 review 的独立 check run |
| `Policy / Recheck` | base 分支 push 后重跑 head 检查 |

这些 job 都从 **base 分支**的代码运行（`pull_request_target`），每个 job 用 `MERGE_POLICY_MASTER_DIFF` / `MERGE_POLICY_PRE_MERGE` / `MERGE_POLICY_HEAD_DIFF` 变量控制 `off` / `report` / `block`；分支保护要求的是这些 check 名。这样外部 PR **无法通过修改 workflow 来绕过检查**，因为检查代码来自默认分支。

### 2.6 CI / 证据层：窄钩子 + 全 CI + 证据分级

- **本地钩子故意很窄**（`lefthook.yml`）：
  - `pre-commit`：翻译配对（staged）、archived notes 校验、staged lint 自动修复、空白检查、pi-ai provider 元数据、vendor manifest guard；
  - `pre-merge-commit`：同一套 staged 配对检查与 archived notes 校验；
  - `pre-push`：只跑 `pnpm run typecheck`。
  - `docs/development.md` 明确：钩子**故意不跑**测试、快照、文档检查、构建和 hygiene；贡献者自己跑一次与改动相关的检查；CI 拥有 exhaustive coverage、built-artifact smoke 和 Node 兼容矩阵。
- **CI 分层**：`ci.yml` 是 PR-only 的大杂烩（static、coverage、lint、benchmarks、snapshots+artifacts、Node 兼容等）；Windows 在 `ci-windows.yml`、Python 在 `ci-python.yml`，各自有 verdict job：`all checks passed`、`windows checks passed`、`python checks passed`；`ci-master.yml` 只在 master push 上跑 master-only 的平台/自托管检查。workflow 数量 32 个，但职责分离得很清楚：PR 只跑 PR 需要的，master 只跑 master 需要的。
- **测试策略**（`docs/testing.md`）：
  - 分层：unit / coverage gate / real-API e2e / owner-local expected / benchmark / snapshot / web browser snapshot；
  - coverage gate 是 `test:coverage`（不是 `test`），对 `packages/*/*/src` 做 per-file 100%；
  - 三条核心口号：**Verify the world, not the self-report**（外部再读一次文件/重跑命令，别信 Agent 自述）、**Test the real entry path**（走 Loader/bin/worker/subprocess，不要手搭 `ctx.plugin`）、**negative controls**（每个新增的验收规则都要证明一个故意无效的 case 会失败）；
  - 非平凡的 model/user-visible 变更必须在同一 PR 更新 keyless recorded-session snapshot；
  - 本地选择和 "why not full suite" 写在 `dsh-pre-push-checks` 里。
- **`scripts/change-scope.ts`**：输出以 merge base 为基准的 committed/staged/unstaged/untracked 路径清单，以及解析出的 base/head/merge-base SHA。pre-push skill 和 code review skill 都先跑它，再决定检查范围和 review 范围。它是"改动面 = 评审面"这个原则的机械化支撑。

### 2.7 事实源层：决策记录与文档标准

- **Agent Notes**（`.agents/notes/`）：记录影响代码库的决策——为什么、放弃了什么、代码和文档装不下的部分。
  - 路径编码生命周期和类型：`{proposed|implemented|rejected|archived}/{feature|bug-fix|simplification|architecture|process|testing}/yyyy-mm-dd-topic.md`；
  - `proposed/` 写 Problem / Proposal / Alternatives considered / Acceptance criteria / Risks；`implemented/` 写 Problem / Decision / Alternatives considered / Consequences；
  - "Alternatives considered" 是**强制**的：没有记录败选方案的决定会招来反复争论；
  - 只在有持久决策理由时写；机械/局部修改豁免（包括局部 UI 修改）；
  - `implemented/` 的 note 要随代码演进而更新事实（路径、名字、结构），但归档后永久冻结，不再作为当前权威；
  - 有 `verify-agent-note-format` / `verify-archived-agent-notes` 等 gate 检查格式和冻结内容。
- **文档标准**（`docs/AGENTS.md`）：每个事实一个 home；当前状态用现在时；一段一行；文档预算；slop checklist（重复规则、历史错位、实现状态标注、手抄目录、推理过程、段落墙、强调通胀、spec-speak 等）；`verify-doc-budgets` / `verify-md-wrap` / `verify-md-links` / `doc-typecheck` 等 gate 保证文档不漂。
- **Defensive patterns**（`docs/defensive-patterns/README.md`）：review 中强制执行的架构规则，按 artifact 分页；每条规则写清"必须成立什么、reviewer 用什么条件判断、边界和例外由谁批准、正例和反例"。其中一条直接对应用户的痛点：

> **Keep the change face equal to the review face.** A change carries only the lines its stated behavior change needs, a migration moves code rather than rewriting it, and a performance-sensitive change carries its measurement.

翻译：**改动面必须等于评审面**——一个改动只携带它声称的行为变更所需的行；迁移是搬代码而不是重写；性能敏感改动自带测量。

## 3. 机制 × 痛点映射

| deepseek-harness 机制 | 解决什么 | 对 Review 负担的影响 |
|---|---|---|
| 不接受外部 PR / Issue-first | 无范围的代码不进入队列 | Reviewer 只处理已被接受的动机 |
| 必需 Issue 引用 + `kind/area/priority` 标签 | PR 动机和分类可追溯，可路由 | 不用在 PR 里问"这是 bug 还是 feature" |
| AGENTS.md 分层 + 预算 | Agent 拿到的是"当前目录的规则"而不是一锅粥 | 减少"Agent 不知道本仓库约定"的低级返工 |
| 仓库内 skills（pre-push / PR description / code review） | 把"该怎么改、怎么测、怎么写 PR、怎么审"变成可加载流程 | Reviewer 面对的是格式一致、信息完整的 PR |
| PR 四段式 + Test design + Proof | PR 自证：问题、范围、变更、证据 | 不用追着作者要测试和复现 |
| AI Usage 五阶段申报 | 知道哪个阶段是 Agent 自主决策 | 把审查注意力放在高风险阶段，而不是平均用力 |
| change-scope + "改动面 = 评审面" | 范围可见、可检查 | Reviewer 能先看面再读 diff，发现夹带 |
| 路径所有权加权审批 | 关键区域由懂的人 review | 避免"随便一个 approve 就过" |
| 可信 Policy workflow | PR 改不了自己的规则 | 防止"改 CI 通过自己"的作弊类 PR |
| 窄钩子 + 全 CI + 证据分级 | 本地快、CI 全、证据按面选择 | 减少本地/CI 重复劳动，也减少"没跑测试"的争论 |
| Agent Notes + 文档 gate | 决策理由写一次；文档不膨胀 | 不用反复重开设计讨论；文档 review 有边界 |
| 标签 + Issue 生命周期 | 状态机可读 | 维护者不用手工做看板 |

## 4. 本仓库现状与差距

### 4.1 现状

- 仓库：`z-zijie/skills`，public，默认分支 `main`；0 star / 0 fork；内容为 `skills/`（8 个可安装 skill）+ `RSI/`（自改进 kernel 优化脚手架）+ `.github/workflows/rsi-checks.yml`。
- README 是中文，安装方式为 `npx skills add https://github.com/z-zijie/skills.git`；`skills/` 里已有 `writing-for-agents`，说明对"给 Agent 写文档"本身有方法论。
- PR 历史基本由 `zijie-super-agent` / `z-zijie` 提交，尚无外部贡献流量；CI 只对 `RSI/**` 和相关 workflow 变更触发（`rsi-checks.yml`）。
- `RSI/` 内部已经有很强的纪律：`RSI/docs/permissions.md` 规定谁能写什么并代码化执行；`RSI/docs/guarantees.md`、`verification.md`、`anti-cheating.md` 把可验证性和反作弊写清楚；`RSI/docs/AUTHORING.md` 是 skill/playbook 的写作约束；CI 每次改动跑契约、统计、状态机、redteam、reference backend demo。
- 缺口不在"内部工程纪律"，而在**仓库层的贡献入口**：没有 `CONTRIBUTING.md`、没有根 `AGENTS.md`、没有 PR 模板、没有 Issue 模板、没有 CODEOWNERS、没有标签体系、没有 PR 政策检查。外部 Agent 进入仓库时拿不到任何"该怎么贡献"的指令。

### 4.2 已有对应物：RSI 已经解决了什么

本仓库不是从零开始。`RSI/` 内部已经有一套与 deepseek-harness 同构的纪律，缺的是把它们提升到仓库层，并覆盖 `skills/` 和外部贡献：

| deepseek-harness 机制 | 本仓库已有对应物 | 还需要补什么 |
|---|---|---|
| 冻结 evaluator / 可信策略 | `RSI/docs/permissions.md` 的写入边界；evaluator 只读；CI 每次跑契约与 redteam | 把边界提升到仓库层（CODEOWNERS + 分支保护），让外部 PR 也受约束 |
| 证据分级 / negative controls | `RSI/docs/verification.md`、`anti-cheating.md`；`tests/run_all.py`；redteam | 把"真实入口/反例/可复现"也写进 skills 层的贡献要求 |
| Agent Notes / 决策记录 | RSI 的 `docs/{guarantees,closures,disagreement-log,...}.md` 和 `_src/rsi-build-plan.md` | 建仓库级 `docs/decisions/`，供非 RSI 改动使用 |
| AGENTS.md 分层 | `RSI/docs/AUTHORING.md` + `RSI/docs/README.md` 的"该读哪份"索引 | 根 `AGENTS.md`、`RSI/AGENTS.md`、`skills/AGENTS.md` 三份入口 |
| 窄钩子 + 全 CI | `rsi-checks.yml` 覆盖 RSI 契约、统计、状态机、redteam | PR 模板/政策检查 + 聚合 check；skills 层的最小验证命令 |

### 4.3 差距矩阵

| 层 | deepseek-harness | 本仓库现状 | 缺口 |
|---|---|---|---|
| 入口 | 不收外部 PR；Issue 模板 + Issue policy | 无 Issue 模板、无政策 | 外部 Agent 可以直接开 PR |
| 指令 | 根 + 17 个子树/专题 AGENTS.md（共 18 个）；22 个 skill | 仅 RSI 内部文档 | 仓库根 / skills/ 没有 Agent 指令 |
| PR 自证 | 模板 + description skill + AI Usage | 无模板 | Reviewer 要自己拼图 |
| 评审 | 加权审批 + Policy checks | 无 CODEOWNERS、无 required checks | 谁都能改任何路径 |
| 自动化 | 32 个 workflow；可信 base-branch 策略 | 1 个 RSI workflow | 没有 PR 政策检查 |
| 证据 | 分层测试 + snapshot + negative controls | RSI 有；skills 层没有统一要求 | 外部 skill 修改缺证据门槛 |
| 决策记录 | Agent Notes + 格式 gate | RSI 有 docs，仓库级无位置 | 外部改动没有"决策写在哪"的答案 |

### 4.4 本仓库的特殊风险

1. **`skills/` 本身是可执行策略**：一个 skill 的 `SKILL.md` 会改变后续 Agent 的行为。外部 Agent 改 skill 等于改产品行为，不能当普通文档改动 review。
2. **`RSI/evaluator/` 是冻结层**：`RSI/docs/permissions.md` 明确 evaluator 只能调 CLI，不能改；外部 PR 如果碰 evaluator，整个测量基线都要重跑。这条边界应在仓库层再声明一次，并让 CODEOWNERS/分支保护兜底。
3. **CI workflow 是政策**：如果外部 PR 可以改 `.github/workflows/` 并让它在新分支上生效，就等于让被审查者改考试规则。deepseek-harness 的解法是"策略代码从 base 分支跑"；本仓库至少要做到"改 `.github/` 的 PR 必须单独 + owner 批准"。
4. **单人维护带宽**：不能引入需要每天手工维护的看板/Project 自动化；优先选"一次配置、长期生效"的机制。

## 5. 建议落地：三档路线

### 5.1 v0：一天内能上完（入口 + 指令 + 模板 + 所有权）

目标：**让外部 Agent 在动手之前先看到规则，让明显的散弹式 PR 在进入 Review 前被挡住。**

1. **`CONTRIBUTING.md`**：写清接受/不接受什么、Issue-first、一个 PR 只做一件事、AI 参与声明、证据要求、Review 与关闭规则。草案见 6.1。
2. **根 `AGENTS.md` + `CLAUDE.md` 符号链接**：standing orders，控制在 ~150 行内。草案见 6.2。
3. **子树 `AGENTS.md`**：`RSI/AGENTS.md`（指向 RSI docs、冻结层、测试命令）和 `skills/AGENTS.md`（skill 作者规则：frontmatter、触发描述、references、测试）。草案见 6.3。
4. **`.github/PULL_REQUEST_TEMPLATE.md`**：四段 + Scope + Checklist。草案见 6.4。
5. **`.github/ISSUE_TEMPLATE/{bug,feature,task}.md` + `config.yml`**：沿用 deepseek-harness 的最小字段，另加"建议的改动范围"和"你打算怎么验证"。
6. **`.github/CODEOWNERS`**：默认 owner 是你；`.github/`、`AGENTS.md`、`CONTRIBUTING.md`、`RSI/evaluator/`、`RSI/schemas/`、`skills/` 必须由 owner review。草案见 6.5。
7. **标签**：`kind/*`（bug / feature / docs / chore / skill / rsi）、`area/*`（skills / rsi / ci / docs）、`p0`–`p3`、`needs-scope` / `needs-evidence` / `needs-ai-declaration` / `accepted` / `external` / `stale`。不要求机器强制，先用于人工分流。
8. **分支保护**（GitHub 仓库设置里手动开）：
   - 合并前必须走 PR；至少 1 个 owner approval；有新 commit 时 dismiss stale approval；
   - 必须通过 `RSI checks`（现有）和后续的 `PR policy`（v1）；
   - 禁止 force push 到 `main`；限制谁能 push；
   - 开启 "Require review from Code Owners"。

完成 v0 后，外部 Agent 打开 PR 时会先看到模板；没有 accepted Issue、没有填模板、碰了受保护路径的 PR 可以直接关闭并附上 6.7 的话术。

### 5.2 v1：一周内（自证检查 + 范围检查 + 决策记录）

目标：**让模板里的承诺可验证，让"散弹"自动暴露。**

1. **PR 政策检查**（草案 6.6）：`pull_request_target` + GitHub API，只读 PR body 和 changed files，**不 checkout/执行 PR 代码**。检查：
   - 四段 + Scope 是否齐全；
   - 是否链接 Issue；
   - 是否填了 AI Usage 块；
   - 是否有 `Declared scope` / `Out of scope`；
   - 对非 owner 作者：受保护路径直接 fail；文件数/area 数超过阈值先 warning，观察后再决定是否 fail。
   - 注意：如果是"必须通过"的 required check，它自身也必须从 base 分支运行，且 action 固定到 SHA。
   - "声明范围 vs 实际路径"的严格比对需要机器可读的格式（例如 `Declared scope: skills/kernel-perf/**, RSI/tools/**`），可以作为下一步；先做"存在声明 + 受保护路径 + 宽改提示"。
2. **`scripts/change-scope`**：可以是 Python/Node 的轻量版（deepseek-harness 的实现是 MIT，可参考或直接移植，保留版权声明）。本地在 push 前跑一次，review 时也可以贴到 PR；v1 先不做强制。
3. **聚合 check**：把现有 `rsi-checks` 和新的 `PR policy` 汇总成一个 `checks passed` job，供分支保护只要求一个名字，后续加检查不用改分支保护。
4. **决策记录位置**：建 `docs/decisions/`，一决策一文件（`yyyy-mm-dd-topic.md`，Problem / Decision / Alternatives considered / Consequences）；机械/局部修改豁免。不要一开始就搞 `proposed/implemented/rejected/archived` 四态迁移，先能写、能搜即可。
5. **Review checklist**：见 6.7，放在 `.github/PULL_REQUEST_TEMPLATE.md` 之外的 `docs/review-checklist.md` 或 CONTRIBUTING 里，reviewer 直接对照。
6. **关闭模板与标签自动化**：无 Issue / 超范围 / 长期无响应的 PR，用固定话术关闭；`external` + `needs-*` 标签帮助分流。

### 5.3 v2：多人或贡献流量上来后再考虑

- **可信策略工作流**：把 PR 政策、审批、标签检查都搬到 `pull_request_target` + 默认分支代码 + 固定 SHA 的 action；需要时再引入专用 check App。
- **加权审批 / 所有权加成 / `/delegate`**：只有多个 maintainer 且路径所有权有实际意义时才值得。
- **文档预算与 verify-\* gates**：当文档开始膨胀、出现重复规则时再上。
- **i18n 配对**：只有确定做双语文档时再上（deepseek-harness 用 `.i18n.yaml` + 配对 gate，成本不低）。
- **Project / Issue 生命周期自动化**：贡献流量大到需要看板时再考虑。
- **AI-DECLARATION 全量规范**：可以继续用简化版（AI 参与级别 + 关键决策记录），不必一开始就实现五阶段评分和徽章。

## 6. 最小草案（可直接复制、按习惯调整语言）

> 下面是草案，不是已生效的仓库政策。固定段名（`## Motivation` 等）建议保留英文，方便不同 Agent 识别；说明文字可以按你的偏好改成中文。

### 6.1 `CONTRIBUTING.md`（草案）

```markdown
# 贡献指南

本仓库接受外部贡献，但**贡献从 Issue 开始，而不是从 Pull Request 开始**。

## 先提 Issue

- 任何代码或文档改动，请先在 Issues 用对应模板提交，并说明：要解决的问题、建议的改动范围、你打算如何验证。
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

- 使用 AI/Agent 提交的 PR，必须在 PR 描述中填写 `## AI Usage`：每个阶段（问题拆解 / 方案设计 / 测试 / 实现 / 评审处理）由谁决策、AI 参与程度。
- 人类贡献者仍然对提交内容负责；「Agent 写的」不是免责理由。

## 证据要求

- 行为修复：修复前失败、修复后通过的回归测试。
- 性能：变更前后数字 + 测量条件 + 可复现命令。
- 文档：链接检查和渲染检查说明。
- RSI：`cd RSI && python3 tests/run_all.py`，以及对应 skill 的回归用例。

## Review

- Reviewer 按 `CODEOWNERS` 指定；没有 owner 的区域默认由维护者 review。
- PR 描述不完整（缺 Issue、缺测试、缺 AI Usage）会被打回，补齐后再 review。
- Review 只处理当前 diff；不要在同一 PR 里继续扩大范围。

## 关闭规则

- 30 天无响应，或与 Issue 结论不符的 PR 会被关闭；可以基于新 Issue 重新提交。
```

### 6.2 根 `AGENTS.md`（草案）

```markdown
# AGENTS.md

This repository contains installable Agent Skills (`skills/`) and the RSI kernel-optimization scaffold (`RSI/`). Read `CONTRIBUTING.md` before changing anything. This file holds the standing orders an agent needs in every session; deeper rules live beside the code.

## Before you start

- Work only on an accepted Issue. No accepted Issue, no code change.
- One PR does one thing. Split unrelated fixes, refactors, formatting, dependency bumps, and renames.
- Declare the expected change scope in the PR: paths/directories, and what is explicitly out of scope.
- Do not expand scope after review starts. If a fix needs a wider change, stop and comment on the Issue.

## Repository layout

- `skills/<name>/SKILL.md` — installable agent skills. Follow `skills/writing-for-agents/SKILL.md` and `skills/AGENTS.md`.
- `RSI/` — self-improving kernel-optimization scaffold. Start at `RSI/README.md` and `RSI/docs/README.md`.
- `.github/` — CI and repository policy. Protected: do not change it in a PR that is trying to pass it.

## Commands

- RSI checks: `cd RSI && python3 tests/run_all.py`
- Evaluate a campaign: `cd RSI && python3 tools/demo.py --no-tests`
- Documentation: verify local links and fenced blocks; no broken links.

## Change discipline

- Keep the change face equal to the review face: only lines the stated behavior change needs.
- Mechanical or local edits need no design note; a durable design decision goes to `docs/decisions/`.
- State only behavior the diff guarantees and the tests cover.
- Comments state contracts, not reasoning transcripts.

## Evidence

- Run the smallest check that would fail for this regression; report only commands actually run.
- Behavior change: a test that fails before and passes after.
- Performance: before/after numbers with measurement conditions.
- Documentation: link and render check.
- CI owns the exhaustive matrix; do not run the full suite by default.

## Safety

- Never modify `RSI/evaluator/` from an agent PR; evaluator changes are human-only.
- Never commit secrets or credentials.
- Do not change `.github/workflows/` or policy files to make your own PR pass. Such a change needs a separate Issue and human approval.

## Pull requests

- Fill every section of `.github/PULL_REQUEST_TEMPLATE.md`; an incomplete description is a blocked PR.
- Declare AI usage honestly.
- Reply to every review thread with a fix or a reason; do not resolve threads you did not address.
```

### 6.3 子树 `AGENTS.md`（两个简版）

`RSI/AGENTS.md`：

```markdown
# AGENTS.md — RSI

Read `RSI/README.md`, `RSI/docs/README.md`, and `RSI/docs/permissions.md` before changing anything here.

- `RSI/evaluator/` is frozen: agents call its CLI, never edit it. Evaluator changes are human-only.
- Only `operators/*/workspace/` is writable by inner-loop agents; see `RSI/docs/permissions.md`.
- Keep the canonical vocabulary from `RSI/docs/vocabulary.md`; do not name a vendor tool or metric as the thing to do.
- A skill or playbook change must pass its own regression cases and `cd RSI && python3 tests/run_all.py`.
- Record a durable design decision in `RSI/docs/` or `docs/decisions/`; mechanical edits are exempt.
```

`skills/AGENTS.md`：

```markdown
# AGENTS.md — skills

Each skill is a directory with a `SKILL.md`; it is an executable instruction for agents, not ordinary prose.

- Read `skills/writing-for-agents/SKILL.md` before creating or changing a skill.
- The frontmatter `name` and `description` are the trigger; the description must say when the skill fires, and the body must stay under the writing guide's limits.
- A skill that ships scripts must include a way to verify them (tests or a documented check command).
- Do not change a skill's output contract without updating every consumer and the skill's tests.
- Keep examples and long reference material in `references/`, reached by a pointer from `SKILL.md`.
```

### 6.4 `.github/PULL_REQUEST_TEMPLATE.md`（草案）

```markdown
<!-- 按 CONTRIBUTING.md 的 Review checklist 填写。 -->

## Motivation

<!-- 链接同仓库 Issue：Fixes #N（解决问题）或 Refs #N（提供上下文）。没有 Issue 的 PR 会被关闭。 -->

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

| 阶段 | 级别 | AI 参与度 | 决策记录 |
|:--|:--|--:|:--|
| breakdown 问题拆解 |  |  |  |
| design 方案设计 |  |  |  |
| testing 验收测试 |  |  |  |
| implementation 技术实现 |  |  |  |
| review 评审处理 | n/a 暂不适用 | — | 尚无已处理的评审意见。 |

## Checklist

- [ ] 已链接被接受的 Issue
- [ ] 一个 PR 只做一件事，变更路径没有超出 Declared scope
- [ ] 测试/证据已填写，且命令实际运行过
- [ ] 未修改 `.github/`、`AGENTS.md`、`CONTRIBUTING.md`、`RSI/evaluator/`、`RSI/schemas/`（如需要，已在单独 Issue 讨论）
- [ ] AI Usage 已如实填写
- [ ] 本地相关检查已通过
```

### 6.5 `.github/CODEOWNERS`（草案）

```text
# 默认由维护者 review
*                       @z-zijie

# 政策与 Agent 指令：owner only
/.github/               @z-zijie
/AGENTS.md              @z-zijie
/CONTRIBUTING.md        @z-zijie
/docs/                  @z-zijie

# RSI 冻结层与契约
/RSI/evaluator/         @z-zijie
/RSI/schemas/           @z-zijie
/RSI/docs/              @z-zijie

# 可执行策略：skill
/skills/                @z-zijie
```

### 6.6 `.github/workflows/pr-policy.yml`（草案）

```yaml
name: PR policy

on:
  pull_request_target:
    types: [opened, edited, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  issues: read
  pull-requests: read

jobs:
  policy:
    if: github.event.pull_request.draft == false
    runs-on: ubuntu-latest
    steps:
      # 只读取 API 数据，绝不 checkout 或执行 PR 代码。
      - uses: actions/github-script@v7
        with:
          script: |
            const pr = context.payload.pull_request;
            const body = pr.body || "";
            const isOwner = pr.user.login === "z-zijie";
            const problems = [];

            for (const heading of ["## Motivation", "## Scope", "## Changes", "## Testing", "## AI Usage"]) {
              if (!body.includes(heading)) problems.push(`missing section: ${heading}`);
            }
            if (!/(Fixes|Closes|Resolves|Refs)\s+#\d+/.test(body)) {
              problems.push("missing Issue reference (Fixes/Closes/Resolves/Refs #N)");
            }
            if (!body.includes("ai-declaration:start")) {
              problems.push("missing AI declaration block");
            }
            if (!/Declared scope/i.test(body) || !/Out of scope/i.test(body)) {
              problems.push("missing Declared scope / Out of scope");
            }

            const files = await github.paginate(github.rest.pulls.listFiles, {
              ...context.repo,
              pull_number: pr.number,
              per_page: 100,
            });
            const paths = files.map((f) => f.filename);
            const protectedPaths = paths.filter((p) =>
              /^(\.github\/|AGENTS\.md$|CONTRIBUTING\.md$|RSI\/(evaluator|schemas)\/)/.test(p)
            );
            if (!isOwner && protectedPaths.length > 0) {
              problems.push(`protected paths touched: ${protectedPaths.join(", ")}`);
            }
            if (!isOwner && files.length > 20) {
              core.warning(`wide change: ${files.length} files; split it or justify in Scope`);
            }
            const areas = new Set(paths.map((p) => p.split("/")[0]));
            if (!isOwner && areas.size > 3) {
              core.warning(`touches ${areas.size} areas: ${[...areas].join(", ")}`);
            }

            if (problems.length > 0) {
              core.setFailed(problems.join("\n"));
            }
```

使用注意：

- 生产上把 `actions/github-script@v7` 固定到 commit SHA（status-writing 类 job 尤其应如此；deepseek-harness 的 approval publisher 就是这样做的）。
- 这个 check 自身如果作为 required check，其代码来自默认分支（`pull_request_target` 的语义），外部 PR 改不了它；但 **owner 自己改政策** 时它仍会运行旧版本，这是有意的保护。
- `files.length > 20` 和 `areas.size > 3` 只发出 warning，不阻断合并；观察一段时间、确认误报率后再决定是否改成 fail（把 `core.warning` 换成 `problems.push`）。
- 如果以后引入更多检查，把它们汇总到一个 `checks passed` job，分支保护只要求那个名字。

### 6.7 标签、关闭话术、Review checklist

**标签**

- `kind/bug`、`kind/feature`、`kind/docs`、`kind/chore`、`kind/skill`、`kind/rsi`（恰好一个）
- `area/skills`、`area/rsi`、`area/ci`、`area/docs`（至少一个）
- `p0`–`p3`（最多一个）
- 流程：`accepted`、`external`、`needs-scope`、`needs-evidence`、`needs-ai-declaration`、`stale`

**关闭话术（无 accepted Issue / 散弹式 PR）**

> 这个 PR 缺少被接受的 Issue，或包含超出单一主题的改动，因此先关闭。请先用 Issue 模板描述问题、建议范围和验证方式；Issue 被接受后再提交一个引用它的 PR。谢谢。

英文版：

> This PR is closed because it has no accepted Issue, or it mixes changes beyond a single topic. Please open an Issue with the problem, the proposed scope, and how you will verify it; once the Issue is accepted, submit a new PR that references it.

**Review checklist**

- [ ] PR 有 accepted Issue，且 Motivation 与 Issue 一致。
- [ ] 变更路径在 `Declared scope` 内；`Out of scope` 的事没有被顺手做掉。
- [ ] 没有混入无关重构、格式化、依赖升级、重命名。
- [ ] 行为变更带回归测试；性能变更有前后数字和测量条件；文档变更有链接检查。
- [ ] 测试设计覆盖了 `Changes` 里的每一项；`Proof` 里是实际运行的证据。
- [ ] AI Usage 与提交内容相符；高风险阶段（design/implementation 的 auto）加重审查。
- [ ] 没有修改受保护路径，或修改已按规则走单独 PR + owner 批准。
- [ ] 审查只针对当前 diff；超范围的意见要求拆分，不在本 PR 里继续扩大。

## 7. 不建议照搬的部分

| 机制 | 为什么不建议 | 替代 |
|---|---|---|
| 完全不接受外部 PR | 会牺牲社区贡献；除非仓库定位就是个人内部仓库 | Issue-first + accepted scope；对散弹式 PR 直接关闭 |
| 加权审批 / 所有权分数 / `/delegate` | 需要多个 maintainer、稳定的 git blame 数据和专用 App；单人仓库无收益 | CODEOWNERS + 分支保护 + 1 个 owner approval |
| Project / Issue 生命周期自动化 | 需要维护 Project 字段、App token 和事件驱动逻辑；容易坏 | 标签 + 手动状态；流量大了再上 |
| i18n 配对（`.i18n.yaml` + 配对 gate） | 只有确定做双语时才值得；维护成本高 | 先单语；要双语时再建配对机制 |
| per-file 100% coverage、32 个 workflow | 与 monorepo 的规模和团队配套；小仓库会变成维护负担 | 按 path 过滤的少量必需检查 + 一个聚合 check |
| AI-DECLARATION 全量五阶段评分 | 规范很细，填写成本高；没有 CI 强制时容易流于形式 | 先用简化版：AI 参与级别 + 关键决策记录；需要时再细化 |
| 18 个 AGENTS.md 一次建齐 | 没有对应目录就不需要 | 先根 + `RSI/` + `skills/` 三份，按需增加 |
| 大型 monorepo 的 docs 预算/生成目录 gate | 文档量没到那个规模 | 先保证链接可点、代码块闭合、一个主题一份文档 |

## 8. 参考文件索引（deepseek-harness）

| 路径 | 作用 | 对本仓库的启示 |
|---|---|---|
| `CONTRIBUTING.md` / `CONTRIBUTING.zh.md` | 外部贡献政策：当前不接受外部 PR | 入口可以比审查更严格；至少 Issue-first |
| `.github/ISSUE_TEMPLATE/{bug,feature,task}.md` | 最小 Issue 字段 | 直接用 |
| `.github/issue-management/README.md`、`policy.mjs`、`rules.mjs` | Issue 引用和标签的 required check | 用轻量版：Issue 引用 + 一个 kind + 一个 area |
| `AGENTS.md`（2,069 词 / 预算 2,080）、`CLAUDE.md` 符号链接 | 根 standing orders | 根文件要短；单一真源 |
| `docs/AGENTS.md` | 文档标准：tier taxonomy、one home per fact、预算、slop checklist | 先借"一个事实一个 home"和"当前状态"两条 |
| `packages/AGENTS.md`、`.github/AGENTS.md`、`scripts/AGENTS.md` 等 | 子树规则 | 先建 `RSI/` 和 `skills/` 两份 |
| `.agents/skills/dsh-pre-push-checks/SKILL.md` | push 前最小证据选择 | 写成本仓库的 pre-push 指南 |
| `.agents/skills/dsh-pr-description/SKILL.md` | PR 四段 + AI Usage + 评分规则 | 模板和规则的主要来源 |
| `.agents/skills/dsh-code-review/SKILL.md` | Review 的 sources of truth、blocking requirements、manual checks | 改写成个人仓库的 review checklist |
| `.agents/notes/README.md` | Agent Notes 的生命周期、格式、归档规则 | 先做简化版 `docs/decisions/` |
| `.github/pull_request_template.md` | 四段模板 + AI Usage 表 | 见 6.4 草案 |
| `.github/review-ownership/README.md`、`approval-policy.json`、`weighted-approval.yml` | 加权审批、所有权、委托、可信发布 | 多人后再考虑；先 CODEOWNERS + 分支保护 |
| `.github/workflows/ci.yml`、`ci-windows.yml`、`ci-python.yml`、`ci-master.yml` | PR/master 分层的 CI 与 verdict job | 用聚合 check + path filter |
| `.github/workflows/issue-policy.yml`、`issue-lifecycle.yml` | 可信 base-branch 政策 + Project 生命周期 | 政策从默认分支跑；生命周期先不做 |
| `.github/workflows/pr-check-on-head-branch.yml`、`pr-check-after-approve.yml`、`pr-check-on-base-branch.yml` | `Policy` 系列检查 | 参考其 off/report/block 模式和 trusted checkout |
| `lefthook.yml`、`docs/development.md` | 窄钩子 + 全 CI 的分工 | 本地钩子只做快检查 |
| `scripts/change-scope.ts` | 改动路径报告 | v1 的 change-scope 参考 |
| `docs/testing.md` | 测试层级、coverage、verify-the-world、real entry path、negative controls | RSI 已有类似纪律；skills 层可借用"真实入口/反例"两条 |
| `docs/defensive-patterns/README.md` | review 中强制执行的架构规则；"change face = review face" | 直接把这句话用于 scope 规则 |
| `README.md` | 项目介绍与贡献入口 | 本仓库 README 可加一节"贡献" |
| `.github/workflows/expected-filenames.yml`、`scripts/ci-workflow.spec.ts` | 小政策检查也可以用 workflow 测试保护 | 可选 |

## 9. 下一步

如果这份整理要变成真正生效的规则，建议按下面顺序推进（可以作为后续 Issue）：

1. **v0 文件**：`CONTRIBUTING.md`、根 `AGENTS.md` + `CLAUDE.md`、`RSI/AGENTS.md`、`skills/AGENTS.md`、PR 模板、Issue 模板、`CODEOWNERS`。
2. **仓库设置**：分支保护、CODEOWNERS review、required checks。
3. **v1 自动化**：`pr-policy.yml`、`change-scope`、聚合 check、`docs/decisions/`。
4. **试运行**：用 2–3 个真实外部 PR 检验；统计"无 Issue PR 数、平均变更文件数、Review 追问次数、返工轮次"。
5. **再决定 v2**：只有在贡献流量和 maintainer 数量支持时才上加权审批/文档预算/Project 自动化。

需要讨论的三个决策点：

- **外部 PR 是完全不收，还是 Issue-first？** 前者最省 Review，后者保留社区。
- **AI 全自主提交是否接受？** 可以接受，但要求 AI Usage 如实填写，并在 design/implementation 阶段加重审查。
- **受保护路径的范围**：`.github/` + `AGENTS.md` + `RSI/evaluator/` 是底线；`skills/` 是否也算，取决于你是否接受外部 skill 贡献。

---

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
