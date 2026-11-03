---
name: logbook
description: 研究台账的建立与读写纪律：一实验一行、预注册先行、崩溃照记。当研究系列初始化要建实验记录，或循环中读上轮结果、落本轮结果、暂停恢复研究时使用。
---

# 台账

台账是研究程序的持久记忆。上下文会断、agent 会换，台账不换。一行就是一轮实验：跑之前预注册，跑完补结果。暂停、恢复、交接都只凭它。

## 台账文件

- `results.tsv`，放在研究工作目录（用户指定优先）。
- 用 TSV 不用 CSV：描述里有逗号会碎。
- 默认不进 git（branch 的提交历史就是状态）；要给人审或交接时再提交。

## 列定义（单一事实来源）

```
id	hypothesis	change	before	after	delta	noise_floor	status	gate	lesson
```

| 列 | 内容 |
| --- | --- |
| id | 轮次，r001 递增 |
| hypothesis | 机制句原文（预注册时写好，含预期方向与量级） |
| change | commit 短哈希 + 一句话改动 |
| before / after | 主指标值，按 harness 定义的位数 |
| delta | after − before，带符号 |
| noise_floor | 本轮参照的噪声底（来自 harness 冻结值） |
| status | keep / discard / crash |
| gate | 回归门结果：pass / fail / n.a. |
| lesson | 一句话：下一轮试什么，或为什么失败 |

## 读写纪律

- **跑前读**：每轮开始先读全表。防重复实验，找 near-miss（差一点成立的轮次，组合的原料）。
- **预注册**：跑之前建行，id / hypothesis / change 填好，after / delta / status 留空；跑完才许填。跑完回头改预测是事后诸葛，禁止。
- **跑后即写**：一轮一落盘，不攒批。crash 行 after / delta 记 0，status 写 crash。
- **恢复**：读台账最后一行 + `git log`，就能继续，不依赖对话历史。

完成判据，逐项核对：

- [ ] 每轮实验在台账有且仅有一行
- [ ] 预注册行在实验运行前已存在（after 为空）
- [ ] status 三态之一，无空值
- [ ] 暂停后只凭台账与 git 可恢复
