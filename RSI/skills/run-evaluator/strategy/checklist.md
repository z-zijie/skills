# run-evaluator: what the checks mean

The script answers three questions in a fixed order. Each answer has exactly one consequence, so
a failure never has to be interpreted.

## 1. Did the evaluator answer at all?

| Observation | Consequence |
| --- | --- |
| Non-zero exit, or no JSON on stdout | `unavailable`; the round stops and the orchestrator decides whether to retry |
| Candidate module not found | `unavailable`; a path typo must never fall back to the baseline |

The evaluator is invoked through its CLI only. Reading its source, importing its modules or
post-processing its numbers belongs to nobody.

## 2. Is the object the shape the contract promises?

Two classes of schema problem, deliberately separated:

- **Blocking.** A required field is missing or mistyped. The next step would read a field that is
  not there, so the result is `schema_invalid` and stops.
- **Drift.** The evaluator emitted an extra block the contract does not name yet. The object is
  still usable, so the run continues and the extra key is recorded in `notes` under
  `schema_drift`. Drift that nobody sees is how a contract silently rots; drift that blocks the
  loop is how a good measurement gets thrown away.

## 3. Does the signature match the body?

`hmac-sha256` over the canonical JSON body, keyed by the evaluator's user. A mismatch means the
file changed after it was signed, so it is `signature_invalid` and is never passed on and never
recorded. A missing key is a setup failure, not a measurement.

The signature is integrity against an agent that can write the filesystem. It is not secrecy and
not a boundary against the owner, and the skill does not pretend otherwise.

## 4. Reading a result that passed

- `correctness.passed` false ends the round. `correctness.failures` carries shape, dtype, seed and
  the tolerance that was exceeded; that list goes back to the implementer as the retry target.
- `timing.significant` is the evaluator's win rule and is reported, never recomputed. It is true
  only when the lower confidence bound of the speedup cleared the measured noise floor.
- `timing.noise_threshold` null means this device has no calibration record. Behave as if the
  threshold is unknown, not as if it is 1.0: a win cannot be called against a number nobody
  measured.
- `timing.setup_cap_us` non-null with `setup_us` near it means setup is close to its ceiling. Work
  migrating out of the timed region shows up here first.
- `guards.implausible_speedup` true pauses the experiment for a human. Report it, promote nothing.
- `timing.interleaved` false means the arms were not alternated, so drift could be hiding as
  speedup. Worth flagging in the ledger entry's `verifier_notes`.

## 5. What this skill never does

- It never edits the evaluator, the spec, or a tolerance.
- It never promotes a candidate; promotion is the evaluator's decision on a later run.
- It never reports a timing number to the verifier path. The orchestrator seals it.
