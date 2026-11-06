# Orchestration: how a campaign actually runs

`docs/workflows.md` gives the seven workflows as prose. This page is the bridge between that
prose and `orchestrator/sm.py`, and it is the page to read when you are driving a campaign by
hand and the state machine says something you did not expect.

## The loop, as a state machine

```
                    ┌──────────────────────────────────────────────┐
                    │                                              │
   diagnose ──► retrieve ──► hypothesize ──► implement ──► evaluate │
       ▲                                        ▲              │   │
       │                                        │              ▼   │
       │                              correctness fail      inspect  │
       │                                        │              │   │
       │                                        │              ▼   │
       │                                        │           verify  │
       │                                        └─ not_implemented ─┤
       │                                                           │
       │                                                           ▼
       │                                                   record ──► promote
       │                                                       │        │
       │                                                       ▼        ▼
       │                                                      gap ◄─────┘
       │                                                       │
       │                                                       ▼
       │                                                    revisit ──► hypothesize (reopen)
       │                                                       │
       └──────────────── plan ◄──────────────────────────────────┘
                                   │
                                   ▼
                                 report ──► done
```

Every arrow is one function call: `sm.advance(campaign, outcome)` consumes a step's outcome and
returns `sm.next_step(campaign)`, which names the state, the skill and the tier. Nothing else
decides what runs next.

## Driving it by hand

```sh
tools/rsi-cli campaign --op vec_add --budget 20            # what runs first?
tools/rsi-cli campaign --op vec_add --outcome outcome.json # consume the step, get the next one
tools/rsi-cli campaign --op vec_add --step                  # report the next step, change nothing
```

State persists in `orchestrator/state/<op>.json`. `--restart` discards it.

`outcome.json` carries only the keys the step actually produced;
`templates/campaign/outcome.json` shows the shape. A key that is absent is treated as absent, not
as a default: a step that produced no diff did not implement anything, and the machine counts a
retry rather than moving on.

## The two retry loops

Both are bounded, and both ending is a blocked campaign rather than a false result.

| Loop | Bound | When it runs out |
| --- | --- | --- |
| Correctness | 2 retries after the first attempt | Blocked. No timing, no evidence: a fast wrong kernel is not a candidate. |
| Not implemented | 2 retries after the first attempt | Blocked. Recorded as an implementation failure that counts toward no rule. |
| Reopen | 2 reopens per direction | Reported as unresolved: the direction was reopened and refuted that many times, which is what the report says. |

The second bound is the important one. Without it, an agent that writes plausible code which does
nothing eventually produces a "confirmed" experiment whose evidence is empty, and every rule
distilled from that evidence is superstition.

There is a third bound, on what a campaign may *conclude*, and it is the one that stops the same
problem from re-entering through the exit. Every refuted or not-implemented round leaves a closure
record saying whether the mechanism ever reached machine code, whether its counters were measured,
and how many points of its parameter were tried. The `revisit` state runs before `plan` and before
a stop is reported, and a direction whose closure is not admissible and swept is reopened rather
than retired. `docs/closures.md` has the classes and the rules.

## The stop conditions

`sm.stop_conditions` evaluates every one at once, so a plan decision is auditable rather than a
judgement call:

- **Attainment reached** the target (85 % by default).
- **The budget is spent**: the experiment count reached the cap.
- **Five rounds without a significant gain** and no remaining hypothesis predicts one above the
  noise floor, with every closure admissible and swept.
- **Three rounds without a gain** triggers a re-diagnosis first, because the bottleneck may have
  moved. This is not a stop; it is a forced restatement of the problem.

When the campaign stops far from the bound, the report says "needs an algorithm-level rewrite".
That is not a failure of the loop: it is the loop reporting that the remaining distance is not
reachable by the tactics in the playbooks, which is exactly what a human needs to know.

## Where the timing result is held

Between `evaluate` and `verify`, the orchestrator holds the timing result back. The verifier
receives the card, the diff, the binary summaries and the counters — and no timing. That is not a
convention the verifier is asked to respect: it is what its input contract accepts, so a verifier
that tried to look would fail its own contract.

The result is unsealed at `record`, when the verdict already exists.

## Model routing

`orchestrator/routing.py` maps a skill to a tier. Nothing in a skill names a model, so swapping
one is a single change in `orchestrator/models.yaml` — and, because it changes cost directly, a
change a human approves rather than one the outer loop applies.

The tier to watch is the implementer's. Its signal is the not-implemented rate: a high rate means
changes are not landing, which is an implementer capability problem, not a hypothesis problem. The
routing policy says to upgrade the implementer before the planner, because the published ablations
found the executor's capability mattered more than the planner's model.

## Replay

`orchestrator/replay.py` implements the gate. The protocol is fixed in code because replay is the
most expensive part of the system and the easiest to accidentally weaken:

- one change at a time;
- same baselines, same operators, same budget for old and new;
- at least three repeats, because a scaffold contains a model and model output is stochastic;
- merge only if the primary metric improves on dev with an interval excluding zero, holdout does
  not regress, and no secondary metric gets clearly worse;
- both outcomes are written to `CHANGELOG-scaffold.md`, so a failed change is not retried next
  month.

Two-stage screening keeps it affordable: two or three cheap dev operators first, the full set only
if that passes. Changes that touch only diagnosis, retrieval or hypothesis ranking are screened
offline against ledger history, with no device involved at all.
