# playbook: mem-dram-saturated

Applies to: class = memory_bound or mixed, with tag `mem_saturated`; usually together with
`uncoalesced` or `cache_hit_low`.
Hardware: any
Version: 1
Last replay: never

The device-memory path is at its measured ceiling. Bytes are the constraint: the memory term of
the roofline is the limiting term, so the only tactics that can move the number are the ones that
lower bytes moved or transactions spent per byte. Instruction-level work cannot help here, and
the measured `occupancy_vs_registers` curve says why — on a bandwidth-bound loop it is flat.

## Confirm first

- `device_mem_throughput_pct` is at or above the `mem_saturated` threshold for this `hw_id`, and
  `compute_throughput_pct` is clearly below its own threshold. If both are high the signature is
  `mixed`, and the bound may be set by either term; check `roofline.limiting_term` before
  choosing this playbook.
- The roofline's limiting term is `device_mem`. If it is `compute`, this is the wrong playbook.
- `sectors_per_request` and `device_mem_bytes` are both readable. If either is null, say so and
  do not claim a byte-side mechanism: an unreadable counter is unknown, not zero.
- Read the measured `coalescing` and `load_width` curves for this `hw_id` before estimating what
  a contiguity fix is worth. The size of the prize is on those curves, not in this file.

## Try in this order

1. **Transactions per request above the uncoalesced threshold** — make the access contiguous, by
   splitting interleaved fields into one array per field (P-001, rule R-001, established).
   Expect `sectors_per_request` to fall toward 1.0 and `device_mem_throughput_pct` to rise for
   unchanged useful bytes. Read the expected speedup off the measured coalescing curve at the
   stride you actually have.

2. **The same bytes moved more than once with no intervening reuse** — keep them in registers or
   on chip instead of re-fetching (P-003, no rule yet). Expect `device_mem_bytes` to fall. This
   is the only tactic in this list that lowers the bound rather than approaching it, so it is
   worth trying before any layout work if the redundancy is real.

3. **Genuine reuse inside a work_group** — stage the reused tile in scratchpad (P-004, no rule
   yet; see conjecture C-001 for the mixed-signature case, which is unverified and may not be
   cited as evidence). Expect `device_mem_bytes` down and `scratchpad_bytes_per_workgroup` up.
   Skip this if the reuse count is one; A-001 records what that costs.

4. **Loads narrower than the widest aligned load the device supports** — widen them (P-002, rule
   R-002, candidate, thin evidence: one confirmation only). Expect `instruction_count` down and
   `sectors_per_request` down. Treat the expected speedup as a guess until the rule is
   established.

5. **A neighbouring operator that consumes this output** — fuse the two passes so the
   intermediate never reaches device_mem (P-016, no rule yet). Expect `device_mem_bytes` down by
   one intermediate round trip and `launch_overhead_us` amortised. This is an algorithm-level
   change; if it is the only one left, consider whether the campaign belongs in the plateau
   playbook instead.

## Do not

- **Raise occupancy by shrinking the work_group.** A-002 records this refuted on exactly this
  signature: residency buys nothing when the memory path is already the constraint, and the
  smaller group can lose coalescing. R-004 is the deprecated rule card behind it.
- **Add a scratchpad tile to a kernel that reads each element once.** A-001 records the extra
  fill pass, the barrier and the capacity pressure, with no byte saved.
- **Tune work-group size at all as a first move.** It is the cheapest change to type and the
  least likely to matter here; the counters it moves are not the counters that set the bound.

## Stop

- Stop and report when attainment reaches the campaign target (the `target_attainment_pct`
  threshold for this `hw_id`, suggested starting value 85%). Further tactics in this list cannot
  pay at that point.
- Switch to `plateau` when every tactic above has been tried and refuted, or when the remaining
  gap is larger than the sum of what the measured curves say these tactics can deliver. A
  saturated kernel far from the bound usually means the byte formula or the algorithm is wrong,
  not that the kernel needs another pass.
- Re-diagnose before either: after three rounds without movement the bottleneck has often moved,
  and continuing here would be optimising the previous signature.
