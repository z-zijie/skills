---
name: plan-campaign
description: Decide one campaign move for one operator - continue, change direction, combine two confirmed changes, or stop. Use after every recorded experiment, and again whenever a campaign is resumed, before the orchestrator starts the next round.
---

# plan-campaign

Campaign-level decision for one operator. The inner loop asks "is this change good?"; this
skill asks "is this campaign still going anywhere, and what is the next move?". It reads the
operator's ledger and the routes the playbooks still hold open, and it answers with one
action the orchestrator can run.

It does not propose hypotheses, write code or measure anything. `propose-hypothesis` turns
`next` into a card when the action calls for one.

## When it runs

- After `record-experiment` appends a round, before the next round starts.
- At the start of a campaign, with an empty entry list, to choose the opening route.
- When a human resumes a paused campaign and the ledger has changed since.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `op`, `entries` | This operator's ledger, oldest first. The ledger is read-only here. |
| `attainment_pct` | Roofline attainment of the current best version |
| `noise_threshold` | Measured A/A floor; a gain inside it is not a gain |
| `target_attainment_pct` | The goal, default 85 |
| `budget` | Experiments granted and spent |
| `start_from` | Version the next round would branch from |
| `open_routes` | Untried playbook items with their predicted speedup |

## Steps

1. Run `python3 scripts/decide.py --input <file>`. Done when it prints one decision object;
   the script is pure over its input, so an identical ledger yields an identical action.
2. Check the decision against `contract/output.schema.json`. Done when validation reports no
   problems.
3. When `action` is `change_direction`, read `strategy/checklist.md` section "Changing
   direction" before the next round: the script can see that the streak is flat but not why.
4. When `action` is `stop`, read `strategy/checklist.md` section "Stopping well" and hand
   `stop_reason` and `needs_algorithm_rewrite` on unchanged. A campaign that stops far from
   the bound must say so.
5. Report the decision. Done when `evidence` names the experiment ids the move rests on, so a
   later reader can re-derive the same call.

## Output

`contract/output.schema.json`. The fields that change what happens next:

- `action` - `continue`, `change_direction`, `combine` or `stop`.
- `next` - the transformation to aim at, the parent version, and the two experiment ids when
  combining. `rediagnose_first: true` means re-profile before writing a card, because the
  bottleneck may have moved.
- `stop_reason` - exactly one of the four campaign stop conditions, or null.
- `prediction_error_watch` - recent experiments whose prediction error exceeds the hole
  threshold. The orchestrator queues `design-microbench` from this list; do not queue it here.
- `needs_algorithm_rewrite` - the campaign stopped far from the bound.

## Judging principles

- Decide on the ledger and the measured noise floor, never on how promising a change sounds.
  A route with no predicted speedup is not evidence of anything.
- Let the script own every threshold. Target attainment, the streak length, the hole threshold
  and the budget are inputs or measured defaults; compute none of them from prose.
- A gain that did not clear the noise floor is not a gain, and a streak is counted in rounds
  that were really implemented. `not_implemented` rounds say something about the implementer,
  not about the direction.
- Combine only independent, confirmed, significant changes. Two edits to the same mechanism
  are one change measured twice, and the script rejects them for that reason.
- Stop at the bound: continuing past the target spends budget that another operator needs, and
  the report has to defend every experiment.
- When the direction is unclear, prefer re-diagnosis over a new hypothesis. Three flat rounds
  usually mean the bottleneck moved, not that the ideas are bad.
