---
name: design-microbench
description: When prediction error is large, propose a microbenchmark that isolates one mechanism and produces one hardware parameter. Use when a rule's mean prediction error or a cluster of experiments misses by more than the hole threshold, and use it instead of a playbook edit in that case.
---

# design-microbench

The hardware model is wrong somewhere. A playbook cannot fix that: the hypothesizer will keep
pricing changes from a model that does not fit, and every downstream prediction inherits the
error. This skill proposes the measurement that would close the hole.

It never writes the microbenchmark. Microbenchmark code is Layer 0, so the proposal is the most
this loop produces, and its gate is always `human_only`.

## When it runs

- When a rule's `mean_prediction_error` exceeds the hole threshold (0.2 in log-ratio terms).
- When `analyze-failures` reports a `large_prediction_error` cluster.
- When a campaign's `prediction_error_watch` names recent misses the orchestrator has queued.
- After calibration moved a parameter and the affected rules no longer predict well.

Not for: a tactic that failed. A refuted hypothesis is evidence about the playbook, and a
microbenchmark proposal against it spends the frozen suite's scarcest slot on the wrong question.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `mechanism` | The one mechanism the measurement isolates |
| `metric`, `parameter`, `unit`, `used_by` | What is observed and what the measurement produces |
| `prediction_errors` | The experiments that missed, with their log-ratio errors |
| `extend_suite_item`, `new_item` | Extend an existing item, or argue for a new one |
| `sweep` | The one dimension the mechanism depends on, and its values |
| `would_settle` | The question the result answers |

## Steps

1. Confirm the hole. Done when at least one experiment's prediction error exceeds the threshold
   and the misses share a mechanism rather than being scattered across several.
2. Name the metric and find the suite item that already observes it. Done when the proposal
   extends an existing item or states why no existing item can be extended.
3. State the sweep and the parameter. Done when the measurement yields a curve or a number that
   a rule's `effect` range or the roofline can consume, and `used_by` names which.
4. Run `python3 scripts/design.py --input <file>`. Done when it prints one result object. When
   `problems` is not empty, the measurement is not justified yet; fix the input rather than
   suppressing the refusal.
5. Check it against `contract/output.schema.json`. Done when validation reports no problems.
6. Write the proposal under `kb/proposals/microbench/` with `gate: human_only` and
   `status: needs_human`, and surface `human_decision.question` to the human. Done when the
   proposal validates and the question is the one a human actually has to answer.

## Output

`contract/output.schema.json`. Read these:

- `proposal` - `kind: microbench`, `target: evaluator/microbench/suite.py`, `gate: human_only`,
  `status: needs_human`, and the experiment ids that justify it.
- `design` - the mechanism, the sweep, the parameter it produces and who consumes it, the
  fingerprint requirement, and the expiry rule.
- `design.fallback_if_rejected` - what happens if the human declines: the affected rules keep a
  wide prediction range and stay under review, and the hole stays open. Say so rather than
  pretending there is no cost.
- `problems` - why the proposal is not yet justified, when it is not.
- `human_decision` - the question, why it is human-only, and what happens either way.

## Judging principles

- One mechanism per measurement. A microbenchmark that varies two things produces a number
  nobody can attribute, which is the same failure as an experiment that changes two things.
- Prefer extending the suite to adding to it. Fourteen items already cover the device's
  temperament, and a fifteenth that measures the same metric with a different name makes the
  suite harder to read without making the model better.
- Justify with misses, not with curiosity. The evidence is the experiments that missed and by how
  much; a mechanism that merely sounds under-measured is not a reason to spend a suite slot.
- Name what consumes the parameter. A measurement whose result no rule, threshold or roofline
  term reads is a number that will be forgotten, and it costs the same device time as one that
  matters.
- State the expiry. Every measured parameter carries a fingerprint and a date, and a result that
  is never re-measured silently becomes wrong.
- Treat approval as the point of the step. The proposal's job is to make a human's decision easy:
  what will be measured, what it settles, what it costs, and what happens without it.
