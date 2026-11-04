# 平台常量快照

工具在采集机上直接读 `$ASCEND_HOME_PATH/x86_64-linux/data/platform_config/<soc>.ini`。
本文件是同一组键的副本，供**不在采集机上的分析**使用（例如把 capture 拷回本地重跑
`kernel_perf.py analyze`）。

只有从 ini 读不到时才会用到这里的值；两者不一致时以 ini 为准，并在报告里注明。

## Ascend950PR_9579（实测，CANN 9.3.0~weekly.20261009.01）

| 键 | 值 | 含义 |
|---|---:|---|
| `NpuArch` | 3510 | 架构号，对应 `--npu-arch=dav-3510` |
| `Short_SoC_version` | Ascend950 | 短名 |
| `ai_core_cnt` | 28 | AIC 数量，cube 计算的并行度上限 |
| `cube_core_cnt` | 28 | 同上（本芯片 AIC 全带 cube） |
| `vector_core_cnt` | 56 | AIV 数量，注意是 AIC 的两倍 |
| `cube_freq` | 1650 | MHz |
| `vec_freq` | 1650 | MHz |
| `cube_m_size` / `cube_n_size` / `cube_k_size` | 16 / 16 / 16 | 单指令 MMA 形状 |
| `vec_calc_size` | 128 | vector lane 数 |
| `l0_a_size` / `l0_b_size` / `l0_c_size` | 65536 / 65536 / 262144 | B |
| `l1_size` | 524288 | B（512 KiB） |
| `ub_size` | 253952 | B（248 KiB） |
| `l2_size` | 134217728 | B（128 MiB） |
| `ddr_rate`（`[AICoreMemoryRates]`） | 31 | 无量纲等级，**不能**当带宽分母 |
| `ddr_rate`（`[VectorCoreMemoryRates]` | 16 | 同上，且与 AICore 段不同 |
| `l2_rate` | 100 | 无量纲等级 |
| `l1_to_ub_rate` / `ub_to_l2_rate` / `ub_to_ddr_rate` | 128 / 128 / 128 | 无量纲等级 |

## 由这些常量推出的理论峰值

```
Cube   = 28 AIC × 1650 MHz × 16×16×16 MAC/cycle × 2 FLOP/MAC = 378.47 TFLOPS
Vector = 56 AIV × 1650 MHz × 128 lane                      =  11.83 TOPS
```

Cube 的 378 TFLOPS 是本工具 MFU 的分母。核对方式：`28 × 1.65e9 × 4096 × 2 = 3.78e14`。

## 为什么 ini 里的 `*_rate` 不能当带宽分母

`ddr_rate=31`、`l2_rate=100` 是无量纲的等级值，不是「每秒多少字节」。把实测 GB/s 除以
它们会得到荒谬的百分比。工具的真实分母从采集数据自身反解（见
`references/measurements.md` 的单位反解一节），ini 值仅在报告里作为参考列出。

## 其他 SoC

同一目录下有 `Ascend950DT_*`、`Ascend910B*` 等 ini。工具按 capture 里的 `soc` 字段
匹配同名 ini，匹配不到就退化为「只用采集数据能推出的信息」，并在报告里标注。
