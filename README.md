# skills

Agent Skills 仓库，支持 Claude Code、Codex、Cursor 等 Agent。

可以用以下命令一键安装全部 skill：

```bash
npx skills add https://github.com/z-zijie/skills.git
```

## 仓库里的 skill

| 目录 | 内容 |
| --- | --- |
| [`skills/bugfix/`](skills/bugfix) | 缺陷修复流程：`reproduce`、`diagnose`、`fix`、`verify`、`ship` |
| [`skills/research/`](skills/research) | 研究系列：`autoresearch` 及立题、实验、审数、成文等 skill |
| [`skills/RSI/`](skills/RSI) | RSI 自改进算子优化框架：22 个 skill，外加 evaluator、知识库、编排器 |
| [`skills/cannbench-serial-eval/`](skills/cannbench-serial-eval) | CannBench 串行逐用例评测 |
| [`skills/create-tool/`](skills/create-tool) | 单文件 CLI 工具脚本 |
| [`skills/kernel-perf/`](skills/kernel-perf) | 昇腾 kernel 性能采集与分析 |
| [`skills/ops-test-kit/`](skills/ops-test-kit) | 用 TTK 做算子批量测试 |
| [`skills/writing-for-agents/`](skills/writing-for-agents) | 写给 Agent 的文档 |

## RSI skills 一键安装

[`skills/RSI/`](skills/RSI) 是一套完整的自改进 kernel 优化框架，含 22 个 skill
（`optimize-operator`、`diagnose-bottleneck`、`propose-hypothesis` 等）。只装这一套：

```bash
npx skills add https://github.com/z-zijie/skills/tree/main/skills/RSI --skill '*' -y
```

先看会装哪些、不落盘：

```bash
npx skills add https://github.com/z-zijie/skills/tree/main/skills/RSI --list
```

只装一个，例如入口 `optimize-operator`：

```bash
npx skills add https://github.com/z-zijie/skills/tree/main/skills/RSI --skill optimize-operator
```

默认装到当前项目的 `./<agent>/skills/`；加 `-g` 装到用户级目录，加 `--copy` 复制而非软链。

`npx skills add` 逐个复制 skill 目录，装好的是 `SKILL.md` 与其中的脚本；脚本运行时依赖
`skills/RSI/` 下的 `evaluator/`、`tooling/`、`schemas/`、`kb/`。要跑 RSI 本体，clone 仓库后
`cd skills/RSI` 用 `tools/rsi-cli`，说明见 [`skills/RSI/README.md`](skills/RSI/README.md) 与
[`skills/RSI/docs/QUICKSTART.md`](skills/RSI/docs/QUICKSTART.md)。

## License

[MIT](LICENSE)
