---
name: collect-profile
description: Collect one profiler run over a candidate and return the profile reduced to
  canonical metrics. Use after a candidate compiles and before diagnose-bottleneck, and again
  after a change when the verifier needs before and after counters.
---

# collect-profile

Script phase. The evaluator runs the profiler adapter; this skill canonicalizes what came back,
records which metrics the device cannot produce, and emits one normalized `profile.json`. A
profiler report never reaches a model as raw text.

## When it runs

- Step 1 of W2, on `best/` before a hypothesis is written.
- Step 6 of W2, on the candidate, so `verify-mechanism` has before and after counters.
- Whenever calibration or a device change may have altered the available metric set.

## What you receive

One JSON object matching `contract/input.schema.json`: `op`, `candidate`, the `command` the
profiler wraps, an optional `adapter` name, and where to write the raw report. When a profiler is
not installed the run still succeeds: it returns the null adapter's profile, with every metric
listed in `unavailable` rather than zeroed.

## Steps

1. Confirm the candidate module exists and the profile command is a non-empty argv.
   Done when both are present; otherwise report `status: unavailable` and name what is missing.
2. Invoke `python3 -m evaluator.cli profile --op <op> --candidate <path> --command <argv...>`
   with the adapter selection and output directory. Done when the CLI exits zero and prints JSON.
3. Validate the emitted object against `schemas/profile.schema.json`. Done when the validator
   reports no problems.
4. Canonicalize every metric name with `rsi.metrics.normalise`. A name that is not in the
   registry moves to `unavailable` with a note, because a number nobody can name is not evidence.
5. Reconcile `unavailable`: every registered metric the adapter did not produce is listed there.
   Done when `metrics` and `unavailable` together partition the registry.
6. Emit the profile. Done when `schemas/profile.schema.json` accepts the final object.

## Output

`{status, profile, problems, notes}` per `contract/output.schema.json`. `profile` is the object
`schemas/profile.schema.json` defines: `op`, `candidate`, `hw_id`, `env_fingerprint`, `adapter`
and its version, the transaction granularity behind `sectors_per_request`, the canonical
`metrics` map, the `unavailable` list, and the archive path of the untouched raw report.

## Judging principles

- Treat an unavailable metric as unknown. Substituting zero turns "this device cannot see
  scratchpad conflicts" into "there are no conflicts", which is a different claim.
- Keep the raw report beside the normalized file: an adapter mapping is data and may be wrong,
  so a human must be able to re-derive a number without re-running the device.
- Compare two profiles only inside one `env_fingerprint`. A profiling run is not a timing run,
  so never quote the profile's own wall time as performance.
