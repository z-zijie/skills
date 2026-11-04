# Worked example: vector_add from cann-samples

This is not an illustration. It is the actual run recorded on Ascend950PR on 2026-11-04; the command, the output and the artifact layout are transcribed from it, and the numbers can be re-checked.

## Target

```
/workspace/cann-samples/build/Samples/0_Introduction/vector_add/vector_add
kernel: _Z10add_kernelIfEvPhS0_S0_llj   (--kernel add_kernel is a substring match)
```

## One command

```shell
python3 scripts/kernel_perf.py profile \
    --app /workspace/cann-samples/build/Samples/0_Introduction/vector_add/vector_add \
    --kernel add_kernel \
    --out ./demo \
    --metrics=PipeUtilization,ArithmeticUtilization,Memory,MemoryUB,MemoryL0,L2Cache,ResourceConflictRatio,Roofline,PipeTimeline
```

`PipeTimeline` is mutually exclusive with the core groups, so the tool splits the request into two passes automatically: the eight core metric sets first, then `PipeTimeline` alone. The log:

```
[warn] 2 other processes are using the device; counters may be noisy
[info] 2 passes required (these metric sets cannot be merged) -> ./demo/capture
[info]   pass 1: ['PipeUtilization', 'ArithmeticUtilization', 'Memory', 'MemoryUB',
                   'MemoryL0', 'L2Cache', 'ResourceConflictRatio', 'Roofline']
[info]   pass 2: ['PipeTimeline']
```

## What is produced

Four artifacts with different purposes:

| Artifact | Content | Used by |
|---|---|---|
| `report.md` | 13-section human-readable report, every metric tiered | a person reading conclusions |
| `metrics.json` | full structured data including per-core raw values | agents and further analysis |
| `capture/pass0_core/OPPROF_*/` | the raw msopprof CSVs + `visualize_data.bin` | audit, archive |
| `capture/pass1_pipetimeline/` + `capture/extra/` | `trace.json` and `probe/` (region map) | block timeline, instruction level |

It is not CSV or JSON: **the raw CSVs are retained**, and the report and JSON are computed on top of them.

## What was actually reported

### Verdict (section 1)

```
Duration 4.285 us, Block Dim 56, clock 1650 MHz
Roofline class: memory bound (dominant pipe aiv_mte2 at 60.15%)
Vendor verdict: latency bound:pipeline caused
Max bandwidth share of peak: 10.9%
```

### Key metrics (section 2)

| Metric | Value | Note |
|---|---:|---|
| MFU | n/a | no cube instruction; does not apply to a vector kernel |
| MAC usage | n/a | the cube pipe was idle |
| Binding constraint | aiv_mte2 | MTE2 at 60.1%, the busiest unit |
| Cube theoretical peak | 378.47 TFLOPS | 28 AIC x 1650 MHz x 16^3 MAC x 2 |

MFU reports `n/a` rather than 0: a vector kernel has no matrix multiply, and 0 would be misread as "extremely inefficient".

### Pipes (section 3)

| Pipe | Mean occupancy |
|---|---:|
| AIV MTE2 (GM->UB) | 60.15% |
| AIV scalar | 26.27% |
| AIV MTE3 (UB->GM) | 11.86% |
| Vector compute | 5.16% |

Transfer dominates and vector compute is only 5% - the typical form of an elementwise kernel at large shape: the bottleneck is moving data, not computing it.

### Block timeline (section 7, schedule layer)

This is the "multi-core launch skew / tail" layer:

```
Block timelines collected for 12 blocks (source trace.json (PipeTimeline))
First block started at 0.000 us, last at 0.864 us
```

| Metric | Value | Meaning |
|---|---:|---|
| Launch skew | 0.864 us (20.2%) | only part of the chip works during this interval |
| Tail | 0.753 us (17.6%) | gap between the earliest and latest block to finish |
| Block duration range | 1.181-1.839 us | spread 45.0% |

**The key point**: launch skew is 20% of kernel duration, and that loss is unrelated to pipeline tuning. Only 12 subcores of 56 blocks were captured (PipeTimeline instruments a subset), so this is a lower bound, and the report states that explicitly.

### Memory paths and cross-die (section 9)

On vector_add, four of the eight paths (12/13/14/15) match the UB four-direction bandwidth byte for byte, and 0/1 are GM read/write. Cross-die paths all sit below 3.3 GB/s.

On the cube kernel (matmul) the picture is different: cross-die paths account for 60.6%, with path 8 alone at 36.2 GB/s. The same tool and the same field reveal the structural difference in data movement between the two operator classes.

## Collection coverage (measured)

| Category | Obtained here | Note |
|---|---|---|
| Pipe occupancy | yes, 4 pipes | MTE2/scalar/MTE3/vector |
| Bandwidth | yes, 8 paths + share of peak | inverted peak 163 GB/s |
| Cache | yes: L2 read/write hit, iCache, DCache | write hit has CV 14% jitter |
| Conflicts | yes: bank conflicts + per-pipe wait | |
| Block timeline | yes, 12 of 56 cores | launch skew, tail, per-block duration |
| Instruction level | requires `--with-instructions` | separate pass, 1-4 minutes |
| Cross-die | yes, per-path bandwidth | path attribution is inferred |

## What is not obtainable (stated alongside)

| Item | Reason |
|---|---|
| Sector / coalescing efficiency | CANN exposes no such counter |
| Outstanding / snoop | no output path under any metric set |
| Load/Store instruction share | needs `--dump=true`, which requires `TimelineDetail`, which fails on dav-3510 |
| VF internals (PUSHQ/RVECEX/...) | only in cannsim simulator traces; this skill is on-device only |
| Timeline for all 56 cores | PipeTimeline instruments a subset |

All of these appear in the report and in `metric_catalogue.py` marked `unavailable`, rather than being estimated.
