# Platform constant snapshot

The tool reads `$ASCEND_HOME_PATH/x86_64-linux/data/platform_config/<soc>.ini` directly on the collection host. This file is a copy of the same keys for analysis **off** the collection host (for example, copying a capture back and re-running `kernel_perf.py analyze`).

These values are used only when the ini cannot be read. Where the two disagree, the ini wins and the report says so.

## Ascend950PR_9579 (measured, CANN 9.3.0~weekly.20261009.01)

| Key | Value | Meaning |
|---|---:|---|
| `NpuArch` | 3510 | Architecture number, corresponding to `--npu-arch=dav-3510` |
| `Short_SoC_version` | Ascend950 | Short name |
| `ai_core_cnt` | 28 | AIC count; the parallelism limit for cube work |
| `cube_core_cnt` | 28 | Same as above (every AIC on this part has a cube) |
| `vector_core_cnt` | 56 | AIV count; note this is twice the AIC count |
| `cube_freq` | 1650 | MHz |
| `vec_freq` | 1650 | MHz |
| `cube_m_size` / `cube_n_size` / `cube_k_size` | 16 / 16 / 16 | Shape of one MMA instruction |
| `vec_calc_size` | 128 | Vector lane count |
| `l0_a_size` / `l0_b_size` / `l0_c_size` | 65536 / 65536 / 262144 | Bytes |
| `l1_size` | 524288 | Bytes (512 KiB) |
| `ub_size` | 253952 | Bytes (248 KiB) |
| `l2_size` | 134217728 | Bytes (128 MiB) |
| `ddr_rate` (`[AICoreMemoryRates]`) | 31 | Dimensionless class value; **not** usable as a bandwidth denominator |
| `ddr_rate` (`[VectorCoreMemoryRates]`) | 16 | As above, and different from the AICore section |
| `l2_rate` | 100 | Dimensionless class value |
| `l1_to_ub_rate` / `ub_to_l2_rate` / `ub_to_ddr_rate` | 128 / 128 / 128 | Dimensionless class values |
| `io_die_num` (`[DieInfo]`) | 2 | Dual-die part; L2 is interleaved and remote access traverses SIO |

## Theoretical peaks derived from these constants

```
Cube   = 28 AIC x 1650 MHz x 16x16x16 MAC/cycle x 2 FLOP/MAC = 378.47 TFLOPS
Vector = 56 AIV x 1650 MHz x 128 lane                        =  11.83 TOPS
```

The cube figure is the MFU denominator. Verification: `28 * 1.65e9 * 4096 * 2 = 3.78e14`.

## Why the ini `*_rate` keys cannot serve as bandwidth denominators

`ddr_rate=31` and `l2_rate=100` are dimensionless class values, not bytes per second. Dividing a measured GB/s by them produces nonsensical percentages. The tool inverts the real denominator from the capture itself (see the unit derivation in `references/measurements.md`), and lists the ini values as reference only.

## Other SoCs

The same directory holds `Ascend950DT_*`, `Ascend910B*` and other ini files. The tool matches the ini by the capture's `soc` field and degrades to "only what the capture itself implies" when no match exists, saying so in the report.
