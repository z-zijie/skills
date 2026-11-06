# replay-benchmark: the protocol, and why the boundary is here

Replay is the most expensive thing the system does and the easiest to accidentally weaken, so the
protocol is code in `orchestrator/replay.py` and this skill calls it.

## The five conditions

A change merges only when all of these hold. Any one failing is a rollback, and the rationale names
which one failed:

| Condition | Where it lives |
| --- | --- |
| At least three repeats per configuration | `replay.MIN_REPEATS` |
| The dev interval for attainment gain per experiment excludes zero | `evaluate_change` |
| The held-out split does not regress | `HOLDOUT_REGRESSION_TOLERANCE` |
| No secondary metric degrades beyond tolerance | `SECONDARY_TOLERANCE` |
| One change on the branch | checked here, before anything is spent |

The secondary metrics are the not-implemented rate, the prediction error and the cost. They matter
because a change can raise attainment per experiment while making the implementer fail more often,
which costs more than the attainment is worth.

## Two-stage screening

The full replay is affordable only after a cheap screen passes. The screen runs two or three cheap
dev operators and returns `needs_more_repeats` (meaning: now run the full set) or `rollback`
(meaning: not worth the cost). A screen that passes has not decided anything; it has bought the
right to spend.

`offline_ledger` is the third stage, for changes that touch only diagnosis, retrieval or hypothesis
ranking. Those can be judged against the ledger: does the new scaffold rank historically confirmed
experiments higher? No device is involved, which is what makes a whole class of change cheap to
test.

## The holdout boundary

`benchmark/holdout.txt` is read by the replay judge and by a human. Nothing else.

This skill's output is the boundary in practice: `split_totals` carries a config count, a mean and a
minimum per split, and `report.configs` carries what the caller supplied. What never appears is a
statement that names which held-out operator moved. The rationale says "holdout regressed"; it does
not say where, because a scaffold tuned on that sentence is a scaffold tuned on the holdout.

Rotating the holdout set, and folding the retired operators into dev, is a human maintenance task.
It exists because a holdout that stays fixed for hundreds of merges stops being held out.

## What is logged

Both outcomes go into `CHANGELOG-scaffold.md`, with the proposal, the decision, the primary metric
and the rationale. A merge without a line did not happen; a rollback without a line will be tried
again next month, at full cost.

## Reading a decision

- `merge`: the change is real on dev, holds on holdout, and costs nothing else. Early on, a human
  approves every merge even when the protocol says merge.
- `rollback`: one of the five conditions failed, and the rationale names it. Rewrite the change or
  discard it; do not re-run it hoping for a different interval.
- `needs_more_repeats`: not a decision. Either run more repeats, or advance from screen to full.
- `human_only`: the branch carried something the protocol cannot attribute, or the change touches
  what only a human may change.

## What this skill never does

- It never re-implements a threshold. Every number in the decision belongs to the protocol module.
- It never runs two changes together, and it never replays a branch it has not been told the change
  summary of.
- It never reports a holdout measurement outward, not even a rounded one.
