# record-experiment: what is evidence and what is not

The ledger is the only thing the distiller counts, the only thing a campaign plan reads, and the
only history a replay can be scored against. So the question this skill answers is narrow: has
this experiment been *proven* to have happened as described.

## The three refusals

| Refusal | Why it is structural |
| --- | --- |
| The result's signature does not verify | Without the signature, a result file is just text. The whole point of signing is that an agent with filesystem access cannot turn a guess into experience. |
| The `exp_id` already exists | The ledger is append-only. A re-run is a new experiment with a new id, or it is an overwrite of history. |
| A counter name is not canonical | No rule can be scoped to a name that has no unit and no direction, and no verifier can compare it across devices. Legacy spellings that the registry aliases are accepted and rewritten. |

Every other problem is reported and stops the append without changing the ledger. Nothing is ever
written in a half-valid state.

## What gets derived, not supplied

- `speedup`, `significant`: copied from the signed result's timing block. The evaluator already
  applied this device's measured noise threshold; a second opinion here would be a second,
  unmeasured threshold.
- `prediction_error`: `|ln(measured / predicted)|` with the predicted speedup taken from the
  card's `predictions.speedup.expect`. This is the number that decides whether the hardware model
  has a hole, so it must be computed rather than estimated.
- `scaffold_commit`: read from the working tree, `+dirty` when the tree is dirty. Hiding a dirty
  tree would make attribution impossible.
- `evaluator_signature`: stored verbatim so a later audit can re-verify a line.

## Artifacts

`ledger/artifacts/<exp_id>/` receives four files: `diff.patch`, `profile.json`, `binary.json` and
the signed `result.json`. The first three come from whatever the round produced; an artifact that
was named but never existed is listed in `missing_artifacts` and the entry records the reference
as absent. A reference that points at nothing is worse than an honest gap.

`not_implemented` experiments are recorded like any other. They count toward no rule, and their
rate is exactly the measurement that decides whether the implementer's tier is too weak, so
dropping them would destroy that signal.

## Fields that come from outside this skill

- `hypothesis`: written by `propose-hypothesis`, validated here against
  `schemas/hypothesis.schema.json` before it is stored.
- `signature`: written by `diagnose-bottleneck`, validated here.
- `verdict` and `verifier_notes`: written by `verify-mechanism`. This skill does not form a
  verdict and does not repair one.

## What this skill never does

- It never edits a line in `ledger/experiments.jsonl`.
- It never recomputes a speedup, a confidence interval or a significance decision.
- It never decides whether a change is promoted; promotion is the evaluator's promotion step.
