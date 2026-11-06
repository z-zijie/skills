# playbook: smem-conflict

Applies to: class = memory_bound or latency_bound or mixed, with tag `scratchpad_conflict`.
Hardware: any
Version: 1
Last replay: never

Accesses to scratchpad are mapping many lanes onto the same bank, so what should have been
parallel is serialised. This playbook carries an extra obligation that the others do not: the
counter that fires it is the one the documented layer marks unreliable, so no verdict here may
rest on `scratchpad_conflict_ratio` alone.

## Confirm first

- `scratchpad_conflict_ratio` is at or above the conflict threshold for this `hw_id`, **and** a
  second, independent signal agrees: `scratchpad_throughput_pct` high, `stall_barrier` high, or a
  measured timing difference against the `bank_conflicts` curve. The counter is in
  `UNRELIABLE_BY_DESIGN` because it counts replays caused by address divergence and misalignment
  as well as by bank mapping; without a second signal you do not know which cause you have.
- The measured `bank_conflicts` curve for this device is available. It is a sweep of scratchpad
  access at different strides, and it is what says whether padding is worth trying and how much a
  given conflict degree costs.
- The scratchpad allocation has a static size (`scratchpad_bytes_per_workgroup` is readable) so
  the cost of a pad can be priced against the residency it may cost.
- The kernel actually reads scratchpad on the hot path. A conflict on a staging buffer read once
  is not the bottleneck, however high the ratio.

## Try in this order

1. **A fixed stride that maps a column onto one bank** — pad the row length so the stride and the
   bank count are coprime (P-013, no rule yet). Expect `scratchpad_conflict_ratio` down and
   `stall_barrier` down, with `scratchpad_bytes_per_workgroup` up by the pad. Read A-003 first: the
   refuted form is a pad that is a multiple of the bank count, or a pad declared in the allocation
   and not applied in the index expression.

2. **An access pattern whose width itself causes replays** — change the element grouping so each
   lane reads a unit the bank mapping handles cleanly (P-002 read in the scratchpad direction; no
   rule yet). Expect `scratchpad_conflict_ratio` down and `scratchpad_throughput_pct` down for the
   same useful data.

3. **Frequent barriers compounding the serialisation** — consolidate phases, or double-buffer so
   the fill and the compute overlap (P-014, no rule yet). Expect `stall_barrier` down. A conflict
   and a barrier interact: serialised accesses make every lane arrive at the barrier later and
   more spread out, so fixing only one may show a smaller gain than expected.

4. **A reduction staging every partial through scratchpad** — fold within the scheduling group
   before staging (P-019, no rule yet). Expect `scratchpad_throughput_pct` down and
   `device_mem_bytes` down. Fewer scratchpad touches means fewer chances to conflict, which is a
   different route to the same counter.

5. **A tile shape that conflicts at every size the measured curve covers** — change the tiling so
   the working set is traversed in an order that touches banks evenly (P-005 combined with P-013;
   no rule yet). Expect `scratchpad_conflict_ratio` down without a capacity increase. This is the
   fallback when padding costs too much residency.

## Do not

- **Rest a verdict on `scratchpad_conflict_ratio` alone.** The documented layer records that this
  counter over-counts on some devices because replays from address divergence and misalignment are
  attributed to it. Cross-check with `scratchpad_throughput_pct`, `stall_barrier`, or measured
  time before claiming the mechanism was implemented.
- **Pad by a value that shares a factor with the bank count.** A-003: the collision pattern recurs
  and the only change is a larger allocation.
- **Pad until the allocation spills.** A-003's mechanism in the other direction: a pad that pushes
  the tile past the scratchpad budget converts conflicts into local-memory traffic and
  `spill_bytes` is the evidence.
- **Add a scratchpad tile to a kernel with no reuse in order to have something to pad.** A-001:
  the tile itself is the mistake on a single-use kernel, and no bank mapping fixes that.

## Stop

- Stop when attainment reaches the campaign target for this `hw_id`.
- Switch to `mem-cache-miss` when the conflict is gone and `sectors_per_request` or `cache_hit_pct`
  now fires: the scratchpad was serialising a feed whose device-memory side was also defective.
- Switch to `compute-bound` when `scratchpad_conflict_ratio` is near its floor and
  `compute_throughput_pct` is at its ceiling with `stall_barrier` low.
- Switch to `plateau` when every pad and traversal tried is refuted, or when the measured
  `bank_conflicts` curve says no stride in the achievable range is conflict-free. The data shape
  is then the limit and only a different algorithm — or a different tile decomposition — moves it.
