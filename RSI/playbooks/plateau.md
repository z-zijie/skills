# playbook: plateau

Applies to: class = any, with tag `plateau` — attainment has stalled over the last three
experiments and the signature-driven tactics have stopped paying.
Hardware: any
Version: 1
Last replay: never

Progress has stopped, and the reason is structural rather than tactical. Everything in the other
seven playbooks moves the kernel toward a bound that is fixed by the operator's byte and flop
counts. This playbook is the only one that can move the bound itself, by changing what the
operator computes or how much it must move to compute it. That makes it the most expensive
playbook and the one most likely to need a human decision, which is why it is last in the
priority order and why it opens with a question rather than a tactic.

## Confirm first

- `plateau` is set: attainment has not improved over the last three experiments by more than the
  noise threshold. Confirm the three experiments were real attempts, not implementation failures —
  a failure counts toward no rule and does not mean the tactic was refuted.
- The kernel has been re-profiled. The bottleneck may have moved during the campaign, and a
  stale signature makes this playbook look like the answer when a different one is.
- `roofline.limiting_term` and `attained_pct` are both known, and the measured peak values behind
  the bound are current for this `hw_id` (check the environment fingerprint and the measurement
  date). A stale peak makes the gap look larger or smaller than it is.
- The gap is worth the cost. If attainment is within a few points of target, stopping and
  reporting is the correct action, not this playbook.

## Try in this order

1. **Ask whether the bound is the right bound.** If `attained_pct` is above 100, the cost model
   has stopped describing the kernel — reuse is happening that the byte formula does not count.
   The fix is a reuse term in `spec.yaml` (a human decision), not a kernel change. Report it; do
   not quietly optimize against a bound that is wrong.

2. **Two adjacent operators each paying a full round trip for the same intermediate** — fuse them
   (P-016, no rule yet). Expect `device_mem_bytes` down by one intermediate round trip and
   `launch_overhead_us` amortised. This is the cheapest bound-lowering change and should be tried
   first among the structural ones, because it needs no change to the operator's mathematical
   definition.

3. **An arrangement of the data that forces every stage to stride** — change the layout once,
   outside the timed region (P-017, no rule yet). Expect `sectors_per_request` down across the
   whole pipeline and `cache_hit_pct` up. This is a bound-adjacent change: the byte formula stays
   the same but the transactions spent per byte fall.

4. **Compute in a narrower dtype than the tolerance requires** — mixed precision, with a wide
   accumulator (P-018, no rule yet). Expect `device_mem_bytes` down and `compute_throughput_pct`
   up, and the bound itself down if the limiting term was memory. This changes numerical
   behaviour: it needs the spec's tolerance checked on the full shape distribution, and it is a
   decision worth flagging to a human before it is merged.

5. **An algorithm that does asymptotically less work for the same result** — a different
   decomposition, a transform-domain formulation, a strength-reduced recurrence. This has no
   pattern entry and no rule, and it should not get one until it has been tried on several
   operators: a single algorithm change is an anecdote. Mark the hypothesis as a conjecture when
   it is first proposed, and let W7 decide whether it is general.

6. **An operator boundary the caller could remove** — if the surrounding graph materialises a
   tensor this operator immediately consumes, the cheapest structural win may be outside this
   kernel. Report it as a finding for the campaign report; the inner loop cannot change the
   caller.

## Do not

- **Keep re-running the tactical playbooks in the hope that a different ordering helps.** They
  were refuted for this signature; repeating them produces implementation-shaped activity and no
  information, and it burns the campaign budget that a structural change needs.
- **Change the algorithm without stating the observable that would falsify it.** An
  algorithm-level hypothesis that cannot be refuted is not a hypothesis, and its confirmation
  cannot enter the rule store.
- **Quietly lower precision to buy attainment.** P-018's traps cover this: narrowing without
  checking the tolerance on the longest reduction is a correctness change, and the campaign's
  whole value depends on the correctness gate being real.
- **Apply a structural change at every shape because it won at one.** A-004 records this for
  merged launches: the gain at the smallest shape became a loss at the largest, and the score was
  dominated by the largest. Scope a fusion or a layout change by shape, not by operator.
- **Treat a confirmed-but-not-faster result as a failure.** It is the most informative outcome
  the system produces: the mechanism worked, so the diagnosis was wrong. Go back and fix the
  signature thresholds or this playbook rather than the kernel.

## Stop

- Stop when attainment reaches the campaign target, or when a structural change has been
  confirmed and the campaign can continue from the new `best/` under a refreshed signature.
- Stop and report when the budget is spent without a confirmed structural change. The campaign
  report must say "needs an algorithm-level rewrite" rather than presenting a plateau as a
  success; a human then decides whether to open a new campaign with a different formulation.
- Hand back to the tactical playbooks if a structural change moves the signature: after a fusion
  or a layout change, the bottleneck is usually a different one, and continuing here would be
  optimizing the previous problem.
