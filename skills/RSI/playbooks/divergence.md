# playbook: divergence

Applies to: class = compute_bound or latency_bound or mixed, with tag `divergence_high`.
Hardware: any
Version: 1
Last replay: never

Lanes within a scheduling group are taking different paths, so the group pays for both sides and
retires less useful work per issued instruction. Two things make this signature easy to
misdiagnose: the utilisation counter can sit near its ceiling while the machine is mostly masked
off, and some devices predicate rather than branch, so the counter may stay low while the cost is
still paid. The timing is the evidence; the counter is the explanation.

## Confirm first

- `branch_divergence_pct` is at or above the divergence threshold for this `hw_id`.
- The measured `divergence` curve for this device says the penalty at this divergent fraction is
  material. That curve is a sweep of the divergent fraction within a work group, and it is the
  only honest estimate of what removing the divergence is worth here. If the curve is flat, stop
  before trying anything.
- The divergent branch is on the hot path. A branch that runs once per work_group contributes
  nothing, and its removal will move `branch_divergence_pct` without moving time.
- `instruction_count` and the timing agree on the story. If the counter fell and time did not, the
  condition was predicated all along and both sides were always executed.

## Try in this order

1. **A condition that is uniform across the group but evaluated per lane** — hoist it so the whole
   group takes one path (P-012, no rule yet). Expect `branch_divergence_pct` down and
   `compute_throughput_pct` up with `instruction_count` down. Check it really is uniform: hoisting
   a condition that varies per element changes results, and that is a correctness failure, not a
   refutation.

2. **A branch whose two sides are both cheap and expressible without control flow** — express it
   as a select (P-012 again, second form). Expect `branch_divergence_pct` down. Watch
   `instruction_count`: a select executes both sides for every lane, so it wins only when the
   expensive side is rare or both sides are cheap. If `instruction_count` rises more than the
   masking cost it removed, it is a refutation, not a partial win.

3. **A genuinely data-dependent branch with very unequal side costs** — sort or partition so that
   elements needing the same path land in the same group (P-012, third form). Expect
   `branch_divergence_pct` down and `compute_throughput_pct` up. The sort must be outside the timed
   region; paying for it inside consumes the gain and is one of the implementation traps on the
   pattern.

4. **Divergence caused by a tail or bounds check** — restructure the loop so the boundary is
   handled by a separate, uniform path for the remainder (P-009 combined with P-012; no rule yet).
   Expect `branch_divergence_pct` down for the bulk of the work and `instruction_count` down.

5. **Divergence that is a symptom of a load imbalance rather than of control flow** — rebalance
   the work assignment so each group has comparable work (P-010, no rule yet). Expect
   `branch_divergence_pct` down and the tail fraction of the runtime down. Try this only after the
   three control-flow forms above are refuted; it is a different mechanism wearing the same tag.

## Do not

- **Replace a branch with a select that computes both expensive sides.** The divergence counter
  moves and the time does not, or gets worse. This is the most common superficial form of the
  change and it is recorded as a trap on P-012.
- **Sort inside the timed region.** The gain from uniform groups is usually smaller than the cost
  of the sort, and the resulting measurement is a loss that hides a real improvement.
- **Trust `branch_divergence_pct` alone as the verdict.** It is definition-dependent: devices that
  predicate may report low divergence while paying the cost of both paths. The verdict needs the
  timing or `instruction_count` as a second signal.
- **Assume a compute ceiling means the compute pipe is the limit.** A divergent group can show
  `compute_throughput_pct` near the top while retiring little useful work. Confirm with the
  divergence curve before choosing this playbook, and re-check after: the ceiling often moves.

## Stop

- Stop when attainment reaches the campaign target for this `hw_id`.
- Switch to `compute-bound` when `branch_divergence_pct` is below threshold and
  `compute_throughput_pct` is at its ceiling: the masking is gone and the pipe is now genuinely
  the constraint.
- Switch to `mem-cache-miss` when the divergent path was also the one with the bad access
  pattern, and the after-profile shows `sectors_per_request` above threshold. The load imbalance
  was hiding a contiguity defect.
- Switch to `plateau` when the branch is inherent to the operator's definition and no
  reformulation makes the groups uniform. At that point the honest report says the data is
  irregular and the kernel is near its practical ceiling for this formulation.
