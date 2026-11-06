# playbook: latency-bound

Applies to: class = latency_bound, with tag `launch_dominated`; also the entry point when
neither utilisation counter clears its threshold and attainment is low.
Hardware: any
Version: 1
Last replay: never

Neither the memory path nor the compute pipe is busy, and the kernel still takes longer than the
bound. Something is paying a fixed cost, or the machine is waiting on a dependency it cannot
overlap. The distinguishing measurement is `launch_overhead_us` against `measured_us`: when the
fixed charge is a material fraction of the total, the tactics here are about doing fewer, larger
things; when it is not, they are about exposing more independent work.

## Confirm first

- Neither `device_mem_throughput_pct` nor `compute_throughput_pct` is at its threshold, and
  `attained_pct` is below the campaign target. That combination is the definition of
  `latency_bound`.
- `launch_overhead_us` and `measured_us` are both readable, so the launch share is computed rather
  than assumed. If `launch_overhead_us` is null, this playbook may still apply but the
  `launch_dominated` tag cannot fire and the first section below cannot be ordered by evidence.
- `occupancy_pct` is at or above the low threshold. If it is below, run `low-occupancy` first:
  idleness from too little work looks identical to idleness from a fixed cost, and the fixes are
  different.
- The noise floor for this operator's timing protocol is known. At small shapes the effect being
  chased can be smaller than the run-to-run variation, and a campaign that ignores that chases
  noise.

## Try in this order

1. **`launch_overhead_us` a material share of `measured_us`** — count the launches. Every launch
   in the sequence is a candidate for removal (P-015, no rule yet). Expect total fixed time down
   in proportion to launches removed. Read A-004 first: merging at shapes where the charge was
   never material was refuted, and the failure mode was a residency loss at the large shapes.

2. **Several adjacent stages on the same data** — fuse them into one pass so the intermediate
   never reaches device_mem (P-016, no rule yet). Expect `launch_overhead_us` amortised *and*
   `device_mem_bytes` down by one round trip. This is strictly better than merging alone and is
   the preferred form when the stages are elementwise-compatible.

3. **A shape too small to fill the device** — check the grid extent against what the device can
   hold concurrently (P-010, no rule yet). Expect `occupancy_pct` up and the tail fraction down.
   At very small shapes the honest answer may be that the kernel is launch-bound and the only
   fixes are fewer launches or a larger batched shape.

4. **Dependent requests that cannot overlap** — give each lane several independent elements, or
   restructure so the loads issue before the first result is needed (P-011, no rule yet). Expect
   `stall_memory` down and effective throughput up. This converts latency-bound into
   bandwidth-bound, which is progress: the second has a higher attainable ceiling.

5. **Barriers that order phases with nothing to order** — consolidate, or double-buffer (P-014,
   no rule yet). Expect `stall_barrier` down. Only worth trying when `stall_barrier` is
   non-trivial; a barrier that costs nothing is not the problem.

## Do not

- **Merge launches at every shape because the smallest shape improved.** A-004 records this
  refuted: the merged kernel carried a larger footprint and lost time at the large shapes, and the
  score was dominated by the large shapes.
- **Raise occupancy as the first move.** A-002: the work-group size change moves `occupancy_pct`
  and not attainment whenever the fixed cost is the real limit. Confirm the launch share first.
- **Report a speedup that is inside the noise floor.** At the shapes this playbook serves, the
  effect and the noise are the same size; a change that does not clear the measured floor is not a
  win, whatever the two point estimates say.

## Stop

- Stop when attainment reaches the campaign target for this `hw_id`, or when the launch share has
  been reduced to below the materiality threshold and the remaining time is real work.
- Switch to `low-occupancy` if the after-profile shows `occupancy_pct` still below threshold with
  no fixed-cost share to explain it.
- Switch to `mem-dram-saturated` or `compute-bound` when a utilisation counter has risen to its
  ceiling: the fixed charge was hiding real work, and that work is now the limit.
- Switch to `plateau` when the operator's shape distribution is so small that the launch floor is
  the attainment ceiling. No kernel change fixes that; the campaign report should say so plainly
  rather than recording a failed optimization.
