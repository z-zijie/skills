---
name: kernel-perf
description: On-device kernel performance collection and analysis for Ascend NPUs (Ascend950PR / dav-3510 included), targeting the information density of NVIDIA NCU — per-core per-pipe occupancy and stalls, every bandwidth path, L2/iCache/DCache behaviour, bank conflicts, MFU/MAC usage, per-block launch timeline, cross-die (SIO) traffic, and instruction-level cost with PC→opcode attribution. Every metric carries an A–E confidence tier calibrated by measured run-to-run variance. Use when asked to profile a kernel, localise an operator's bottleneck, obtain detailed kernel performance metrics, compute MFU, compare two kernel runs, or diagnose multi-core launch skew and tail effects.
---

# Ascend kernel performance collection

Given an executable and a kernel name, collect everything the chip exposes about that kernel, reduce it to a form that can be acted on, and state how far each number can be trusted.

On-device capture only (`msopprof`). No npusim simulation.

## Quick start

```shell
# Standard collection, analysis and validation
python3 scripts/kernel_perf.py profile --app ./vector_add --kernel add_kernel --out ./out

# Add instruction-level cost (slower: a separate pass, 1-4 minutes)
python3 scripts/kernel_perf.py profile --app ./vector_add --kernel add_kernel --out ./out \
    --with-instructions --instr-pipes="vector|mte2|mte3"

# Validate the capture and the report (exit code gates delivery)
python3 scripts/critic.py --out ./out --report ./out/report.md

# Repeatability: quantify per-metric jitter across identical runs
python3 scripts/stability.py --app ./vector_add --kernel add_kernel --runs 5

# Enumerate every metric with its provenance and confidence tier
python3 scripts/metric_catalogue.py

# Compare two captures
python3 scripts/kernel_perf.py diff ./before ./after

# Off-device self-tests (no NPU required)
python3 scripts/selftest.py
```

## Information layers

Metrics are organised by which part of the machine they describe; the report reads in this order, which is also the debugging order.

| Layer | Contents | The question it answers |
|---|---|---|
| **SoC** | Data-movement bandwidth, interconnect, L2, cross-die (SIO), die count | Is the algorithmic shape or total tiling wrong? |
| **Core** | MTE2/MTE3/Cube/Vector/Scalar/FixPipe occupancy, conflict rates, UB/L0/L1 bandwidth, iCache/DCache, VF ratio | Which unit is the bottleneck? |
| **Schedule** | Per-block start/end timestamps, launch skew, tail | Did the cores start together, did any straggle? |

`scripts/layers.py` is the single source of truth for layer assignment; the `layers` field of `kernel_perf.py --json` maps each metric to its layer.

### Schedule layer: block timeline

Ascend950PR carries 56 cores. Each core is one block (one AIC subcore plus two AIV subcores). Collect one extra pass with `PipeTimeline`, then take `min(ts)` and `max(ts + dur)` per `pid` (`core<N>.veccore<M>`) from `trace.json` to obtain each block's first activity and last completion:

```shell
python3 scripts/kernel_perf.py profile --app ./x --kernel k --out ./o \
    --metrics=PipeUtilization,...,PipeTimeline
```

Report section 7 gives:

- **Launch skew** = `last_start - first_start`, as an absolute value and a fraction of kernel duration
- **Tail** = `last_end - first_end`
- The slowest blocks

Measured on vector_add: skew 0.864 us (20.2%), tail 0.753 us (17.6%). Neither is visible from the pipe-level view.

> **Coverage limit**: PipeTimeline instruments only a subset of cores. A 56-block kernel yielded 12 subcores, so the skew is a **lower bound**, not a chip-wide truth. The report always states the covered count next to the number.

### SoC layer: cross-die traffic

The platform ini declares `[DieInfo] io_die_num=2`, so the part is dual-die. L2 is interleaved across dies and remote-die L2 access traverses SIO. Inside `visualize_data.bin`, `memory_unit[]` entries with `memory_path` in {2, 6, 7, 8, 37, 38} appear only on the cube kernel and carry real traffic, so they are attributed to the remote die.

Measured on matmul: 47.05 GB/s cross-die against 30.52 GB/s on-die — **60.6% cross-die**, with path 8 alone at 36.2 GB/s.

> **The `memory_path` ids are undocumented in CANN**, so the names and the cross-die grouping are inferred, and the confidence tier is C. When cross-die traffic is high, the available levers are: map GM addresses to the L2 interleave so each core writes only its local die's cachelines, reduce the number of cores writing the same cacheline, or reduce same-address write participation.

## What is collected

One collection, split across passes where metric sets cannot coexist:

| Layer | Contents | Primary source |
|---|---|---|
| Identity | Kernel name, duration, Block Dim, clock, SoC | `OpBasicInfo.csv` |
| Pipes | Per-core per-pipe busy time, ratio, active bandwidth | `PipeUtilization.csv` |
| Stalls | Per-pipe wait ratio, per-core accumulated wait cycles | `ResourceConflictRatio.csv`, `visualize_data.bin` |
| Conflicts | Store/load bank conflicts, SFU conflicts | `ResourceConflictRatio.csv` |
| Bandwidth | GM↔UB, UB↔GM, main memory, L1, L0A/B/C | `Memory*.csv` |
| Paths | 8+ memory paths with bandwidth and request counts (absent from CSV) | `visualize_data.bin` 0x08 |
| Cache | L2 close/far × hit/miss/victim, iCache, DCache, per-core hit rates | `L2Cache.csv`, `visualize_data.bin` |
| Compute | Cube instruction counts, FP/integer instruction split | `ArithmeticUtilization.csv` |
| Key metrics | MFU, MAC usage, arithmetic intensity, binding constraint | Derived |
| Instructions | Per-opcode cost, per-PC cost, pipe attribution | `trace.json` + `dfx_region_map.txt` |
| Schedule | Per-block start/end, launch skew, tail | `trace.json` (PipeTimeline) |

## Key metrics (section 2)

| Metric | Definition | Reference value on this part |
|---|---|---|
| **MFU** | Achieved TFLOPS ÷ theoretical peak | Peak 378.47 TFLOPS (28 AIC × 1650 MHz × 16³ MAC × 2) |
| **MAC usage** | Cube pipe occupancy, i.e. the fraction of MAC issue slots | Above 70% means the cube is MAC-saturated |
| Arithmetic intensity | FLOPs ÷ main-memory bytes | Combined with peak bandwidth, decides compute- vs memory-bound |

MFU is always tier D: the denominator is a theoretical peak, so it supports comparison, not a claim about "measured compute".

**Do not infer the bottleneck from the operator type.** A measured `cube` matmul spent 0.08% in the cube pipe and 92.5% in AIC scalar. Classification always follows measured occupancy.

## Instruction-level cost

Requires `--with-instructions`. Produces:

- Cost and share aggregated by opcode
- Cost aggregated by pipe
- Hottest instruction sites (PC offset + opcode + pipe)

Method: `args.pc_addr` in `trace.json` is an absolute address; subtracting `pc_start_addr.txt` yields the kernel-relative offset, which aligns with `pc=` in `dfx_region_map.txt` and therefore yields the opcode and pipe owning that instruction.

The report states the **covered fraction** — attributed time as a share of total kernel time. Below 50% it warns explicitly that the breakdown describes the instrumented region, not the whole kernel.

The same trace also drives **PC-vs-cycle icache-miss diagnosis** (the primary technique in `scalar_story`): a run of consecutive events sharing one PC with a gap of at least 0.05 us is treated as waiting on instruction fetch, and the longest-staying PCs are listed. Measured on vector_add: zero such stalls — a small kernel has no icache misses, and the tool reports the honest zero.

## Collection constraints (measured)

- **Collection is split into passes.** `InstrTimeline` and `PipeTimeline` are mutually exclusive; `InstrTimeline` combined with the eight core groups hangs rather than erroring. `plan_passes` performs the split automatically.
- **No more than eight groups per pass.** Eleven groups return success while writing an all-`NA` capture, caught by the `collection_nonempty` check.
- **`--instr-timeline-pipe`** accepts only `cube|fixp|vector|mte1|mte2|mte3`; `scalar` is rejected outright.
- **`dfx_region_map.txt` is deleted by msopprof's later parsing**, so collection archives it into `probe/` immediately.
- **`TimelineDetail` fails outright** on dav-3510.
- **Application-level `msprof` (non-`op`) is unusable on this host**: the bundled Python's `_ctypes` does not match the interpreter, so capture data cannot be parsed. Kernel-level analysis is unaffected.
- **`biprof` is not an on-device tool**: it is a `cannsim` simulator package requiring `plotly`, which is not installed. `msprof op` is itself a wrapper over `msopprof`.

## Confidence tiers

Every metric carries one of five tiers, assigned by how the value is produced and calibrated by measured repeatability. Anything above 10% coefficient of variation is demoted to E automatically.

| Tier | Meaning | Example |
|---|---|---|
| A | Direct counter, reproduces to the last digit | Duration, cycles, cube instruction count, wait cycles |
| B | Exact derivation from tier-A values, no sampling, no model assumption | Pipe ratios, bandwidth, bank conflicts, core imbalance |
| C | Sampled statistic, aliasing risk on short kernels | L2 read hit rate, instruction-level cost, timeline |
| D | Depends on a theoretical constant, not measured | MFU, MAC usage, arithmetic intensity |
| E | Known unstable or meaningless in common cases | **L2 write hit rate** (measured CV 14%) |

Report section 10 lists every E-tier metric in that report and why, so an unstable value cannot be quoted by accident.

## Reading the results

Thresholds in `references/interpretation.md`; measured data in `references/measurements.md`; platform constants in `references/platform-constants.md`; container format and capability boundaries in `references/dav3510-notes.md`; worked examples in `references/example-vector-add.md` and `references/example-three-kernels.md`.

Three rules:

- A dominant pipe above 60% with peak utilisation above 70%: close to the hardware limit, so the gain must come from the algorithm
- No pipe above 50%: pipeline bubbles, so read the wait ratios and check double buffering
- Per-core duration spread above 30%: a tiling problem, unrelated to pipeline tuning
