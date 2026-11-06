# distill-rules strategy

## What the life cycle decides, and what you decide

The script computes the status. You supply the three things the computation is only as good as:

1. **The scope.** Too wide and prediction error rises until the life cycle demotes the card;
   too narrow and the rule never retrieves. Start at the tag the cards were built on plus one
   class, and widen only when a second signature confirms the same mechanism.
2. **The evidence boundary.** A confirmation counts only if the verifier said the mechanism
   landed and the timing was unsealed. `not_implemented` and `implausible` entries never enter
   a card, and an unexplained speedup adds no positive evidence until its real mechanism is
   named.
3. **The operator map.** `operators >= 2` is what separates established from candidate. Count
   operators, not experiments: four confirmations on one operator still leave a rule a
   candidate, and saying so plainly is better than quietly widening the scope to promote it.

## Updating an existing rule

When a stored card covers the group, the proposal is an update rather than a create:

- Keep the stored `id`. A new id for the same statement forks the evidence and halves both
  cards' confirmation counts.
- Union the evidence lists, then de-duplicate. Re-listing an experiment already on the card
  inflates the confirmation rate and is the fastest way to fabricate an established rule.
- Recompute `speedup_median` and `range` over all confirmations, not just the new ones.
- Recompute `mean_prediction_error` the same way. A rising error is the signal that the scope
  needs narrowing, and it arrives before the life cycle demotes the card.
- Add a `counter_example_note` whenever a refutation enters the card, naming what was different
  about it. The build plan's SoA rule carries the 64-byte-element case for exactly this reason;
  a refutation without a note teaches nothing and gets re-learned later.

## Writing the statement

The shape is fixed: under condition C, transformation T moves metric M by delta.

- Name a canonical metric from `tooling/rsi/metrics.py`. A statement about a profiler
  expression will not survive a device change and will fail `validate_rule`.
- Name the conditions in words a reader can match to a signature: the class, the tags, and the
  shape or dtype constraint that mattered.
- Give the effect as a median with a range, and put the range in `effect.range`. A single
  number hides that the rule was measured four times with different results.
- Keep the mechanism on the card. It is what the hypothesizer needs to write a falsifiable
  prediction, and a rule without one produces cards nobody can verify.

## Going to anti-patterns

A group with no confirmation at all is not a rule. It becomes an anti-pattern proposal:

- State the refuted form concretely: what was tried, under which signature, and what happened.
- Keep it in the same proposal run, as a separate proposal with `kind: antipattern` and target
  `kb/antipatterns/`. The two stores answer different questions and retrieval caps them
  separately.
- Include the operator count. A transformation refuted on one operator is a scope note; refuted
  on three it is a genuine trap.

## Common failures, and what they mean

| Symptom in the proposal set | Meaning | Fix |
| --- | --- | --- |
| Many `candidate` cards, no `established` | Evidence is spread thin across narrow scopes | Widen one scope at a time and let the operator count grow |
| One `deprecated` card per run | The playbook still offers a dead transformation | Remove it from the priority order through `edit-scaffold` |
| `under_review` on every card | `hardware_changed` is set, or prediction errors rose together | Check calibration date before proposing anything else |
| The same group skipped every run | The group has no confirmation, or the rule failed validation | Read `skipped.reason`; it names which |
| Statements that read "always" | Scope was dropped while writing | Rewrite as condition, transformation, metric, delta |

## What this skill must not do

- Write to `kb/rules`, `kb/patterns`, `kb/antipatterns`, `kb/conjectures`, `skills/` or
  `playbooks/`. Proposals only.
- Change a status by hand. The life cycle is the whole mechanism; a hand-edited status
  is indistinguishable from a fabricated one.
- Touch the evaluator, the tolerances or the holdout split. None of them are evidence inputs
  here, and reading the holdout would invalidate every rule distilled afterwards.
