---
id: hw-doc-occupancy
title: Occupancy, what limits it, and why more is not automatically faster
store: hardware/documented
source:
  - docs/vocabulary.md section 2 (occupancy_pct) and section 5 (work_lane, work_group, wave)
  - _src/rsi-build-plan.md section 11 (occupancy versus registers microbenchmark)
  - evaluator/microbench/kernels.py (occupancy_vs_registers)
  - tooling/rsi/signature.py (occupancy_low and register_pressure thresholds)
last_reviewed: 2026-11-05
---

# Occupancy

## What it is

`occupancy_pct` is resident work items over the device maximum: how much of the machine's
ability to hold work lanes at once is actually in use. It is a ratio of a realised count to a
theoretical maximum, and both halves are device properties that the adapter reports.

Occupancy matters because latency has to be hidden. A lane waiting on `device_mem` cannot issue,
and the scheduler fills that gap from other resident lanes. With few resident lanes there is
nothing to fill the gap with, so `stall_memory` rises and throughput falls — that is the
`latency_bound` shape, and it is the case where raising occupancy genuinely pays.

## What limits it

Three resources, and the tightest one wins. The framework reports all three so the limit is
visible rather than guessed:

1. **Registers.** `registers_per_lane` times lanes per `work_group` must fit the register file
   per resident unit. High pressure — or live state the compiler keeps across a long region —
   reduces the number of resident groups directly. Spilling relieves the pressure by moving
   state to local memory, which trades occupancy for `spill_bytes` and therefore for
   `device_mem` traffic.
2. **Scratchpad.** `scratchpad_bytes_per_workgroup` times resident groups must fit the on-chip
   storage. This is the limit a tiling change runs into: the tile that maximises reuse is often
   the tile that halves residency.
3. **Work-group size.** Groups are scheduled in quanta, so a group size that is not a multiple
   of the scheduling quantum leaves part of every quantum idle. Very large groups also cap the
   number of groups that can be resident even when registers and scratchpad would allow more.

A fourth, softer limit is the barrier: a group that synchronises often cannot overlap its
phases, so residency buys less than the count suggests; that shows up as `stall_barrier`.

## Why more occupancy is not automatically faster

This is the most common wrong inference in the store, so it is worth stating plainly.

- **When the machine is already saturated, extra residency has nothing to hide.** If
  `device_mem_throughput_pct` or `compute_throughput_pct` is at the device ceiling, the pipes
  are busy and the added lanes only contend for the same resource. The measured
  `occupancy_vs_registers` curve shows this: on a bandwidth-bound loop, throughput is flat
  across a wide occupancy range, while on a latency-bound loop it rises steeply.
- **Cutting registers to raise occupancy can lose more than it gains.** Fewer live registers
  means recomputation or spilling; either costs instructions or `device_mem` traffic. If the
  kernel is compute-bound or bandwidth-bound, that trade is a net loss even though
  `occupancy_pct` went up.
- **Occupancy is an enabler, not an objective.** The objective is attainment against the
  roofline. Occupancy is useful only as an explanation of why the machine is idle, and the tag
  `occupancy_low` is a hint to check whether idleness is the actual problem — not an instruction
  to raise the number.

The correct question is: which term of the bound is limiting, and is the machine idle waiting
for it? `occupancy_low` together with high `stall_memory` and low utilisation is the case for
raising residency. `occupancy_low` together with utilisation at the ceiling is a case for doing
nothing about occupancy at all.

## Device-specific notes

- Devices differ in whether the register file is allocated in fixed-size granules, so the same
  `registers_per_lane` can allow different residency; the granule is a property of the device and
  is captured by the measured occupancy curve rather than assumed.
