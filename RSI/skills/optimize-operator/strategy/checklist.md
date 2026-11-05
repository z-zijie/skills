# optimize-operator strategy

## The campaign this opens

One operator, one budget, one stop condition. The stages are fixed, and the orchestrator owns the
transitions.

| Stage | Skill | Done when |
| --- | --- | --- |
| preflight | this one | every precondition holds |
| baseline | `run-evaluator` | experiment 0 measured, profiled, diagnosed, recorded |
| round | `propose-hypothesis` | one W2 round has reached the ledger |
| decide | `plan-campaign` | the next move is continue, change_direction, combine or stop |
| report | `report-campaign` | the campaign's figures and its rewrite recommendation exist |

Within a round, W2 fixes nine steps: profile, diagnose, retrieve, hypothesize, implement, evaluate,
inspect, verify, record. Correctness failures and `not_implemented` verdicts loop back to the
implementer at most twice, and an experiment that never lands is recorded as an implementation
failure that counts toward no rule.

## The four stop conditions

| Condition | Meant by | What the report says |
| --- | --- | --- |
| `target_attainment_reached` | attainment at or above the target | the remaining gap, and whether the bound is trustworthy |
| `budget_exhausted` | experiments, device seconds or tokens spent | gain per experiment, and whether the trend was still rising |
| `five_rounds_without_significant_gain` | five rounds, no win, no open route above noise | which routes were refuted |
| `no_hypothesis_above_noise` | every remaining route prices inside the noise band | that the playbook is exhausted for this signature |

Set the target from the operator's spec and the measured layer, not from ambition. A target the
roofline cannot reach produces a campaign that spends its budget failing.

## When preflight refuses

Each refusal has one fix, and it is always outside the campaign.

| Failed check | Fix |
| --- | --- |
| `operator_exists` | Run W1 onboarding: write `spec.yaml`, add reference and baseline kernels |
| `spec_parses` | The spec's cost model or shapes are malformed; the evaluator will not measure it |
| `budget_granted` | A human grants a budget; zero experiments is a refusal, not a default |
| `target_set` | Set the target attainment in `(0, 100]` |
| `noise_floor_known` | Run calibration or the A/A test; an unknown floor makes every win unmeasurable |
| `baseline_on_record` | Record experiment 0 before optimizing anything |

A start that fails preflight spends nothing. That is deliberate: the failure mode it prevents is
a campaign that begins from a stale baseline and reports a large gain from an unrelated change.

## The budget

- Three counters: experiments, device seconds, tokens. Whichever runs out first stops the
  campaign, and the stop reason is the same.
- Replay is outside the campaign budget. A scaffold change is paid for from the outer loop's own
  weekly cap, and a campaign that funds it hides the real cost of the system.
- The baseline is not free. Experiment 0 costs a measurement, a profile and a diagnosis, and a
  campaign started with a budget of one has no rounds in it.
- Ten experiments per operator is the plan's starting figure for a replay configuration; a real
  campaign is sized by how far the operator is from its bound.

## What this skill must not do

- Call another skill. The orchestrator reads `stage_plan` and runs it.
- Read or write `benchmark/holdout*`. The campaign runs on dev operators, and the holdout is the
  replay judge's.
- Change the evaluator, the spec, the tolerances or a schema. All four are human-only, and the
  gates are emitted in the output so the orchestrator can enforce them.
- Write a rule, a playbook or a knowledge entry. Campaigns produce experiments and proposals, and
  the gates decide what lands.
- Continue past a stop condition because one more experiment looks promising. The next campaign
  is the place for that, with a fresh budget and a report to justify it.
