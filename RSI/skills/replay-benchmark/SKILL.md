---
name: replay-benchmark
description: Replay the old and the new scaffold on dev and the held-out split and return merge or
  rollback. Use only from workflow W5 step 5, after edit-scaffold has produced exactly one change
  on a branch, and never from inside a campaign.
---

# replay-benchmark

Script phase, and the only skill besides the human that reads the held-out split. It replays one
scaffold change, twice if needed, and returns `merge` or `rollback` with the reason. Everything it
decides is a mechanical rule from the replay protocol.

## When it runs

- Step 5 of W5, on a branch holding exactly one scaffold change.
- After `edit-scaffold` has written the change note, and before anything is merged.

Two changes tested together cannot be attributed, so a branch with more than one change is
`human_only` and is not replayed.

## The holdout boundary

`benchmark/holdout.txt` is read here and nowhere else. Its contents never leave this skill: the
output carries per-split aggregates and the decision, and the operator names come from the caller's
configuration, not from this skill's reading of the split. Outer-loop agents receive "pass" or
"fail".

## What you receive

One JSON object matching `contract/input.schema.json`: the `proposal` id and summary, the `configs`
(each one operator with its dev or holdout measurements for the old and new scaffolds), the
`budget`, the `repeats`, and `stage` — `two_stage_screen`, `offline_ledger`, or `full`.

## Steps

1. Confirm the branch holds one change. Done when the change summary names one change and one
   target; otherwise emit `human_only` and stop.
2. Confirm `repeats` is at least the protocol minimum. Done when it is; a replay below the minimum
   cannot separate signal from noise, so emit `needs_more_repeats` instead of a decision.
3. Hand the configs to `orchestrator.replay.evaluate_change` (or `screen` for the cheap subset,
   or `OfflineJudge` for a diagnosis-only change). Done when it returns a primary metric with an
   interval, a holdout flag, a secondary flag and a decision.
4. Read the decision as given: merge when the dev interval excludes zero, holdout did not regress
   and no secondary metric degraded beyond its tolerance. Done when the rationale names which of
   the three conditions failed, for a rollback.
5. Emit the replay report, validating it against `schemas/replay-report.schema.json`. Done when the
   report carries `primary_metric`, `holdout_regressed`, `secondary_regressed` and `decision`.

## Output

`{status, report, problems, notes, screens}` per `contract/output.schema.json`. `report` is the
object `schemas/replay-report.schema.json` defines: the configs, the primary metric with its
interval, the two regression flags, the screening stage and the decision.

## Judging principles

- Report the decision the protocol produced. A replay that disagrees with its own interval is worse
  than no replay, because it launders a guess into a merge.
- Keep the aggregates and drop the operator detail when reporting outward. The decision travels;
  the holdout measurements do not.
- Distinguish a failed screen from a failed replay. A screen that does not clear its cheap subset
  costs nothing further; a full replay that fails has already been paid for, and both get logged.
- Log both outcomes. A rollback written down is a change nobody repeats next month; a rollback
  forgotten is next month's experiment.
