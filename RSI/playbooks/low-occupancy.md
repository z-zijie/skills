# playbook: low-occupancy

Applies to: class = latency_bound, with tag `occupancy_low`; usually with `register_pressure`,
`spills`, or an unusually large scratchpad allocation per work-group.
Hardware: any
Version: 1
Last replay: never

The machine is holding fewer work items than it could, and it is idle waiting. Occupancy is the
explanation, not the objective: what matters is whether the idleness it causes is the reason the
kernel is slow. That question has a cheap test, and this playbook starts with it.

## Confirm first

- `occupancy_pct` is below the low-occupancy threshold for this `hw_id`.
- **Neither** `device_mem_throughput_pct` nor `compute_throughput_pct` is at its ceiling. If
  either is, go to that playbook instead — raising residency buys nothing on a saturated resource,
  and A-002 records exactly that failure.
- `stall_memory`, `stall_barrier`, `registers_per_lane`, `spill_bytes` and
  `scratchpad_bytes_per_workgroup` are readable, so the limiting resource is identified rather
  than guessed. If several are null, say which, and treat the limit as unknown.
- Read the measured `occupancy_vs_registers` curve for this `hw_id`. If it is flat at this
  occupancy, the whole playbook is a no-op and you should stop before trying anything.

## Try in this order

1. **Work-group size not a multiple of the scheduling quantum, or a grid too small to fill the
   device** — size the geometry from the machine rather than from the shape alone (P-010, no rule
   yet). Expect `occupancy_pct` up and the tail fraction of the runtime down. Check the grid
   extent: a group-size change without a matching extent change alters coverage, which is a
   correctness bug wearing an optimization's clothes.

2. **One element per lane, with the kernel idle waiting on the first request** — give each lane
   several elements so the requests overlap (P-011, no rule yet). Expect `stall_memory` down and
   effective throughput up, with `instruction_count` per element down. This is often the larger
   win of the two, and it does not touch residency at all.

3. **Register pressure near the device maximum** — shorten live ranges or recompute cheap values
   (P-020, rule R-003, under review). Expect `registers_per_lane` down, `occupancy_pct` up and
   `stall_memory` down. **Read R-003's counterexamples first**: half its evidence refuted it, the
   mean prediction error is above the hole threshold, and the failures were cases where a
   utilisation counter was already at its ceiling. Treat it as a lead with a warning, not as a
   recipe.

4. **Scratchpad allocation capping residency** — reduce the tile below the point where another
   work group fits (P-004, reverted in this direction; no rule yet). Expect
   `scratchpad_bytes_per_workgroup` down and `occupancy_pct` up. Watch `device_mem_bytes`: a tile
   too small to hold the reuse re-fetches, which trades residency for traffic.

5. **Frequent barriers preventing overlap** — consolidate phases or double-buffer (P-014, no rule
   yet). Expect `stall_barrier` down. This is the case where low occupancy and barrier cost
   compound; fixing one without the other may show nothing.

## Do not

- **Treat `occupancy_pct` as the objective.** A-002: it is the classic proxy win — the counter
  rises, attainment does not. Every tactic here must name the counter that should move *with* the
  speed, and the mechanism for why idleness was the limit.
- **Force spills to relieve register pressure.** A-001's mechanism applies in reverse: moving
  live state to local memory converts a residency problem into device-memory traffic, and
  `spill_bytes` is the evidence that it happened.
- **Raise occupancy by staging data in scratchpad when the tile already fit in registers.** Same
  anti-pattern, different route: capacity pressure for no reuse.

## Stop

- Stop when attainment reaches the campaign target for this `hw_id`.
- Switch to `latency-bound` if residency is now reasonable and the machine is still idle: the
  limit is then launch or synchronisation cost rather than parallelism.
- Switch to `mem-dram-saturated` or `compute-bound` when a utilisation counter arrives at its
  ceiling as a result of the added work — the idleness has been used up, and the next constraint
  is the real one.
- Switch to `plateau` when the measured occupancy curve says throughput is flat across the
  achievable occupancy range. More residency is then not the answer and no tactic here can pay.
