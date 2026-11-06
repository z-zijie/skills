---
name: calibrate-hardware
description: Run the microbenchmark suite on this device and update the measured hardware
  parameters plus the signature thresholds derived from them. Use on a new device, after a
  runtime or compiler upgrade, on the monthly routine, and whenever a prediction error says the
  hardware model has a hole.
---

# calibrate-hardware

Script phase. The evaluator owns the microbenchmark code; this skill runs the suite, folds the
results into `kb/hardware/measured/<hw_id>.yaml`, derives the thresholds that tags depend on, and
reports what moved.

## When it runs

- Workflow W4: a new device, a driver or runtime upgrade, the monthly routine.
- When a campaign's prediction errors cluster above the hole threshold, which is the signal that
  the machine's numbers are stale rather than the operator's.

Nothing else in the system measures the machine. Every later number — the roofline's two terms,
the noise floor a win must clear, the launch floor for small shapes — comes from this file.

## What you receive

One JSON object matching `contract/input.schema.json`: a `suite` name (`core`, `quick`, or a name
the evaluator registers), `samples`, `seed`, `quick`, an optional `backend`, and `hw_id` when the
device description does not name itself. A `measured_file` may be supplied to replay a stored
calibration offline.

## Steps

1. Run the suite through the evaluator CLI, never its internals:
   `python3 -m evaluator.cli calibrate --suite <name> --out <path>`. Done when it exits zero and
   prints one report.
2. Read `skipped` and name every microbenchmark that did not run with its reason. Done when each
   skipped entry has a reason; a suite that silently measured less than it claims is the failure
   mode that matters here.
3. Fold the report into `kb/hardware/measured/<hw_id>.yaml` with the environment fingerprint and
   the date. Done when the file loads and its `hw_id` matches the device.
4. Compare with the previous run: the suite records `moved` and `history`. Done when every
   parameter that moved beyond its change threshold is listed, and `under_review` is set when any
   did.
5. Derive the threshold block with `propose_thresholds` and record it next to the parameters.
   Done when every threshold the tag set consumes has a value, or is explicitly left at the
   device-neutral default.
6. Re-run each operator's `best/` when a parameter moved. Done when each operator's attainment is
   down against the same files it was measured with before; a regression after a calibration is a
   real signal, and a driver upgrade is the usual cause.

## Output

`{status, hw_id, measured, moved, thresholds, skipped, problems, notes}` per
`contract/output.schema.json`. `measured` is the merged file as written; `moved` names each
parameter that shifted beyond its threshold with before, after and the relative change.

## Judging principles

- Treat a measured parameter as expiring. It carries the fingerprint and the date that produced
  it, and a number from another fingerprint is not a comparison, it is a new measurement.
- Prefer a skipped microbenchmark to a fabricated one. A capability the backend lacks is reported;
  the parameter stays absent, and everything downstream treats it as unknown.
- Flag rather than rewrite. When a parameter moves, the rules that depend on it go `under review`
  through a proposal; their statements are not silently re-tuned here.
