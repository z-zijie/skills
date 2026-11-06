# playbook: mem-cache-miss

Applies to: class = memory_bound or latency_bound, with tag `uncoalesced` or `cache_hit_low`;
often together with `memory_stall_high`.
Hardware: any
Version: 1
Last replay: never

Bytes are being fetched badly rather than too often: the working set is not staying on chip
between uses, or each request is spending transactions on bytes nobody reads. This is the
playbook for access-pattern defects, which is why it comes before the saturated playbook in the
priority order — a coalescing fix here can lower the utilisation that made the other playbook
fire.

## Confirm first

- `sectors_per_request` is at or above the uncoalesced threshold, or `cache_hit_pct` is below the
  low-hit threshold, or both. Record which one fired; the tactics differ.
- `device_mem_throughput_pct` may be anywhere. If it is at the saturation threshold, run this
  playbook first anyway and re-diagnose afterwards — a contiguity fix usually changes the
  signature.
- The transaction granularity the adapter used is recorded next to `sectors_per_request`. Read
  it: the same ratio at two granularities is two different amounts of waste, and a rule scope
  that omits it is device-bound without saying so.
- Read the measured `cache_hierarchy` curve for this `hw_id` before choosing a tile size. The
  capacity steps on that curve are the only honest source for "how big is on chip here".

## Try in this order

1. **Interleaved fields read one at a time** — split into one contiguous array per field (P-001,
   rule R-001, established). Expect `sectors_per_request` down and `device_mem_throughput_pct` up.
   This is the highest-confidence tactic in the store for an `uncoalesced` signature; if the
   access is already contiguous, skip it rather than rewriting the layout.

2. **A traversal order that leaves a tile before its neighbours are used** — reorder the loop nest
   so concurrent lanes touch neighbouring addresses, or tile the walk (P-005, no rule yet).
   Expect `cache_hit_pct` up and `sectors_per_request` down with no layout change. Cheap to try
   and cheap to revert, which is why it comes before a layout conversion.

3. **A layout that forces every stage to stride** — convert once to a blocked or tiled layout
   outside the timed region (P-017, no rule yet). Expect `sectors_per_request` down across the
   whole pipeline and `cache_hit_pct` up. The conversion pass must be outside the measured
   window, or it will consume the gain it creates.

4. **Reuse that the hardware cache is not capturing** — stage the tile in scratchpad (P-004, no
   rule yet). Expect `device_mem_bytes` down. Only when the reuse count is greater than one:
   A-001 records the cost of doing it without reuse.

5. **Loads narrower than the widest aligned load** — widen them (P-002, rule R-002, candidate,
   thin evidence). Expect `instruction_count` down and `sectors_per_request` down. Weakest
   evidence in this list; try it last.

## Do not

- **Stage data in scratchpad that is read exactly once.** A-001: the copy adds a fill pass, a
  barrier and capacity pressure, and saves no byte. The measured symptom is
  `device_mem_bytes` unchanged or higher.
- **Cut registers to raise occupancy as a way to hide the misses.** A-002 records the same
  failure on a saturated path: the proxy counter moves and attainment does not. Fix the access
  pattern first; occupancy is the wrong lever for a miss problem.
- **Assume the stride penalty from the stride.** The measured `coalescing` curve is the estimate;
  a stride number is not a cost.

## Stop

- Stop when attainment reaches the campaign target for this `hw_id`.
- Switch to `mem-dram-saturated` if, after the contiguity and locality work, `sectors_per_request`
  is near 1.0 and `device_mem_throughput_pct` is at its ceiling — the defect is gone and bytes are
  now the constraint.
- Switch to `plateau` if the working set simply cannot fit on chip at any tile size the measured
  curve supports, or if every tactic above is refuted.
- Re-diagnose after three rounds without movement; a locality fix often changes which tag fires
  next.
