# 原始数据说明

本目录的 CSV 体积较大（约 99MB），**没有提交进仓库**；仓库里只保留聚合后的
`../points-summary.csv` 与 `../msprof-kernel.csv`。需要复跑时按主报告 §4 生成。

| 文件 | 内容 |
|---|---|
| `points1.csv` / `points2.csv` / `points3.csv` | 三次独立复现的原始时间戳，每次 88,480 行（79 个数据点 × 20 次发射 × 56 核）。**主结论只用这组。** |
| `points{1,2,3}.json` / `.txt` | `exp/aggregate.py` 对上面三份的聚合结果 |
| `prof_summary.csv` | msprof 逐次发射的 kernel 时长与 AIV pipe 分解 |
| `pipe_ext.csv` | 双缓冲预取模式在大 n 上的扩展测量（n 到 1024） |
| `sweep2.csv` / `sweep3.csv` | **协议陷阱样本**：同进程顺序扫描，结论不可用，仅作反例保留 |

列定义（`points*.csv`）：
`launch,idx,point,mode,nops,copybytes,tiles,blocks,core,t_begin,t_issue1,t_end1,t_issue2,t_end2`

`mode`：0=单条 100KB 对照，1=两条 100KB，2=剂量扫描，3=双缓冲预取 K=8，
4=单条 200KB，5=三条 72KB，6=纯 noop 阶梯（时钟标定）。

派生量：`T1 = t_end1 - t_begin`，`W2 = t_issue2 - t_issue1`，
`SPAN = t_end2 - t_begin`。时间单位是 `SYS_CNT` tick，1 tick = 0.998 ns。

## MTE2 建模章节的额外产物

| 文件 | 内容 |
|---|---|
| `m2_train.csv` | 建模训练网格 33 点 × 40 次发射 × 56 核 |
| `m2_valid.csv` | 留出验证 12 点（不在训练网格上） |
| `bsweep.csv` | 核数细扫 18 个 B × K=1,2 |
| `../mte2-points-summary.csv` | 上述三份的逐点聚合（C_min / C_med），随仓库提交 |
| `../MTE2建模报告.html` | 建模现状报告 |

列：`launch,core,t_begin,t_issue0..7,t_done0..7,K,S,B`，时间单位 `SYS_CNT` tick ≈ 1.00 ns。

## 硬件计数器（§7）

| 文件 | 内容 |
|---|---|
| `hwclean.csv` / `../hw-counters.csv` | 17 个配置 × 4 次发射的原始计数器：`l2_miss`、`aiv_total_cycles` |
| `../hw-result.json` | 拟合结果与 CV |

关键：`aiv_read_local_l2_miss` 在冷读下**恰好等于 K·B·S/256**，即拆包尺寸 256 字节。
因它是事务计数而非时间，跨发射 CV 只有 1.2–4.2%，远优于墙钟时间的 7–14%。
