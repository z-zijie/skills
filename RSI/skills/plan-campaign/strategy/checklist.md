# plan-campaign strategy

Read the section the action points at, not the whole file.

## Continue

Take the route with the highest predicted speedup that is still above the noise floor.

- The predicted speedup is what the hardware model and the rule cards say, not what you hope.
  A route whose prediction is missing ranks last, because an unpriced route cannot be compared.
- Prefer a route carried by an established rule over one carried by a candidate rule, and a
  candidate rule over a conjecture, at equal predicted speedup.
- Keep the parent current: the next round branches from `next.parent`, never from an older
  best. Branching from stale code silently discards confirmed gains and makes the next
  measurement unattributable.
- One card per round. A round that changes two things cannot add evidence to either rule.

## Combine

Combine when two confirmed changes are independent and their product clears the noise floor.

- Independent means different scopes: a load-width change and a tiling change combine; two
  different tiling changes do not, because the second already contains the first.
- Combine is worth one experiment when the product of the two measured speedups exceeds
  `1 + noise_threshold` by a margin. Below that the combined card costs a round and teaches
  nothing, since the individual gains were already recorded.
- Write the combined card's mechanism as the composition of the two mechanisms, and list both
  experiment ids under `based_on`. The verifier needs both counters to move.
- After a combine round, expect the bottleneck to have moved. Re-profile rather than assuming
  the pre-combine signature still holds.

## Changing direction

`change_direction` fires after three rounds without a significant gain. The script knows the
streak; you decide what it means.

- Re-profile and re-diagnose before writing the next card. Three flat rounds under one
  signature usually mean the signature is stale, not that the ideas were bad. The most
  valuable experiment in the plan is the one where the mechanism worked and the kernel did not
  move: it says the diagnosis was wrong.
- Check the rule the direction rested on. If its confirmation rate is falling, the scope is
  probably too wide; narrow it and retry rather than abandoning the mechanism.
- Read `prediction_error_watch`. When recent prediction errors are large, the hardware model
  has a hole there and no route can be priced. That is a `design-microbench` trigger, and it
  belongs to the orchestrator, not to this decision.
- Do not answer a flat streak with a bigger change. A rewrite that fails costs a campaign's
  worth of budget and produces no attributable evidence.

## Stopping well

Exactly four conditions stop a campaign, and each carries its own reason:

| `stop_reason` | Meaning | What the report must say |
| --- | --- | --- |
| `target_reached` | Attainment is at or above the target | The gap left, and whether the bound itself is trustworthy |
| `budget_exhausted` | The granted experiments are spent | Gain per experiment, and whether the trend was still rising |
| `no_gain_in_five_rounds` | Five rounds, no significant gain, no open route | Which routes were refuted, so the next campaign skips them |
| `no_open_route` | Every remaining route prices inside the noise band | That the playbook is exhausted for this signature |

- `needs_algorithm_rewrite` is set when the campaign stops away from the bound for a reason
  other than the target. Say it plainly in the report: the remaining gap is not reachable by
  the current playbook, and a human decides whether to fuse, change the algorithm or change
  the layout.
- Stopping is a decision with evidence, like any other. `evidence` names the rounds it rests
  on, so a later reader can disagree with the call rather than with the summary.

## Worked example

A memory-bound operator at 71.5% attainment, two confirmed wins (wide loads 1.18, tiling
1.09), noise floor 0.04, budget 10 with 2 spent:

- Target is not reached and the budget is not spent.
- The last round cleared the noise floor, so `continue` wins over `combine`.
- The next card aims at the highest-priced open route above 1.04, branching from the current
  best.

One more flat round changes the answer: with the streak at 3, the same ledger yields
`change_direction` with `rediagnose_first: true`, and profiling comes before any new card.
