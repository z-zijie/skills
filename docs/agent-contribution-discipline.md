# Agent 协作的贡献纪律：deepseek-harness 调研与本仓库落地

> **调研对象**：`deepseek-ai/deepseek-harness`，`master` @ `fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159`（2026-11-04，MIT）。
> **调研时间**：2026-11-08。
> **落地状态**：本 PR 已把 v0 规则落地为仓库文件（见 4.2），并加入 `PR policy` 检查与 `RSI checks / checks passed` 聚合检查；标签和分支保护需要仓库所有者手动开启（见 `docs/maintainer-setup.md`）。
> **引用约定**：文中 `deepseek-harness:<path>` 指该仓库内路径；结论基于上述快照，上游规则可能继续变化。

## 0. 先看结论

deepseek-harness 的做法可以压缩成一句话：**先管入口，再让 PR 自证，最后用可信的自动化做 Review 门槛；指令本身分层、按需加载。**

三个最值得抄的机制：

| 机制 | 直接效果 | 本仓库状态 |
|---|---|---|
| **入口管控**：外部贡献从 Issue 开始；没有 accepted Issue 的 PR 直接关闭（deepseek-harness 当前甚至完全不接受外部 PR） | 散弹式 PR 不进入 Review 队列，维护者不必先读代码再判断"想干什么" | **已落地**：`CONTRIBUTING.md` + Issue 模板 + `PR policy`（外部作者强制 Issue 引用）；标签和分支保护待手动开启 |
| **PR 自证**：模板 + 仓库内 skill 规定 Motivation / Changes / Testing / AI Usage，要求 Issue 链接、测试设计、证据和 AI 参与度申报（本仓库加了 Scope 段） | Reviewer 不用追问"改了什么、为什么、测了什么、谁做的决策" | **已落地**：PR 模板 + AI Usage + `docs/review-checklist.md` |
| **可信自动化 + 路径所有权**：策略检查从默认分支运行，PR 不能修改自己的规则；关键路径必须由 owner review | 外部 Agent 无法通过改 CI 来让自己的 PR 过关；关键区域不会被陌生 Agent 顺带改掉 | **部分落地**：CODEOWNERS + `PR policy` 从 base 分支运行；分支保护待手动开启；加权审批未采用 |

三条判断：

1. deepseek-harness 是 monorepo + 内部团队，它的规则密度、32 个 workflow、加权审批体系不适合原样搬进个人仓库；本仓库采用它的**机制形状**，不采用它的**规模**。
2. **规则要能被 Agent 读到**：外部 Agent 不会读你的 wiki 或聊天记录，但会读根目录 `AGENTS.md`、`CLAUDE.md`、PR 模板、Issue 模板和 CI 失败信息。纪律要写在这些位置——本 PR 已写入。
3. **规则要能被机器执行**：只写在文档里的规则是建议，会随 Agent 的发挥而失效；能在 PR 上自动失败的规则才是纪律。本 PR 落地了两个可作为 required check 的检查。

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
  - **Issue 模板**只有三种：Bug（Summary / Reproduction / Current behavior / Expected behavior / Environment）、Feature（Motivation / Behavior）、Task（Summary / Deliverables）。字段少而具体，引导写清"问题"和"预期"。
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

**重要限定**：在 deepseek-harness 仓库内可见的实现中，AI Usage 由 PR 模板和 skill 约束；`.github/issue-management/policy.test.mjs` 会检查模板与 skill 是否符合 AI-DECLARATION 规范版本，但 **PR 正文本身没有可见的 CI 校验**。这是"声明 + 社会约束"，不是机器强制。本仓库的 `PR policy` 只校验 AI 声明块和段落是否存在，不校验内容真实性；真实性由 reviewer 判断。

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
- **CI 分层**：`ci.yml` 是 PR-only 的 CI（static、coverage、lint、benchmarks、snapshots+artifacts、Node 兼容等）；Windows 在 `ci-windows.yml`、Python 在 `ci-python.yml`，各自有 verdict job：`all checks passed`、`windows checks passed`、`python checks passed`；`ci-master.yml` 只在 master push 上跑 master-only 的平台/自托管检查。workflow 数量 32 个，但职责分离得很清楚：PR 只跑 PR 需要的，master 只跑 master 需要的。
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

## 4. 本仓库的落地

### 4.1 落地前

- 仓库：`z-zijie/skills`，public，默认分支 `main`；0 star / 0 fork；内容为 `skills/`（8 个可安装 skill）+ `RSI/`（自改进 kernel 优化脚手架）+ `.github/workflows/rsi-checks.yml`。
- README 是中文，安装方式为 `npx skills add https://github.com/z-zijie/skills.git`；`skills/` 里已有 `writing-for-agents`，说明对"给 Agent 写文档"本身有方法论。
- PR 历史基本由 `zijie-super-agent` / `z-zijie` 提交，尚无外部贡献流量；CI 只对 `RSI/**` 和相关 workflow 变更触发。
- `RSI/` 内部已经有很强的纪律：`RSI/docs/permissions.md` 规定谁能写什么并代码化执行；`RSI/docs/guarantees.md`、`verification.md`、`anti-cheating.md` 把可验证性和反作弊写清楚；`RSI/docs/AUTHORING.md` 是 skill/playbook 的写作约束；CI 每次改动跑契约、统计、状态机、redteam、reference backend demo。
- 缺口不在"内部工程纪律"，而在**仓库层的贡献入口**：没有 `CONTRIBUTING.md`、没有根 `AGENTS.md`、没有 PR/Issue 模板、没有 CODEOWNERS、没有标签体系、没有 PR 政策检查。外部 Agent 进入仓库时拿不到任何"该怎么贡献"的指令。

### 4.2 本 PR 落地的文件

| 文件 | 作用 |
|---|---|
| `CONTRIBUTING.md` | 入口规则：Issue-first、接受/不接受、AI 参与声明、证据要求、PR 要求、受保护路径、Review/关闭规则 |
| `AGENTS.md` + `CLAUDE.md` | 根 Agent standing orders；`CLAUDE.md` 是 `AGENTS.md` 的符号链接 |
| `RSI/AGENTS.md` | RSI 子树规则：冻结 evaluator、写入边界、canonical vocabulary、测试命令 |
| `skills/AGENTS.md` | skills 子树规则：skill 是可执行策略、frontmatter/触发描述、脚本验证、output contract |
| `.github/PULL_REQUEST_TEMPLATE.md` | Motivation / Scope / Changes / Testing / AI Usage / Checklist |
| `.github/ISSUE_TEMPLATE/{bug,feature,task}.md` + `config.yml` | 三类 Issue 模板；关闭空白 Issue |
| `.github/CODEOWNERS` | 默认 owner；`.github/`、`AGENTS.md`、`CONTRIBUTING.md`、`docs/`、`RSI/evaluator/`、`RSI/schemas/`、`skills/` 由 owner review |
| `.github/workflows/pr-policy.yml` | `PR policy`：从 base 分支运行、只读 API，校验模板段落、Issue 引用、AI 声明、Scope、受保护路径、宽改提示 |
| `.github/workflows/rsi-checks.yml` | 新增 `changes` + `checks passed` 聚合检查；`RSI checks / checks passed` 可在分支保护里作为 required check，docs-only PR 也会成功 |
| `docs/review-checklist.md` | Reviewer 对照表 |
| `docs/decisions/README.md` | 决策记录格式与写作条件 |
| `docs/decisions/2026-11-08-agent-contribution-discipline.md` | 本决策的记录，也是决策记录格式的示范 |
| `docs/maintainer-setup.md` | 标签、分支保护、required checks 的手动设置步骤 |
| `README.md` | 增加 Contributing 入口链接 |
| `docs/agent-contribution-discipline.md` | 本报告：调研、设计依据、边界与后续 |

### 4.3 RSI 已经解决了什么

本仓库不是从零开始。`RSI/` 内部已经有一套与 deepseek-harness 同构的纪律，缺的是把它们提升到仓库层，并覆盖 `skills/` 和外部贡献：

| deepseek-harness 机制 | 本仓库已有对应物 | 本 PR 补上的部分 |
|---|---|---|
| 冻结 evaluator / 可信策略 | `RSI/docs/permissions.md` 的写入边界；evaluator 只读；CI 每次跑契约与 redteam | CODEOWNERS + `PR policy` 把边界提升到仓库层，外部 PR 也会被拦 |
| 证据分级 / negative controls | `RSI/docs/verification.md`、`anti-cheating.md`；`tests/run_all.py`；redteam | `CONTRIBUTING.md` 把"真实入口/反例/可复现"写进 skills 层的贡献要求 |
| Agent Notes / 决策记录 | RSI 的 `docs/{guarantees,closures,disagreement-log,...}.md` 和 `_src/rsi-build-plan.md` | `docs/decisions/` 作为仓库级决策记录位置 |
| AGENTS.md 分层 | `RSI/docs/AUTHORING.md` + `RSI/docs/README.md` 的"该读哪份"索引 | 根 `AGENTS.md`、`RSI/AGENTS.md`、`skills/AGENTS.md` 三份入口 |
| 窄钩子 + 全 CI | `rsi-checks.yml` 覆盖 RSI 契约、统计、状态机、redteam | `checks passed` 聚合 check + `PR policy`；skills 层的最小自动校验仍待补 |

### 4.4 仍需人工完成的仓库设置

- **标签**：`kind/*`、`area/*`、`p0`–`p3`、`needs-*`、`accepted`、`external`、`stale`。创建命令见 `docs/maintainer-setup.md`。
- **分支保护**：要求 PR、1 个 owner approval、CODEOWNERS review、stale approval dismissal、`PR policy / policy` 和 `RSI checks / checks passed`、禁止 force push。步骤见 `docs/maintainer-setup.md`。
- 这两项是仓库设置，不能由本 PR 的代码完成；Agent 也不会替你改仓库设置。

### 4.5 仍未覆盖的缺口

- **change-scope 脚本**：deepseek-harness 的 `scripts/change-scope.ts` 能列出改动路径；本仓库还没有等价物。当前 `PR policy` 只能检查受保护路径和宽改阈值，不能严格比对"声明范围 vs 实际路径"。
- **skills 层自动校验**：`skills/` 下每个 skill 的 frontmatter/触发描述/脚本测试还没有统一 gate；目前靠 review 和 `skills/AGENTS.md`。
- **文档预算与链接 gate**：本报告的引用和链接靠人工检查；文档量上来后需要 `verify-md-links` 之类的 gate。
- **Issue 生命周期自动化 / 加权审批 / i18n 配对**：见第 7 节，规模不够时不建议引入。

## 5. 设计取舍

1. **外部作者强制 Issue，owner 与 owner 的 agent 豁免。** 外部贡献必须先有 accepted Issue；`z-zijie` 和 `zijie-super-agent` 不要求 Issue 引用，也不因受保护路径直接失败。原因是前者是纪律的目标对象，后者是维护者自己的日常通道；如果 owner 也被 Issue 卡住，纪律会先卡住维护者。模板段落、Scope 和 AI 声明对所有人都必填。
2. **`PR policy` 用 `pull_request_target` + 只读 API。** 它从默认分支运行，只读 PR body 和 changed files，不 checkout/执行 PR 代码。这样外部 PR 不能通过修改 workflow 绕过检查；代价是 policy 变更要合并进默认分支才生效——这正是想要的。
3. **受保护路径用“硬拦 + CODEOWNERS 双保险”。** `PR policy` 对外部作者的 `.github/`、`AGENTS.md`、`CONTRIBUTING.md`、`RSI/evaluator/`、`RSI/schemas/` 直接失败；CODEOWNERS 在任何情况下都要求 owner review。两种机制覆盖不同层面：前者是自动闸门，后者是人工责任。
4. **`RSI checks` 改成聚合 check。** 原来的 workflow 只在 RSI 路径变化时触发；如果把它直接设为 required check，docs-only PR 会永远等不到它。新增的 `checks passed` job 让所有 PR 都能得到一个确定的 check 结果：RSI 改动必须通过完整检查，非 RSI 改动快速成功。`PR policy / policy` 也是所有非 draft PR 都会运行。
5. **不引入加权审批、Project 生命周期、i18n 配对、per-file 100% coverage。** 这些是 deepseek-harness 在团队和 monorepo 规模下的解；本仓库是单人维护，引入后维护成本大于收益。具体见第 7 节。
6. **不配置 auto-merge。** 每个 PR 由人合并；Agent（包括 `zijie-super-agent`）不得合并自己的 PR。

## 6. 验证

### 6.1 本 PR 的验证

- Markdown：所有新增 `.md` 代码围栏平衡、无行尾空白、本地路径引用存在。
- YAML：两个 workflow 语法检查通过。
- RSI：新增 `RSI/AGENTS.md` 会触发 `RSI checks`；本地 `python3 tests/run_all.py` 已通过（46 passed），其余 RSI gate 由 workflow 在 CI 中运行。
- `PR policy` 只会在合并后对后续 PR 生效（`pull_request_target` 从 base 分支取 workflow）；本 PR 自身不由它检查。
- 合并后按 `docs/maintainer-setup.md` 的第 6 节开三个测试 PR，验证通过、缺段落失败、外部作者碰受保护路径失败。

### 6.2 观察指标

- 无 Issue 的外部 PR 数（目标 0）。
- 外部 PR 平均变更文件数 / 触碰的顶层目录数。
- Reviewer 为搞清楚 scope/evidence 而追问的次数。
- 同一 PR 的返工轮次。
- `PR policy` / `RSI checks / checks passed` 的失败原因分布（用于调整阈值）。

## 7. 不建议照搬的部分

| 机制 | 为什么不建议 | 替代 |
|---|---|---|
| 完全不接受外部 PR | 会牺牲社区贡献；除非仓库定位就是个人内部仓库 | Issue-first + accepted scope；对散弹式 PR 直接关闭 |
| 加权审批 / 所有权分数 / `/delegate` | 需要多个 maintainer、稳定的 git blame 数据和专用 App；单人仓库无收益 | CODEOWNERS + 分支保护 + 1 个 owner approval |
| Project / Issue 生命周期自动化 | 需要维护 Project 字段、App token 和事件驱动逻辑；容易坏 | 标签 + 手动状态；流量大了再上 |
| i18n 配对（`.i18n.yaml` + 配对 gate） | 只有确定做双语时才值得；维护成本高 | 先单语；要双语时再建配对机制 |
| per-file 100% coverage、32 个 workflow | 与 monorepo 的规模和团队配套；小仓库会变成维护负担 | 按 path 过滤的少量必需检查 + 一个聚合 check |
| AI-DECLARATION 全量五阶段评分 | 规范很细，填写成本高；没有 CI 强制时容易流于形式 | 简化版：AI 参与级别 + 关键决策记录；需要时再细化 |
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
| `.github/pull_request_template.md` | 四段模板 + AI Usage 表 | 见 `.github/PULL_REQUEST_TEMPLATE.md` |
| `.github/review-ownership/README.md`、`approval-policy.json`、`weighted-approval.yml` | 加权审批、所有权、委托、可信发布 | 多人后再考虑；先 CODEOWNERS + 分支保护 |
| `.github/workflows/ci.yml`、`ci-windows.yml`、`ci-python.yml`、`ci-master.yml` | PR/master 分层的 CI 与 verdict job | 用聚合 check + path filter |
| `.github/workflows/issue-policy.yml`、`issue-lifecycle.yml` | 可信 base-branch 政策 + Project 生命周期 | 政策从默认分支跑；生命周期先不做 |
| `.github/workflows/pr-check-on-head-branch.yml`、`pr-check-after-approve.yml`、`pr-check-on-base-branch.yml` | `Policy` 系列检查 | 参考其 off/report/block 模式和 trusted checkout |
| `lefthook.yml`、`docs/development.md` | 窄钩子 + 全 CI 的分工 | 本地钩子只做快检查 |
| `scripts/change-scope.ts` | 改动路径报告 | 后续可加的 change-scope |
| `docs/testing.md` | 测试层级、coverage、verify-the-world、real entry path、negative controls | RSI 已有类似纪律；skills 层可借用"真实入口/反例"两条 |
| `docs/defensive-patterns/README.md` | review 中强制执行的架构规则；"change face = review face" | 已把这句话用于 scope 规则 |
| `README.md` | 项目介绍与贡献入口 | 本仓库 README 已加 Contributing |
| `.github/workflows/expected-filenames.yml`、`scripts/ci-workflow.spec.ts` | 小政策检查也可以用 workflow 测试保护 | 可选 |

## 9. 后续路线

1. **v0（已完成）**：`CONTRIBUTING.md`、根/RSI/skills `AGENTS.md`、PR/Issue 模板、CODEOWNERS、PR 模板、review checklist、决策记录、维护者设置文档。
2. **v1（部分完成）**：`PR policy` 与 `RSI checks / checks passed` 已加入；剩余：`change-scope`、skills 层自动校验、文档链接 gate、PR policy 的“声明范围 vs 实际路径”严格比对。
3. **v2（按需）**：加权审批/所有权、Project 生命周期、i18n 配对、docs 预算。只有在贡献流量和 maintainer 数量支持时才做。

需要讨论的三个决策点：

- **外部 PR 是完全不收，还是 Issue-first？** 当前实现选 Issue-first；如果之后觉得 Review 仍然重，可以把 `CONTRIBUTING.md` 改成"暂不接受外部 PR"。
- **AI 全自主提交是否接受？** 当前接受，但要求 AI Usage 如实填写，并在 design/implementation 阶段加重审查。
- **受保护路径的范围**：当前是 `.github/` + `AGENTS.md`/`CLAUDE.md`/`CONTRIBUTING.md` + `RSI/evaluator/` + `RSI/schemas/`；`skills/` 由 CODEOWNERS 要求 owner review，但不在 `PR policy` 的硬拦列表里——取决于你是否接受外部 skill 贡献。

---

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
