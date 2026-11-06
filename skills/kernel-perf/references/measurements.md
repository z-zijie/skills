# Measurement log (Ascend950PR / dav-3510)

This file holds the raw numbers behind the tool's conclusions. Re-measurements are appended, never overwritten: if a threshold drifts, the cause must be traceable to one specific collection.

Environment: `Ascend950PR_9579`, CANN `9.3.0~weekly.20261009.01`, `cube_freq = vec_freq = 1650 MHz`, 28 AIC / 56 AIV, UB 248 KiB, L1 512 KiB, L0C 256 KiB, L2 128 MiB, `io_die_num = 2`.

## Collection 1 - vector_add (2026-11-04 14:36)

Binary: `cann-samples/build/Samples/0_Introduction/vector_add/vector_add`
Arguments: `msopprof --warm-up=2`, the default 7 metric groups. Kernel `_Z10add_kernelIfEvPhS0_S0_llj`, 409600 float32.

```
Op Type: vector   Task Duration: 9.965 us   Block Dim: 56
Current Freq: 1650   Rated Freq: 1650
```

| Pipe | Mean share |
|---|---:|
| AIV MTE2 | 51.84% |
| AIV scalar | 23.89% |
| AIV MTE3 | 17.74% |
| AIV vec | 8.26% |

- GM->UB measured 28.41 GB/s, `GM_to_UB_bw_usage_rate` mean 17.4%, inverted peak **163.13 GB/s**
- UB read (vector) 20.99, write (vector) 20.50 GB/s
- L2 read hit 0.99%, write hit 59.52%, read close miss total 2365
- iCache hit 100% (per-core table `iCache Total` 141/141)
- Bank conflicts: store 3.26%, load 2.23%, sfu 0%
- Wait: Vector 43.73%, MTE3 49.47%
- Per-core duration 1.453-2.873 us, mean 1.974 us, **spread 71.9%**

Vendor advice: `MTE2 bandwidth utilization lower than 80% when active`, `aivector compute usage lower than 20%`.

## Collection 2 - matmul mxfp4 base (2026-11-04 14:50)

Binary: `matmul_tutorial_mxfp4_base 100 50 200`, kernel `_ZN6Kernel26QuantMatmulMxfp4BaseKernelEmmmPhS0_S0_S0_S0_`.

```
Op Type: cube   Task Duration: 3.051 us   Block Dim: 28
```

| Pipe | Mean share |
|---|---:|
| AIC scalar | 92.51% |
| AIC fixpipe | 2.42% |
| AIC MTE2 | 1.07% |
| AIC MTE1 | 0.36% |
| AIC MTE3 | 0.28% |
| AIC cube | 0.08% |

- Inverted peaks: `GM_to_L1` 174.77 GB/s, `L0C_to_GM` 139.81 GB/s
- L2 read hit 89.34% (cube side)

**Why this sample matters**: it is a "cube operator" whose cube pipe is nearly idle (0.08%), with 92.5% in scalar. Operator type and actual bottleneck are two different things - the report must classify by measured occupancy, never by `op_type`.

## Collection 3 - eleven metric groups merged (failure sample, 2026-11-04 14:43)

`--aic-metrics` given eleven groups at once (core7 + BasicInfo + Roofline + Occupancy + KernelScale):

```
msopprof returns 0, the log prints "All task success"
OpBasicInfo has Task Duration = 6.936 us and Block Dim = 56
but every counter column in all 7 CSVs is NA
```

**This is the most dangerous failure mode**: exit code 0, success in the log, structurally complete CSVs, and `Task Duration` still populated. Only checking for at least one non-zero pipe ratio reveals it. Caught by the `collection_nonempty` check; `roofline.class` is set to `no data (all pipe counters empty)`.

Conclusion: never request more than seven metric groups in one pass.

## Availability of each metric set (2026-11-04 14:37-14:44)

Collected one at a time against the same `vector_add`:

| Metric | rc | Time | Result |
|---|---:|---:|---|
| Default | 0 | 22s | the full set of 7 CSVs |
| BasicInfo | 0 | 35s | OpBasicInfo.csv only |
| Roofline | 0 | 35s | 7 CSVs + a `0x0D` roofline record (`latency bound:pipeline caused`) |
| Occupancy | 0 | 37s | OpBasicInfo + visualize_data (with per-core advice) |
| KernelScale | 0 | 35s | 7 CSVs |
| Source | 0 | 36s | requires `-g`; otherwise "Failed to parse pc to code" |
| PCSampling | 0 | 37s | as above; hot-spot computation fails |
| MemoryDetail | 0 | 10s | 7 CSVs, but Task Duration 4.311 us is clearly anomalous (different replay semantics) |
| TimelineDetail | 0 | 24s | **fails**: "Parse kernel data failed", all CSVs empty |
| PipeTimeline | 0 | 45s | `trace.json`, 618 `X` events, only `core1.veccore0` |
| InstrTimeline | 0 | 55s | `trace.json`, 71 events (GET_BUF / RLS_BUF / MOV_UB_TO_OUT_ALIGN_V2) |

`PipeTimeline`'s trace contains accumulated SCALAR/MTE2/VECTOR/MTE3 for a single core (42.4 / 22.0 / 4.5 / 9.0 us), not a whole-chip timeline - the report must state which core was captured.

## Identity checks (recomputed by `critic.py` on every run)

| Check | Measured result |
|---|---|
| `ratio == time/total_time` | 224 pairs, 0 mismatches (tolerance 1e-3) |
| `cycles/time == freq` | 56 pairs, max deviation 0.0 MHz |
| KiB->GiB bandwidth conversion | 56 pairs, max relative error **3.06e-07** |
| Recovered peak round-trip against vendor % | 56 times, max absolute error **3.55e-15** percentage points |
| Cross-table `aiv_time` agreement | 280 values, 0 mismatches |
| visualize core count vs CSV rows | 56 vs 56 |
| Sum of pipe time / core time | max 1.073 (pipes may overlap, so above 1 is allowed) |

Every maximum error is on the order of 1e-7, which means these are genuine identities rather than approximations and can be used as assertions.

## Unit derivation (kept for the record)

First row of `Memory.csv`: `aiv_time=2.267879 us`, `GM_to_UB_datas(KB)=57.375`, `aiv_gm_to_ub_bw(GB/s)=24.126980`, `GM_to_UB_bw_usage_rate(%)=14.790032`.

| Assumption | Computed GB/s | Ratio to the published value |
|---|---:|---:|
| kB=1000 B, GB=1e9 | 25.2990 | 1.0486 |
| kB=1024 B, GB=1e9 | 25.9061 | 1.0737 |
| **kB=1024 B, GB=1024^3** | **24.1270** | **1.000000** |
| kB=1000 B, GB=1024^3 | 23.5615 | 0.9766 |

Only KiB + GiB reproduces the value exactly. Using GB=1e9 shifts every share-of-peak by 4.9%, enough to misread "headroom remains" as "close to the limit".

## Configuration search (collect_optimize.py, 2026-11-04 15:03)

Six candidate configurations against the same `vector_add`. Reward = passes - 3 x failures + coverage/1000 (capped at 5), with the critic as the reward model.

| Configuration | replay | launch | reward | pass | fail | coverage |
|---|---|---|---:|---:|---:|---:|
| **core7+roofline** | kernel | 1 | **18.87** | 15 | 0 | 3874 |
| core7 | kernel | 1 | 18.86 | 15 | 0 | 3864 |
| core7-launch5 | kernel | 5 | 18.86 | 15 | 0 | 3864 |
| core7+occupancy | kernel | 1 | 18.86 | 15 | 0 | 3864 |
| core7+timeline | kernel | 1 | 18.86 | 15 | 0 | 3864 |
| core7-appreplay | **application** | 1 | - | - | - | empty capture |

Three conclusions:

1. **`--replay-mode=application` collects nothing on this host** (returns empty in 0 seconds). It cannot be used as the fallback for "kernel replay failed", at least not on dav-3510 with CANN 9.3.0. `kernel_perf.py` therefore keeps `kernel` as its default.
2. `core7+roofline` wins on coverage, 3874 against 3864: one extra collection (about 11 s) buys the vendor Roofline verdict and the 8 `memory_path` entries. It is now the tool's default metric set.
3. `launch5` (averaging five launches) has exactly the same coverage and the same runtime (11 s) as a single launch - repeated launches add no fields, they only smooth the values. The default stays at one launch; add more when stability matters.

The device carried 1-2 other processes during this run and all 15 critic checks still passed, which shows the identities are insensitive to counter jitter (they are arithmetic relations, not threshold judgements). That is precisely why validation uses identities rather than "expected ranges".

## Stability measurement (stability.py, 2026-11-04 15:5x, 3 repeats)

vector_add, `--warm-up=5`, `launch-count=3`, 3 independent collections:

| Metric | Mean | CV% | Range % | Verdict |
|---|---:|---:|---:|---|
| Core busy time | 3.0993 us | 0.31 | 0.61 | stable |
| MTE2 occupancy | 61.21% | 0.69 | 1.34 | stable |
| Vector occupancy | 5.19% | 0.21 | 0.42 | stable |
| Scalar occupancy | 25.26% | 2.41 | 4.75 | stable |
| GM->UB bandwidth | 17.8484 GiB/s | 0.20 | 0.41 | stable |
| L2 read hit | 0.6248% | 0.00 | 0.01 | stable |
| **L2 write hit** | 76.9353% | **9.57** | **18.78** | usable (near threshold) |
| Vector wait | 60.62% | 2.22 | 4.26 | stable |

Two independent measurements (5 runs and 3 runs) agree on one thing: **the L2 write hit rate is the only jittery metric** (CV 14.4% and 9.6%), everything else stays below 3%. This is what justifies tiering `l2_write_hit` as E and `l2_read_hit` as C - inside the same CSV table, the read side and the write side have completely different trustworthiness.

## Metric coverage (2026-11-04 15:3x)

CANN's own `AI_CORE_CALCULATION_FORMULA` in `analysis/common_func/constant.py` defines **74** computable metrics. After a default 7-group `msopprof` collection:

| Scope | Count |
|---|---:|
| Total fields actually exposed by the CSVs | 122 |
| Of those, named metrics mapping onto a CANN definition | **7 / 53** |
| Extra rows obtained from `visualize_data.bin` | 34 (UB four-direction bandwidth, 8 `memory_path` entries, per-core L2, wait cycles...) |

Typical unreachable items: `vec_bankgroup_cflt_ratio`, `mte*_iq_full_ratio`, `control_flow_prediction_ratio`, `acess_stack_ratio`. None has an output path under any `--aic-metrics` value - the tool does not expose them, which is a tooling gap rather than a collection failure, and the report says so instead of guessing a value.

## Instruction-level cost (2026-11-04 15:36)

`--aic-metrics=InstrTimeline --instr-timeline-pipe="vector|mte2|mte3"`, vector_add (built with `-g`):

```
28 probe sites, 97 trace events, 1.423 us attributed (kernel 4.2 us, 34% covered)
```

Aggregated by opcode:

| Opcode | Accumulated us | Share |
|---|---:|---:|
| `MOV_UB_TO_OUT_ALIGN_V2` | 1.3248 | 93.1% |
| `GET_BUF` | 0.0254 | 1.8% |
| `RLS_BUF` | 0.0254 | 1.8% |

Aggregated by PC, the hottest site is `MOV_UB_TO_OUT_ALIGN_V2` at `0x824` - the write-back transfer. This complements the pipe-level conclusion that MTE2 dominates: MTE2 dominates **reads**, while the write side concentrates its cost in this one instruction.

## Why collection is split into passes (2026-11-04 15:4x)

| Combination | Result |
|---|---|
| `InstrTimeline,PipeTimeline` | immediate error: `'PipeTimeline' and 'InstrTimeline' cannot be specified together` |
| `InstrTimeline` + the 8 core groups | **hangs** (no error, no output for a long time, eventually killed) |
| `InstrTimeline` alone | normal, about 1-4 minutes, producing a 25 MB `timeline.bin` + 189 MB `OperandRecord.bin` |

The tool therefore splits automatically: one pass for the core groups, one for `InstrTimeline`, one for `PipeTimeline`.

## Lifecycle of dfx_region_map.txt

`dfx_region_map.txt` (pc to pipe/opcode) is written by `msopprof` during collection, but **its later parsing step deletes it**. Measured: the same capture has the file right after collection and it is gone a few minutes later. The tool copies it and `pc_start_addr.txt` into `capture/probe/` immediately after `run_msopprof` returns, and instruction-level analysis reads that copy first.

## Dual die and cross-die paths (2026-11-04 16:3x)

`platform_config` declares `[DieInfo] io_die_num=2`, confirming that Ascend950PR is dual-die. L2 is interleaved across dies, so reaching the remote die's L2 traverses SIO.

`visualize_data.bin`'s `core_memory_map[].memory_unit[]` exposes different `memory_path` sets depending on the kernel:

| Scenario | path ids |
|---|---|
| Vector kernel (vector_add) | 0, 1, 12, 13, 14, 15, 48, 52, 54 |
| Cube kernel (matmul mxfp4) | 0, 1, 2, 6, 7, 8, 37, 38, 39, 40, 41 |

Measured on the cube kernel:

| path | Bandwidth GB/s | Requests | Attribution |
|---:|---:|---:|---|
| 8 | 36.188 | 91 | cross-die (inferred) |
| 40 | 24.208 | 487 | GM read (cube side) |
| 2 | 3.330 | 67 | cross-die (inferred) |
| 0 | 3.281 | 66 | GM read (local die) |
| 1 | 3.032 | 61 | GM write (local die) |
| 38 | 2.585 | 26 | cross-die (inferred) |
| 6 | 2.262 | 91 | cross-die (inferred) |
| 37 | 1.392 | 14 | cross-die (inferred) |
| 7 | 1.292 | 13 | cross-die (inferred) |
| 39, 41 | 0.000 | 0 | always 0, reserved |

Cross-die totals 47.05 GB/s against 30.52 GB/s on-die - **60.6% cross-die**.

On the vector kernel this group of cross-die paths carries almost no traffic (below 3.3 GB/s), so the pattern depends on the access pattern rather than being fixed chip behaviour.

**CANN documents none of the path ids.** The naming rests on: 0/1 matching the main GM<->UB traffic; 12-15 matching the per-core `UB` table's four directions byte for byte; 39/41 always 0 in every capture; and 2/6/7/8/37/38 appearing only on the cube kernel with real traffic. Those names are therefore **inferred**, and the tool assigns tier C rather than A.

## Availability of sector / coalescing / outstanding / snoop

Searched `msopprof`, `libmsprofiler.so` and every profiler plugin by strings:

| Concept | Conclusion |
|---|---|
| `sector` / coalescing efficiency | no counter and no string anywhere |
| `outstanding` / `inflight` | zero hits across the whole `visualize_data.bin` |
| `snoop` | none |
| `instr_popped` (Load/Store share) | **exists in msopprof**, but `--dump=true` requires `--aic-metrics=TimelineDetail`, and that fails on dav-3510 with `Unexpected argument --dump, maybe in wrong soc platform` |

## Observation-by-observation comparison against the stories

After reading `cann-samples/Samples/2_Performance/scalar_story` and `simd_vf_story`, each observation they rely on was checked against this tool:

| Observation in the story | This tool | Note |
|---|---|---|
| Per-pipe cycle distribution | yes | PipeUtilization |
| Scalar share | yes | scalar_ratio |
| icache miss (PC-vs-cycle plot) | partly | drawable from InstrTimeline's pc_addr + ts; needs `--with-instructions` |
| dcache miss | yes | DCache table (added in this work) |
| Load/Store instruction share | no | needs the instr_popped dump, unavailable on-device |
| Register spill | no | as above; would be inferred from the Load/Store share |
| Software head overhead (cycles) | partly | only indirectly, via scalar time |
| VF count / PUSHQ VF cycles / RVECEX / RVECLD / RVECSU / PUSH_PB | no | cannsim traces only; on-device there is just the `aiv_vec_vf_ratio` summary |
| MTE2/MTE3 sum_cycles | yes | already collected |
| Bank conflicts (vf_data_transform_story) | yes | ResourceConflictRatio |

## PC-vs-cycle (icache-miss diagnosis) measurement (2026-11-04 16:4x)

InstrTimeline's `trace.json` carries both `args.pc_addr` and `ts`, the two fields scalar_story's primary technique needs. `layers.analyse_pc_cycle()` treats "the same PC appearing consecutively with a gap at or above the threshold" as residence, the equivalent of a horizontal segment on a PC-vs-cycle plot.

vector_add (built with `-g`, 71 events, 8 distinct PCs, span 0.7115 us):

```
stalls = 0, total_stall_us = 0.0, stall_pct = 0.0
```

**That is the honest result**: the kernel is short with a small code footprint, so it has no icache misses. The tool reports zero rather than manufacturing a finding. The detector's own correctness is covered by a synthetic trace (same PC, 0.4 us gap) yielding one residence covering 80% of the span, in `selftest.py`.

The threshold is an absolute 0.05 us rather than a multiple of event duration: instrumented `dur` values are on the order of nanoseconds, so a ratio test would classify every adjacent pair as a residence.

## Raw pipeline waveform (2026-11-06)

`msprof op --aic-metrics=PipeTimeline` writes `dump/timeline.bin.0` alongside
`trace.json`. The decoder in `references/pipeline_waveform.py` reads the raw
stream; `--verify` checks it against the vendor's own `trace.json` for the run.

Five captures, CANN 9.3.0, Ascend950PR_9579, 1650 MHz. The last row is a fresh
collection made by `kernel_perf.py profile --with-timeline`, so it exercises the
collector as well as the decoder:

| Capture | Kernel | Records | Verify checks | Result |
|---|---|---:|---:|---|
| vector_add | `add_kernel`, 56 blocks, AIV | 12 | 48 | pass |
| vector_add (2nd) | `add_kernel`, 56 blocks, AIV | 4 | 16 | pass |
| matmul 1024 | `MatmulKernel`, 28 blocks, AIC | 6 | 20 | pass |
| matmul 128 | `MatmulKernel`, 28 blocks, AIC | 6 | 10 | pass |
| end-to-end | `add_kernel` via the collector | 12 | 48 | pass |

142/142 exact interval matches. The container is a fixed 524290-word stride, and
the raw file is 25 MB for a 4 us kernel -- almost all of it zero padding, which
is worth knowing before keeping captures around.

### Collection stability: 3 of 10 identical captures lost the stream

Ten consecutive identical KDA collections (`kimi_delta_attn_lite_prepare_k`,
1C2V, `msprof op --aic-metrics=PipeTimeline,PipeUtilization --warm-up=2`):

| Outcome | Count |
|---|---:|
| raw stream present, decoded to samples | 7 |
| **exit 0, `OpBasicInfo.csv` written, no `timeline.bin.0`** | **3** |

The three failures are silent: rc=0, `[INFO] Profiling running finished. All
task success.`, `[INFO] Save profiling data with metric [PipeUtilization]
success.`, and a `visualize_data.bin` 10 776-10 808 bytes instead of 14 076-14 384.
They carry no `0x02` (trace) record and no stream, so nothing in the capture
records that anything went wrong.

The retry itself is **not** demonstrated by this data. A separate probe of two
further identical runs produced a stream both times, but at a ~70% base rate two
successes say nothing about whether re-running recovers a *specific* failure --
that would need a run that failed and then succeeded, which did not occur in the
probe. So `--with-timeline` retries (`--waveform-retries`, default 2) as a cheap
mitigation with an honest cost bound, not as a verified repair, and when the
budget is exhausted section 15 says the stream was not collected instead of
omitting the section. This is the one place where "no waveform" must never be
left to read like "no waveform requested".

### KDA stability across the 7 complete captures

`kimi_delta_attn_lite_prepare_k`, 1C2V mix kernel, 1650 MHz. Every run sampled
the same shape: 3 cores, 5 windows. Shares are the mean of per-core means.

| Pipe | Mean % | SD | CV | Min | Max |
|---|---:|---:|---:|---:|---:|
| VECTOR | 60.84 | 0.71 | 1.2% | 59.75 | 61.63 |
| SCALAR | 17.74 | 0.67 | 3.8% | 17.19 | 18.71 |
| MTE3 | 15.97 | 0.41 | 2.6% | 15.13 | 16.31 |
| CUBE | 5.52 | 0.09 | 1.6% | 5.35 | 5.59 |
| FIXP | 4.22 | 0.07 | 1.6% | 4.16 | 4.35 |
| MTE2 | 3.14 | 0.10 | 3.3% | 3.05 | 3.33 |
| MTE1 | 2.27 | 0.04 | 1.6% | 2.20 | 2.30 |

The **serial-vs-overlapped verdict for MTE2 vs VECTOR was "Never overlap" in all
7 captures** -- identical to the character, not merely close. So the percentages
move by a few percent between runs while the qualitative conclusion does not:
repeat a capture for the shares, but a single capture is enough to trust the
ordering verdict.

### vector_add, first sampling group

| Pipe | Share | Idle (mean) | Worst core idle |
|---|---:|---:|---:|
| SCALAR | 34.9% | 2.04% | 11.62% (`core4.veccore1`) |
| MTE2 | 30.9% | | |
| MTE3 | 11.6% | | |
| VECTOR | 7.4% | | |

SCALAR is 4.7x VECTOR. This matches the CSV-based collection 1 (scalar 23.9% vs
vec 8.3%) in shape, though the waveform numbers are per-core samples and the CSV
numbers are block averages -- the two are not expected to agree digit for digit.

### MatmulKernel (cube), first sampling group

| Pipe | Share |
|---|---:|
| CUBE | 47.8% |
| MTE2 | 39.5% |
| MTE1 | 11.8% |
| SCALAR | 5.7% |
| FIXP | 3.4% |

CUBE and MTE2 overlap across the whole body: a healthy double-buffered matmul.

### The vendor's two outputs disagree on cube kernels

For the same `MatmulKernel` launch, `trace.json` and `PipeUtilization.csv`
report different AIC cycle counts (three of five pipes off by more than the
CSV's rounding):

| Pipe | `trace.json` | `PipeUtilization.csv` x 1650 |
|---|---:|---:|
| CUBE | 646 | 512 |
| FIXP | 441 | 633 |
| MTE2 | 842 | 1307 |
| SCALAR | 971 | 683 |
| MTE1 | 283 | 284 |

Only block 0 of that CSV carries non-zero AIC time; the other 27 blocks report
zero. The decoder is validated against `trace.json`, because that file is
decoded from the same sampled records as the raw stream. Cube-side cycle counts
from the CSV are not a usable cross-check.

### The raw stream needs `msprof op`, not the bare `msopprof`

Same binary, same kernel, same `--aic-metrics=PipeTimeline`:

| Entry point | Files written | `dump/timeline.bin.0` |
|---|---:|---|
| `tools/msopprof/bin/msopprof ...` | 11 | **absent** |
| `msprof op ...` | 12 | 25,165,920 B |

The direct binary accepts every flag and returns 0, and its `trace.json` is
complete enough to look like a successful pipeline collection -- the omission is
silent. `kernel_perf.py` therefore runs its `PipeTimeline` pass through
`msprof op`.
