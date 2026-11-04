# Reading the results

Every threshold below comes from measured samples on Ascend950PR (dav-3510); the raw numbers behind each one are in `references/measurements.md`. Read in three passes, in this order.

## Pass 1: confirm the data is valid

A collection can fail while `msopprof` still returns 0. Any of the following means fix the collection before reading metrics:

| Symptom | Meaning | Action |
|---|---|---|
| All `*_ratio` cells are `NA` or blank | Replay never reached the kernel | Reduce the number of `--metrics` groups, or change `--replay-mode` |
| `Block Dim` far below the chip core count (e.g. 1) | Tiling covered only one block, or the kernel did not spread across cores | Inspect the tiling; this is not a performance question |
| `Current Freq` well below `Rated Freq` | Throttling, likely thermal or power-limited | Re-collect later; confirm the clock matches when comparing runs |
| The same kernel differs by more than 15% between collections | Another workload is on the device | Confirm exclusive access with `npu-smi info` |

The tool states this explicitly: when `roofline.class` is `no data (...)`, the capture is empty rather than the kernel being idle.

## Pass 2: locate the dominant pipe

`pipes[].mean_ratio_pct` is the per-pipe occupancy averaged across cores. Take the largest:

| Dominant pipe | Meaning | Check first |
|---|---|---|
| `vec` above 60% | Vector unit saturated | Peak utilisation above 70% means the limit; otherwise check bank conflicts and casts |
| `cube` above 50% | Cube saturated | Read `arithmetic.cube_fp_ratio`; confirm the operator uses the intended precision |
| `mte2`/`aiv_mte2` above 50% | GM-to-chip transfer limited | Larger transfers (16 KB or more), 512 B alignment, L2 cache mode |
| `mte3`/`aiv_mte3` above 50% | Write-back limited | Coalesce writes; avoid spilling intermediates to GM |
| `scalar` above 30% | Scalar instructions are the bottleneck | Shrink TilingData, remove scalar work from loops, do not over-provision cores |
| Everything below 50% | Pipeline bubbles | Read `conflicts.*_wait` and confirm double buffering |

High `scalar` is easy to miss: it means address arithmetic and loop control are consuming the time the main pipelines need, and enlarging the tile will not help as much as removing scalar instructions.

## Pass 3: read the bottleneck evidence

### Share of peak bandwidth

The denominator in `bandwidth[].peak_pct` is the real peak, inverted from the capture itself rather than taken from a datasheet. Reading:

- Above 80%: this path is at its limit; pushing it further yields nothing, so change the algorithm or the path
- 50-80%: headroom remains for further transfer tuning
- Below 50% while this path is dominant: the limit is latency or bubbles, not bandwidth (the profiler itself reports "bandwidth utilization lower than 80% when active")

### Core-to-core spread

`imbalance.aiv.spread_pct = (max - min) / mean`:

- Below 10%: even tiling
- 10-30%: tail-tile effect, usually an indivisible large shape
- Above 30%: the tiling strategy is wrong; pipeline gains will be eaten by the imbalance

Note that a large `spread_pct` on a very short kernel (under 10 us) mostly reflects launch overhead, not a tiling defect. Check the absolute `time_us` before concluding.

### Bank conflicts

`conflicts.vec_stu_cflt`, `vec_ldu_cflt`, `vec_sfu_cflt`:

- Below 5%: ignorable
- 5-15%: adjust UB address alignment or add padding
- Above 15%: the UB layout has a systemic problem

### Cache

- L2 read hit rate below 50%: data reuse is not happening, or the tile is large enough to thrash L2
- L2 write hit rate is naturally low because of write-back; do not tune it in isolation, and treat the value as tier E (measured CV 14%)
- iCache miss above 15%: the code footprint is too large (heavy unrolling or inlining); consider shrinking or splitting the kernel

### Wait ratios

`conflicts.vec_wait`, `*_mte*_wait` and `pipe_wait_cycles_total` show what each pipe is waiting for:

- Vector wait high with low MTE2 occupancy: transfers are not issued early enough; double buffering is not taking effect
- MTE3 wait high: the output queue is full and write-back cannot keep up
- Two pipes waiting on each other: queue depth is insufficient; increase the buffer count

## Expected distribution by operator type

| Type | Dominant pipe | Healthy range | Abnormal signal |
|---|---|---|---|
| Elementwise (Add/Mul/Relu) | vec | 50-80% | MTE2 share above vec |
| Reduction | vec | 40-70% | scalar above 20% |
| Activation (Softmax/Gelu) | vec | 60-85% | Heavy cast traffic |
| MatMul | cube | 40-70% | vec share above cube |
| Pure movement (Transpose/Concat) | mte2 + mte3 | Combined above 50% | vec above 30% |

These are expectations, not rules. A measured `cube` matmul spent 0.08% in the cube pipe because the shape was small; classification follows measured occupancy.

## Measured reference points (Ascend950PR, CANN 9.3.0, 1650 MHz)

**vector_add** (56 cores, 9.965 us, 409600 float32): MTE2 51.8%, scalar 23.9%, MTE3 17.7%, vec 8.3%. GM-to-UB peak 163 GiB/s, measured 28.4 GiB/s (17.4%). Core spread 71.9%, dominated by launch overhead on a short kernel. Vendor advice: MTE2 bandwidth below 80% while active, and vector occupancy below 20%.

**matmul mxfp4 base** (28 cores, 3.051 us): AIC scalar 92.5%, fixpipe 2.4%. Cube only 0.08% - at this shape the kernel spends nearly all its time on scalar preparation, the typical form of a cube-class operator at small shape, and proof that "cube operator" does not imply "cube busy".
