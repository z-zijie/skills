---
name: revisit-closures
description: Decide which written-off directions have to be reopened before a campaign stops, and with which experiment. Use when plan-campaign proposes stop or change_direction, after three flat rounds, and whenever two or more closures are open.
---

# revisit-closures

The skills that judge one round answer "is this change good?". This one answers the question the
campaign cannot answer about itself: **did we write a direction off, or did we only fail to
implement it?** A compile error, a wrong arity, a probe whose own baseline check failed, an API
form that is declared and not linked, or a single point tried out of a range are all recorded as
"this does not work" — and the playbook then stops offering a direction that was never really
tried.

The campaign stop conditions say "no remaining hypothesis predicts a gain above the noise floor".
That sentence is true only if the refuted directions were refuted. This skill is how the
orchestrator checks the premise instead of assuming it.

## What a closure is

Every `refuted` or `not_implemented` entry carries a **closure record**: the direction it closed,
the evidence class, how many points of the direction's parameter were tried, and the condition
under which the direction deserves another experiment. The class is derived from the verifier's
own checks, never chosen by the implementer:

| `evidence` | What the checks showed | Negative evidence about the direction? |
| --- | --- | --- |
| `implementation` | a group A or B check failed, or the verdict is `not_implemented` | no: nothing landed |
| `measurement` | groups A, B and C passed and the score did not follow | no: this is a mislabelled confirmation |
| `premise` | the mechanism landed, the counters were measured, they did not move | yes |

A direction is **retirable** only when its closure is `premise`, it was tried at `MIN_POINTS` or
more points of its parameter, and it records no `reopen_when`. Anything else is **open**.

## When it runs

- When `plan-campaign` returns `stop` or `change_direction`: the stop reason "no open route" and
  the five-round plateau both rest on the refutations, so they are checked before they are
  reported.
- After three rounds without a gain, together with the forced re-diagnosis.
- At any point where two or more closures are open, because that is a pattern rather than a
  single mistake.

## What you receive

One object matching `contract/input.schema.json`: the operator's `entries`, optionally the
archived `verdicts` by experiment id for entries that predate closures, the `stalled_rounds` the
campaign has counted, the `proposed_action` from `plan-campaign`, and the thresholds.

## Steps

1. Run `python3 scripts/revisit_closures.py --input <file>`. Done when it prints one plan object
   and every `refuted`/`not_implemented` entry appears in `closures`.
2. Read `decision`. Done when you can say which of the three triggers fired: a proposed stop, a
   flat streak, or the number of open closures.
3. Read `reopens` in order and take the first one whose `first_check` is cheap: that is the next
   card. Done when the card's mechanism is the reopen's `retry`, not a new direction.
4. For a `premise` reopen with `sweep`, the card must sweep `min_points` points of the parameter
   and predict against the best point, not against the first. Done when the card's
   `points_tried` will be at least `min_points`.
5. Answer every question in the reopen's `questions` in the card's notes. Done when no question
   is left to the next reader.
6. When `decision` is `hold` or `exhausted`, hand the counts to `report-campaign` and say plainly
   that the closures stayed unresolved. Done when the report does not present them as refuted.

## Output

`contract/output.schema.json`. The fields that change what happens next:

- `triggered`, `triggers` - whether a revisit is due, and which condition made it due.
- `decision` - `reopen`, `hold`, `exhausted` or `not_triggered`.
- `reopens` - at most three, ordered by evidence class then by the predicted speedup the closed
  card committed to. Each carries the retry kind, the reason it deserves an experiment, the
  questions the card has to answer, and the condition that would make a second refutation valid.
- `capped` - directions that have used `MAX_REOPENS`; they are no longer reopened and are
  reported as unresolved.
- `counts` - closures per evidence class, and how many are admissible at all.

## Judging principles

- A closure whose direction never reached machine code proves nothing, and no amount of campaign
  budget makes it evidence. Reopen it, or drop the direction without a word about its merit.
- A `measurement` closure is the most valuable outcome in the system wearing the wrong label.
  Bank the patch and record that the diagnosis was wrong; do not call the direction dead.
- A one-point refutation of a parameter is a sample, not a sweep. Widen before you write off.
- The count is the point: one mislabelled refutation is a mistake, five are a campaign that will
  report "exhausted" while most of its search space is untouched.
- Reopening is bounded. After `MAX_REOPENS` the closure stands, and the report says the direction
  was reopened and refuted that many times, which is the number a human needs.
