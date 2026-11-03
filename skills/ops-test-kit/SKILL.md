---
name: ops-test-kit
description: 用 TTK（CANN 官方算子批量测试框架 ops-test-kit）做昇腾算子的批量测试与验证：功能验证、精度/golden 比对、性能评估、无卡仿真。当用户要求跑算子测试或验证算子正确性，在算子仓库遇到待执行的用例 CSV 或 tests/assets/（golden.py、input.py），或没有 NPU 也要验证算子时使用。
---

# TTK 算子批量测试

TTK（ops Test Kit）是 CANN 官方算子库的批量测试框架，Kernel / GEIR / ACLNN / E2E 四条通路覆盖算子层到框架层，独立仓库，自带面向 agent 的完整指导。本 skill 只做两件事：把工具就位，然后交接——怎么用，进仓库读它的文档。

## 1. 就位

`python3 -m ttk` 只能在 TTK 仓库根目录下执行。优先复用现成克隆（`git pull` 更新到最新），没有再克隆到一个固定会复用的位置。依赖只装进仓库内的 venv，不污染外部环境：

```shell
git clone https://gitcode.com/cann/ops-test-kit.git
cd ops-test-kit
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m ttk info    # 自检：设备与 CANN 版本，跑通即就位
```

之后一律用 `.venv/bin/python -m ttk ...` 执行。前置：已装 CANN（TTK 启动时自动探测并 source）；ACLNN / E2E 通路另需配套的 torch / torch_npu——同样只装进 venv，配套关系见 TTK 文档。

## 2. 交接

进仓库后按它自己的索引走，不要凭记忆拼命令：

1. 读根目录 `AGENTS.md`：agent 总入口，含通路选择规则与技能索引
2. 按任务取 `.claude/skills/` 下的对应技能
3. 细节查 `docs/`：用例生成、任务执行、精度比对、各通路指南、FAQ

从这里起以仓库内文档为准。算子仓库侧的资产就是为 TTK 准备的：用例 CSV 直接作 `-i` 输入，`tests/assets/` 的 golden.py / input.py 经 `--plugin` 接入。
