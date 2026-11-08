# 维护者设置

工程纪律有两部分不能通过 PR 完成：**标签**和**分支保护**。合并本 PR 后，由仓库所有者执行一次。

## 1. 创建标签

在仓库根目录执行（`gh` 已登录且有该仓库权限）。标签已存在时用 `gh label edit` 更新。

```sh
gh label create kind/bug --description "Defect in existing behavior" --color B60205
gh label create kind/feature --description "New or intentionally changed behavior" --color 0E8A16
gh label create kind/docs --description "Documentation only" --color 0075CA
gh label create kind/chore --description "Maintenance, tooling, or CI" --color FEF2C0
gh label create kind/skill --description "Agent skill change" --color 5319E7
gh label create kind/rsi --description "RSI scaffold change" --color 1D76DB

gh label create area/skills --description "skills/ tree" --color 0052CC
gh label create area/rsi --description "RSI/ tree" --color 006B75
gh label create area/ci --description ".github/ and automation" --color 6A737D
gh label create area/docs --description "docs/ tree" --color 1D76DB

gh label create p0 --description "Drop everything" --color B60205
gh label create p1 --description "Next" --color D93F0B
gh label create p2 --description "Normal" --color FBCA04
gh label create p3 --description "Later" --color 0E8A16

gh label create accepted --description "Issue accepted; a PR may follow" --color 0E8A16
gh label create external --description "PR or Issue from outside the owner accounts" --color C5DEF5
gh label create needs-scope --description "Declared scope missing or exceeded" --color FBCA04
gh label create needs-evidence --description "Tests or proof missing" --color D4C5F9
gh label create needs-ai-declaration --description "AI Usage section missing or incomplete" --color F9D0C4
gh label create stale --description "No activity; candidate for closing" --color EDEDED
```

## 2. 开启分支保护（`main`）

GitHub → Settings → Branches → Add branch protection rule：

- Branch name pattern：`main`
- **Require a pull request before merging**：开启
  - Required approvals：1
  - Dismiss stale pull request approvals when new commits are pushed：开启
  - Require review from Code Owners：开启
- **Require status checks to pass before merging**：开启
  - Require branches to be up to date before merging：开启
  - Required checks：
    - `policy`（来自 `PR policy` workflow；GitHub UI 可能显示为 `PR policy / policy`）
    - `checks passed`（来自 `RSI checks` workflow；GitHub UI 可能显示为 `RSI checks / checks passed`）
- **Require conversation resolution before merging**：开启
- **Do not allow bypassing the above settings**：按需（勾选后 owner 也不能绕过）
- **Restrict force pushes**：开启（或选择 Block force pushes）
- **Allow deletions**：关闭

注意：required check 的名字只有在某个 PR 上实际运行过一次后才会出现在选择列表里。先合并本 PR，再开一个只改 `docs/` 的小 PR，让两个检查各跑一次，然后把它们加进去。

## 3. CODEOWNERS

`.github/CODEOWNERS` 在进入默认分支后生效；再配合上面的 “Require review from Code Owners”，受保护路径的改动就必须由 `@z-zijie` review。

## 4. 可信账号

`PR policy` 把 `z-zijie` 和 `zijie-super-agent` 视为 trusted：不要求 Issue 引用，不因受保护路径直接失败；但 PR 模板段落、Scope 和 AI 声明仍然必填。外部贡献者没有这个豁免。要调整名单，修改 `.github/workflows/pr-policy.yml` 里的 `trusted` 数组。

## 5. 合并策略

本仓库不配置 auto-merge；每个 PR 由人合并。Agent（包括 `zijie-super-agent`）不得合并自己的 PR。

## 6. 验证

依次开三个测试 PR：

1. 只改 `docs/`：`policy` 与 `checks passed` 都通过。
2. 删掉模板里的 `## Testing`：`PR policy` 失败并指出缺少段落。
3. 用非 trusted 账号改 `.github/`：`PR policy` 失败并列出受保护路径。

### 单 owner 的注意点

如果只有 `@z-zijie` 一个 code owner，你自己开的 PR 无法由自己 approve。两种处理：

- 保留 “Do not allow bypassing the above settings” 不勾选，让你以 admin 身份合并；
- 或把 `zijie-super-agent` 也加为 code owner，由它 review 你开的 PR（它不应 review 或合并自己的 PR）。
