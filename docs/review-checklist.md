# Review checklist

评审 PR 时按这个顺序看。发现 blocker 时只给一条可执行的下一步；不在本 PR 里扩大讨论。

## 入口

- [ ] PR 链接了被接受的 Issue；外部作者的 PR 没有 Issue 直接关闭。
- [ ] 一个 PR 只做一件事；`Declared scope` 与实际改动路径一致，`Out of scope` 没有被顺手做掉。

## 改动

- [ ] 没有混入无关重构、格式化、依赖升级、重命名。
- [ ] 行为变更带回归测试；性能变更有前后数字和测量条件；文档变更有链接检查。
- [ ] 测试设计覆盖 `Changes` 的每一项；`Proof` 是实际运行的证据。
- [ ] 没有修改受保护路径，或修改已按 `CONTRIBUTING.md` 走单独 Issue + owner 批准。

## Agent 协作

- [ ] `AI Usage` 与提交内容相符；design/implementation 为 `auto` 的 PR 加重审查。
- [ ] 高风险改动（公共接口、CI、评估器、schema）有对应的验证或决策记录。
- [ ] 没有为了让检查通过而修改 `.github/`、`AGENTS.md`、`CONTRIBUTING.md`。

## 结论

- [ ] 只给当前 diff 内的意见；超范围的意见要求拆分。
- [ ] 通过时说明还需要哪些检查；不通过时给出最小修复路径。
