---
name: record-experiment
description: Validate one finished experiment, archive its artifacts and append exactly one
  entry to the append-only ledger. Use at W2 step 7 once the verdict exists and the timing is
  unsealed; it refuses any evaluator result whose signature does not verify.
---

# record-experiment

Script phase. One experiment becomes one ledger line. This skill is the only writer of
`ledger/experiments.jsonl`: it validates, archives and appends, and it refuses anything it cannot
prove came from the evaluator.

## When it runs

- Step 7 of W2, after `verify-mechanism` returned a verdict and the orchestrator unsealed the
  timing.
- Step 4 of W1, for the baseline as experiment zero, with no hypothesis card.

It runs once per experiment. A duplicate `exp_id` is a refusal, not an update.

## What you receive

One JSON object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `op`, `parent`, `hw_id` | Identity of the experiment |
| `result_file` | The signed evaluator result; unsigned means refused |
| `hypothesis`, `hypothesis_file` | The card, as `schemas/hypothesis.schema.json` defines it |
| `signature`, `signature_file` | The bottleneck signature the round started from |
| `verdict`, `verifier_notes` | From `verify-mechanism`; this skill never repairs one |
| `diff_file`, `profile_file`, `binary_file` | Artifacts archived under `ledger/artifacts/<exp_id>/` |
| `timing_before`, `counters_before`, `counters_after` | Measured state around the change |
| `cost` | Tokens, device seconds, implementation retries |
| `dry_run` | Validate the entry and write nothing |

## Steps

1. Verify the signature of the result with `rsi.ledger.verify`. Done when it matches its body;
   otherwise `status: refused_unsigned` and nothing is appended.
2. Validate the result, the hypothesis card and the signature against their shared schemas. Done
   when all three pass.
3. Allocate `exp_id` with `rsi.ledger.next_exp_id`. Done when it is one above the highest present.
4. Build the entry: copy `speedup` and `significant` from the signed timing, compute
   `prediction_error` as the absolute log ratio of measured over predicted speedup, take
   `scaffold_commit` from the working tree. Done when every required field of
   `schemas/ledger-entry.schema.json` is filled from an artifact.
5. Canonicalize the counters and reject any name the registry does not know. Done when
   `rsi.metrics.check_names` returns nothing.
6. Archive `diff.patch`, `profile.json`, `binary.json` and `result.json` under
   `ledger/artifacts/<exp_id>/`. Done when each is on disk or is listed in `missing_artifacts`.
7. Append with `rsi.ledger.append`, which re-validates the entry and refuses a duplicate id.
   Done when exactly one line was added and it reads back.

## Output

`{status, exp_id, entry, archived, missing_artifacts, problems, notes}` per
`contract/output.schema.json`. `entry` is the object `schemas/ledger-entry.schema.json` defines; it
carries `scaffold_commit`, so a later gain can be attributed to the scaffold that was live, and
`evaluator_signature`, so a line can be re-verified by an audit.

`status` is `ok`, `dry_run`, or one of the four refusals: `refused_unsigned`,
`refused_duplicate`, `refused_unregistered_metric`, `schema_invalid`.

## Judging principles

- Derive every number from an artifact. A speedup typed from memory is indistinguishable from a
  fabricated one, and the ledger is what the distiller counts as evidence.
- Record `not_implemented` experiments with the verdict intact. They count toward no rule, and
  their rate is the measure of the implementer's tier, so dropping them hides the one signal that
  decides a routing change.
- Keep the evaluator's `significant` flag as given. It already encodes this device's measured
  noise threshold; deciding it again here would put a second, unmeasured threshold in the record.
- Report a named artifact that does not exist in `missing_artifacts`. A reference to nothing is
  worse than an honest gap, because a gap is visible.

`strategy/checklist.md` states the three refusals and lists which fields must stay derived.
