# Worked example: three representative kernels

Recorded on Ascend950PR (CANN 9.3.0~weekly) on 2026-11-04 against three representative kernels from `cann-samples`. Commands and numbers are transcribed from the actual runs.

These three were chosen because they land in three different roofline classes, which demonstrates that the report's conclusion comes from measurement rather than from the operator type.

## 1. One command

```shell
python3 scripts/kernel_perf.py profile \
    --app /workspace/cann-samples/build/Samples/0_Introduction/vector_add/vector_add \
    --kernel add_kernel \
    --out ./out
```

`--app` may carry program arguments (matmul needs `100 50 200`, written as `--app "./mm 100 50 200"`). `--kernel` is a substring match; omit it to collect the first kernel that matches.

## 2. What it produces

```
out/
├── report.md          # 13-section human-readable report, every metric tiered A-E
├── metrics.json       # full structured data, including per-core raw values
├── collect.log        # collection log (msopprof output for each pass)
└── capture/
    ├── pass0_core/OPPROF_*/     # raw CSVs + visualize_data.bin
    ├── pass1_*/                 # additional metric passes when needed
    ├── probe/                   # region map / base address rescued from dump
    └── OPPROF_merged -> pass0_core/OPPROF_*
```

The raw CSVs are always retained; `report.md` and `metrics.json` are computed from them. The two are not alternatives.

## 3. Results for the three kernels

| Sample | Kernel | Duration | Block Dim | Roofline class | Dominant pipe |
|---|---|---:|---:|---|---|
| vector_add | `add_kernel` | 4.314 us | 56 | **memory bound** | AIV MTE2 61.15% |
| KDA | `kimi_delta_attn_lite_prepare_k` | 17.116 us | 1 | **latency bound** (bubbles) | Vector 49.34% |
| matmul | `QuantMatmulMxfp4BaseKernel` | 2.992 us | 28 | **scalar bound** | AIC scalar 92.43% |

### vector_add - transfer-bound

```
MFU n/a (vector kernel issues no cube instruction)
MAC usage n/a
Binding constraint aiv_mte2 at 60.1%
Max bandwidth share of peak 10.9%
```

A textbook elementwise kernel: data movement dominates and vector compute is only 5%. MFU reports `n/a` rather than 0, because there is no matrix multiply at all, and 0 would read as "extremely inefficient".

### KDA - check Block Dim = 1 first

```
Duration 17.116 us, Block Dim 1     <- the key line
Roofline: latency bound (dominant pipe vec only 49.34%)
MFU 0.0003%, MAC usage 0.24%
Arithmetic intensity 0.215 FLOP/B
```

`Block Dim = 1` means this capture landed on the small **prepare-phase kernel**, using one core, not the main compute kernel. The report classifies it as latency bound instead of forcing a "compute bound" label, because no pipe exceeds 50%.

This is exactly why classification follows measured occupancy rather than operator type: an operator whose `op_type` is mix would have been labelled cube-bound, which would be completely wrong here.

(To capture KDA's main compute kernel, match its real kernel name with `--kernel`, or use `--launch-count` / `--replay-mode` to target the intended launch.)

### matmul - a cube operator that is not cube-bound

```
Duration 2.992 us, Block Dim 28
Roofline: scalar bound
AIC scalar 92.43%, cube only 0.08%
MFU 0.0007%, MAC usage 0.08%
Arithmetic intensity 0.110 FLOP/B
```

**A cube operator whose cube is almost idle**: at a small shape the time goes to scalar preparation - tiling arithmetic, addressing, loop control. This mismatch between operator type and actual bottleneck is visible only from measured occupancy.

## 4. How to read each metric

Report section 1 gives the verdict; the rest is organised by layer:

| Layer | Sections | What to look at |
|---|---|---|
| - | 2. Key metrics | MFU / MAC usage / arithmetic intensity / binding constraint |
| SoC | 4. Bandwidth, 5. Cache, 9. Cross-die | Is transfer saturated, L2 hit rate, cross-die share |
| Core | 3. Pipes, 6. Conflicts and wait, 12. Instructions | Which unit is busy, what it waits on, hottest instructions |
| Schedule | 7. Block timeline, 8. Core-to-core load | Launch skew, tail, tiling evenness |

Confidence tiers (section 10): A direct counter / B exact derivation / C sampled / D theoretical constant / E unstable. Check this column before quoting a number - for example the L2 write hit rate is tier E (measured CV 14%) and cannot be quoted alone.

## 5. Reproducing

```shell
# vector_add
python3 scripts/kernel_perf.py profile \
    --app $SAMPLES/build/Samples/0_Introduction/vector_add/vector_add \
    --kernel add_kernel --out ./va

# KDA (build first)
cmake --build $SAMPLES/build --target kimi_delta_attn_lite_story -j 8
python3 scripts/kernel_perf.py profile \
    --app $SAMPLES/build/Samples/2_Performance/kimi_delta_attn_lite_story/kdalite_v0 \
    --out ./kda

# matmul (with program arguments)
python3 scripts/kernel_perf.py profile \
    --app "$SAMPLES/build/Samples/2_Performance/matmul_story/matmul_tutorials/0_naive/matmul_tutorial_mxfp4_base 100 50 200" \
    --out ./mm
```

The KDA binary depends on `libplatform.so`, so the CANN environment must be loaded first; the tool sources `set_env.sh` itself.
