---
id: hw-doc-profiler-metric-pitfalls
title: Counters that lie, and how a verifier cross-checks them
store: hardware/documented
source:
  - _src/rsi-build-plan.md section 10.3 C ("if a cited counter is listed in the documented layer as known to be unreliable, cross-check with an alternative metric")
  - tooling/rsi/metrics.py (UNRELIABLE_BY_DESIGN)
  - tooling/rsi/counters.py (unreliable_metrics in the comparison result)
  - one device vendor's developer-forum thread on a conflict counter reading higher than expected; the URL is recorded in the device-specific note below
last_reviewed: 2026-11-05
---

# Profiler metric pitfalls

Every canonical metric is a decision made from a counter that some adapter read from some
device. Counters are not ground truth: they are estimates produced by sampling, by hardware
event units with finite width, or by a definition that only approximates the quantity the name
suggests. This entry lists what is known to be shaky, and what a verifier must do about it.

## Marked unreliable by the framework

`scratchpad_conflict_ratio` is the one metric currently in `UNRELIABLE_BY_DESIGN`. The reasons
are structural, not incidental:

- The counter counts *replays* of a scratchpad access, and a replay can be caused by something
  other than a bank conflict — an address divergence within the request, a misaligned access, a
  bank-mode change. The count therefore conflates several causes under one name.
- The ratio's denominator (accesses) is itself a counted quantity, so an error in either term
  moves the ratio without anything about the access pattern changing.
- Reporting granularity varies: some devices expose per-instruction counts, some per scheduling
  quantum, some only a derived percentage. A ratio built from a quantum-level count is not the
  same quantity as one built from a per-access count.

Consequence for the verifier: a verdict may not rest on `scratchpad_conflict_ratio` alone.
`counters.py` surfaces such metrics in `unreliable_metrics`, and the check must be paired with a
second, independent signal — `scratchpad_throughput_pct`, `stall_barrier`, or a measured timing
comparison against the `bank_conflicts` curve for this device.

## Counters that are definition-dependent

These are not wrong, but they mean different things on different devices, so a threshold or a
prediction built from one device's definition does not transfer.

- **`occupancy_pct`.** Whether the denominator is the theoretical maximum for the compiled
  kernel or for the device, and whether the numerator counts resident lanes or active lanes,
  changes the number substantially. Compare only within one `hw_id`, and prefer the measured
  `occupancy_vs_registers` curve over the absolute percentage.
- **`sectors_per_request`.** Depends entirely on the transaction granularity the adapter used.
  A ratio of 1.4 at one granularity and 1.4 at another are different amounts of waste. The
  adapter records the granularity next to the number; a rule that cites this metric must carry
  the granularity in its scope or be treated as device-bound.
- **`stall_memory`.** The set of stall reasons a device can attribute is device-specific, and
  "waiting on device memory" is a bucket whose edges differ. A drop in `stall_memory` with no
  change in `device_mem_throughput_pct` may mean the stall was reattributed, not removed.
- **`cache_hit_pct`.** Reports the outermost hardware-managed level only. Because that level is
  shared, the number moves when unrelated work is co-scheduled, and it moves when the working
  set crosses a capacity step. A before-and-after comparison is valid only if both profiles were
  taken under the same residency conditions.
- **`launch_overhead_us`.** A single fixed number from the calibration suite, applied to kernels
  of every size. It is a floor, not a per-launch measurement of the run being profiled; at very
  small shapes the real overhead includes driver-side work that the calibration kernel does not
  exercise.

## Counters that can be structurally absent or zero

- **`spill_bytes`.** Zero can mean "no spilling" or "this device does not report it". The
  schema allows null for exactly this reason; a null must be read as unknown, never as zero.
- **`instruction_count`.** Inflated by unrolling and by code the compiler emitted but the hot
  path does not execute; it counts issued instructions in the window, not useful ones.
- **`branch_divergence_pct`.** Devices that predicate rather than branch may report low
  divergence while still paying the cost of both paths; the timing is the evidence, the counter
  is the explanation.

## What the verifier does about it

1. Refuse a verdict that rests on a single metric listed here when no second signal moved.
2. Cross-check against a metric that measures the consequence rather than the cause: the
   conflict ratio against `scratchpad_throughput_pct` or `stall_barrier`; divergence against
   `instruction_count` and the timing; spills against `device_mem_bytes`.
3. Require the change to exceed that counter's own run-to-run variation before it counts; a
   movement inside the noise floor is not a movement.
4. Record the granularity, the definition and the environment fingerprint alongside any number
   that is carried into a rule card, so a later reader can tell whether the comparison is even
   the same quantity.

## Device-specific notes

- One device vendor's developer forum reports that its conflict-related counter can read higher
  than the conflict count a programmer expects, because the counter increments for replays with
  causes other than bank conflicts (https://forums.developer.nvidia.com/t/shared-memory-bank-conflicts-and-nsight-metric/115731). This is one device's counter behaviour, not a universal property; treat it as the reason to cross-check, not as a correction factor.
