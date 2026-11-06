---
name: optimize-operator
description: The entry point. Start, resume or inspect a full optimization campaign for one operator, given a budget and a target attainment. Use when a human says "optimize operator X", and for the status question about a campaign already running.
---

# optimize-operator

The one skill a human invokes directly. It opens a campaign for one operator, refuses to open one
that cannot be measured, and hands the orchestrator a stage sequence with a budget and a stop
condition attached.

It drives the state machine rather than calling skills. `stage_plan` and `round_steps` name what
the orchestrator runs and in what order; this skill never invokes another skill and never touches
a kernel, a profile or the ledger.

## When it runs

- A human asks for one operator to be optimized. `mode: start`.
- A paused campaign is being picked up again. `mode: resume`.
- Someone asks where a campaign stands. `mode: status`, which changes nothing.
- After W1 onboarding, when the operator has a spec, a baseline and a noise floor.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `op` | The operator; its `operators/<op>/spec.yaml` must exist |
| `mode` | `start`, `resume` or `status` |
| `budget` | Experiments, device seconds and tokens this campaign may spend |
| `target_attainment_pct` | Stop target, 85 by default |
| `noise_threshold` | The measured A/A floor; unknown is a refusal, not a zero |
| `baseline_exp_id` | Experiment 0 must already be on record |
| `attainment_pct`, `last_round_gained`, `no_hypothesis_above_noise` | State that decides a stop |
| `read_ledger` | False keeps the run pure over its input |

## Steps

1. Preflight. Done when every check in `preflight.checks` is ok: the operator exists, a budget was
   granted, a target is set, a noise floor is measured, and the baseline is on record. Each failed
   check names what to fix, and a start with a failure spends nothing.
2. Read the state. Done when `state.state` and `state.stop_condition` are known: `ready_to_start`,
   `started`, or `stopped` with one of the four conditions.
3. Run `python3 scripts/campaign.py --input <file>`. Done when it prints one campaign object.
4. Check the object against `contract/output.schema.json`. Done when validation reports no
   problems.
5. On a refusal, read `strategy/checklist.md` section "When preflight refuses" and hand the human
   the named fix. Done when the refusal has become one specific action, not a list of concerns.
6. On a start or resume that is ready, write the campaign state under `orchestrator/state/` and
   hand `next` and `stage_plan` to the orchestrator. Done when the orchestrator has the stage
   sequence, the budget and the stop conditions from this object and from nowhere else.

## Output

`contract/output.schema.json`. Read these:

- `preflight` - one check per precondition, each with a `detail` a human can act on.
- `state` - the campaign's standing: `progress` counts by verdict, the budget remaining, and
  `stop_condition` when it has stopped.
- `stage_plan` - five stages from preflight to report, each naming the skill the orchestrator runs
  and the condition that ends it.
- `round_steps` - the nine W2 steps inside one round, in order.
- `gates`, `write_boundary` - what the outer loop may change and who may write where. Quote these
  rather than restating them.
- `next` - the stage and skill to run immediately. `refusals` names what blocked a start.

## Judging principles

- Refuse to start on a missing noise floor. A campaign measured against an unknown floor produces
  wins that are indistinguishable from variation, and every rule distilled from it inherits that.
- Spend the budget as granted. Experiments, device seconds and tokens are the human's, and a
  campaign that stops because the budget ran out is a normal outcome rather than a failure.
- Keep replay outside the campaign budget. Scaffold changes are paid for separately, and a
  campaign that quietly funds them is hiding its own cost.
- Start from the baseline, not from a copy of it. Experiment 0 is what the gain is measured
  against, and a campaign that re-derives its own baseline cannot be compared with any other.
- Let the orchestrator own sequencing. The stage list is data; calling the next skill from here
  would make the campaign untraceable and impossible to replay.
- End at the target. Continuing past it spends a budget another operator needs, and the report has
  to defend every experiment it was given.
