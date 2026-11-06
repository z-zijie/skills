---
id: hw-doc-memory-hierarchy
title: The memory hierarchy as four roles
store: hardware/documented
source:
  - docs/vocabulary.md section 1 (canonical names and roles)
  - _src/rsi-build-plan.md section 11 (cache hierarchy, bank conflicts, load width)
  - "Hennessy and Patterson, Computer Architecture: A Quantitative Approach, chapter 2"
  - evaluator/microbench/suite.py (which parameter each curve produces)
last_reviewed: 2026-11-05
---

# The memory hierarchy

Four levels, named by the role they play in the machine. The names are canonical and
device-neutral: every accelerator has all four, and no step in this framework may assume
anything about their sizes, speeds or sharing rules beyond what the measured layer reports.

## The four levels

| Level | Role | Managed by | What it bounds |
| --- | --- | --- | --- |
| `register_file` | per-lane storage the instruction set addresses directly | the compiler, through allocation | how much live state one lane can hold; spilling is the failure mode |
| `scratchpad` | on-chip storage with a declared size and banking, shared by a `work_group` | the program, explicitly | reuse inside a work group, and the barrier cost of that reuse |
| `onchip_cache` | hardware-managed cache levels between the lanes and `device_mem` | the hardware, by replacement policy | how much of a working set survives between accesses without the program saying so |
| `device_mem` | off-chip memory, the only level whose bandwidth is a fixed device property | the memory controller | total bytes that must move for the operator to complete |

Two consequences follow directly, and they drive most of the tactics in the playbooks.

- `device_mem` is the level the roofline is written against. Bytes read and written there are
  counted from the operator's `spec.yaml` formulas, so the bound is a property of the shape and
  the device, not of the kernel.
- `scratchpad` and `onchip_cache` only help if the same bytes are touched more than once. A
  kernel with no reuse gains nothing from either, and a kernel that already fits in
  `onchip_cache` gains nothing from a `scratchpad` copy except an extra hop.

## Limits, level by level

- **`register_file`.** Finite per lane and per `wave`. When the allocator runs out it spills to
  local memory, which is backed by `device_mem`; `spill_bytes` above zero is therefore a
  memory-traffic problem wearing a register-pressure disguise. Live state per lane also caps
  how many work items can be resident, which is one of the three occupancy limits.
- **`scratchpad`.** Finite per `work_group` (`scratchpad_bytes_per_workgroup`), organised into
  banks or vaults, and reachable only across a barrier. Its bandwidth is high but not
  infinite: `scratchpad_throughput_pct` near the device peak means the copy is now the
  bottleneck. Conflicts are a function of the bank mapping and the access stride, so the same
  code can conflict on one device and not on another.
- **`onchip_cache`.** Capacity and associativity are device properties. A hit rate is a
  property of the access pattern *and* of everything else resident at the same time, which is
  why `cache_hit_pct` moves when unrelated work is co-scheduled. The outermost level is the one
  the canonical metric reports.
- **`device_mem`.** Large, slow relative to the on-chip levels, and accessed in transactions of
  a fixed granularity. Requests that are not contiguous cost more transactions for the same
  bytes, which is what `sectors_per_request` measures.

## Why tile sizes come from the measured layer

A tile size is a bet about capacity: "this block of the working set stays resident while I
reuse it". The bet is only as good as the capacity and latency numbers behind it, and those
are not derivable from a specification sheet, for three reasons.

1. **Effective capacity is smaller than nominal capacity.** Replacement policy, set conflicts
   and other resident work all eat into it. Only a working-set sweep shows where the latency
   steps actually are.
2. **The banking structure decides whether a tile shape is conflict-free.** The mapping from
   address to bank is a device property. The `bank_conflicts` microbenchmark produces the
   slowdown-versus-stride curve, and that curve is what says whether to pad.
3. **The crossover between levels is a curve, not a number.** The `cache_hierarchy`
   microbenchmark sweeps the working set from small to large and reports the capacity and
   latency steps; a tile that straddles a step loses most of its reuse.

So the playbooks never state a tile size. They state the relationship — "tile so the reused
region stays inside the level the measured curve says is fast, and pad until the measured
conflict curve is flat" — and the number is read from `kb/hardware/measured/` for the current
`hw_id`.

## Device-specific notes

- On some devices the `scratchpad` and the first `onchip_cache` level are the same physical
  storage, so a large `scratchpad_bytes_per_workgroup` reduces cache capacity; on others they
  are separate. The measured `cache_hierarchy` and `bank_conflicts` curves show which case the
  current device is in.
