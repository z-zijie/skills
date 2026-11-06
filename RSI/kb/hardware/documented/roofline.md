---
id: hw-doc-roofline
title: The roofline bound and what attainment means
store: hardware/documented
source:
  - Williams, Waterman and Patterson, "Roofline: An Insightful Visual Performance Model for Multicore Architectures", Communications of the ACM 52(4), 2009
  - _src/rsi-build-plan.md section 4.5 (theoretical bound) and section 11 (bandwidth, peak compute, launch overhead)
  - tooling/rsi/hardware.py (Roofline.bound_us, Roofline.attainment)
  - schemas/signature.schema.json (roofline.bound_us, measured_us, attained_pct, limiting_term)
last_reviewed: 2026-11-05
---

# The roofline

## What the bound is

For one operator and one shape, the cost model in `spec.yaml` gives two quantities: the bytes
that must move to and from `device_mem`, and the floating-point operations that must be
performed. The device supplies two measured ceilings: peak `device_mem` bandwidth and peak
compute for the operator's dtype. The bound is the larger of the two implied times:

```
T_mem    = bytes / peak_bandwidth
T_compute = flops / peak_flops[dtype]
T_bound  = max(T_mem, T_compute)
```

Whichever term is larger is the `limiting_term`, and it is a property of the operator's
arithmetic intensity, not of the kernel. An operator whose intensity sits below the device's
ridge point is memory-limited and no amount of instruction-level work will move its bound; one
above it is compute-limited and no amount of coalescing will.

Both ceilings come from the measured layer, never from a datasheet. `peak_bandwidth_gbps` and
`peak_flops` are produced by the `memory_bandwidth` and `peak_compute` microbenchmarks, carry an
environment fingerprint, and are re-measured when the environment changes.

## What attainment means

```
attained_pct = 100 * T_bound / T_measured
```

Attainment is the fraction of the achievable limit the kernel actually reaches. It is the one
absolute number the system optimises, which is why campaigns stop on it rather than on a
speedup: a kernel can double in speed and still sit at 40% of the bound, and a kernel at 84%
has little left no matter how clever the next change is.

The campaign target is a threshold from the threshold table for the current `hw_id`, not a
constant in prose. It is compared against the measured `attained_pct` that the signature
carries.

## Three ways a roofline comparison misleads

**1. Cache reuse is assumed away.** The cost model counts bytes to `device_mem` and ignores
`onchip_cache` and `scratchpad` reuse. A kernel that keeps its working set on chip can beat the
memory term of the bound, and then `attained_pct` exceeds 100. That is not a measurement error;
it means the cost model no longer describes the kernel. Treat attainment above 100 as evidence
that the byte formula needs a reuse term, and fix the formula in `spec.yaml` rather than the
kernel.

**2. Peak was measured on a different dtype.** Peak compute is per dtype, and the same machine
can differ by a large factor between them. Comparing an operator that computes in one precision
against a peak measured in another produces an attainment that is off by that factor, in either
direction. The dtype used for the bound must be the dtype the operator's hot loop actually
executes in, after any implicit promotion.

**3. Launch overhead is not in the bound.** The bound models steady-state work. A kernel whose
measured time is a large multiple of the launch cost is dominated by a fixed charge that the
bound does not contain, so its attainment is low for a reason that has nothing to do with
efficiency. When the `launch_dominated` tag fires, the fix is fewer and larger launches, and the
attainment number should be read against the launch floor as well as the roofline.

A fourth, smaller trap: the bound assumes the device is dedicated to this kernel. Co-scheduled
work, clock behaviour and thermal state all move `measured_us` without moving the bound, which
is why every measurement carries an environment fingerprint and a noise threshold.

## Device-specific notes

- On devices that can execute some operations on a wider pipe than the one the `spec.yaml`
  flop count assumes (for example a matrix pipe counted as scalar operations), the compute term
  is understated and attainment can exceed 100 for a correct kernel; the measured
  `peak_flops` entry for the relevant dtype, not the scalar one, is the term to use.
