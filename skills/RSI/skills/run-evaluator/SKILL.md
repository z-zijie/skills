---
name: run-evaluator
description: Measure one candidate kernel through the frozen evaluator and return its signed
  result with correctness and timing. Use for every candidate measurement and whenever a later
  step needs a timing number it is allowed to trust.
---

# run-evaluator

Script phase. The evaluator decides correctness, measures timing and signs the result; this
skill invokes it once, checks what came back, and reports it in one object. No skill measures a
kernel any other way.

## When it runs

- Step 5 of W2, on the candidate `implement-change` produced.
- Step 4 of W1, to record the baseline as experiment zero.

The timing it returns is sealed: the orchestrator holds it back from `verify-mechanism` until a
verdict exists, so the verifier cannot be biased by "it got faster". The A/A noise floor is a
separate evaluator command with its own output and is not this skill's result.

## What you receive

One JSON object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `op` | Operator id; its `operators/<op>/spec.yaml` supplies shapes and tolerances |
| `candidate` | Kernel module to measure, absolute or relative to the RSI root |
| `exp_kind` | `evaluate` for a candidate, `operator_baseline` for experiment zero |
| `samples`, `warmup`, `seed` | Timing protocol; leave unset to use the evaluator's defaults |
| `backend` | Execution backend name; leave unset to use the device description |
| `out` | Where the signed result is kept, default under `ledger/artifacts/` |
| `timeout_s` | Wall-clock cap for one run |

## Steps

1. Resolve the candidate and confirm the file exists. Done when it does; when it does not,
   report `status: unavailable` and stop, because a mistyped candidate that silently falls back
   to the baseline would read as a perfect measurement.
2. Invoke the evaluator CLI, never its internals:
   `python3 -m evaluator.cli evaluate --op <op> --candidate <path> --out <out>`.
   Done when the process exits zero and prints JSON.
3. Validate the emitted object against `schemas/result.schema.json`. Done when the validator
   reports no problems; otherwise report `status: schema_invalid` with every problem.
4. Verify the signature with `rsi.ledger.verify`. Done when it matches the body; a mismatch is
   `status: signature_invalid` and the result is never passed on.
5. Report continuously. `status: ok` when correctness passed, `status: correctness_failed` when
   it did not — both carry the signed result, which the ledger accepts either way.

## Output

`{status, result, problems, command}` according to `contract/output.schema.json`.

- `result` is the signed evaluator result; `schemas/result.schema.json` is its authoritative
  definition, and `contract/output.schema.json` inlines the shape only because contracts may not
  reference another file. It carries correctness with failing cases, timing with candidate and
  baseline medians, the sample count, the noise threshold when calibration has measured one, and
  the signature block.
- `problems` is empty unless a check failed, and then it names the check and the reason.
- `command` is the argv that ran, so the run can be reproduced.

## Reading the result

- `correctness.passed: false` ends the round: a fast wrong kernel is not a candidate. The
  failing cases in `correctness.failures` go back to the implementer.
- `timing.significant` is the evaluator's own win rule: the lower confidence bound of the
  speedup cleared the measured noise floor. Report it as given.
- `timing.noise_threshold: null` means calibration has not measured this device yet. The number
  is unknown rather than one; say so, and the orchestrator queues calibration before judging a
  win.
- `guards.implausible_speedup: true` pauses the experiment for human review. Report it plainly
  and stop; nothing is promoted on that result.

## Failure modes worth naming

- A missing signing key stops the evaluator from producing a signed result at all. Treat it as a
  setup failure, not as a measurement.
- Exit status zero is not evidence on its own: every result this skill emits has passed both the
  schema check and the signature check, or it is labelled with the check that failed.
