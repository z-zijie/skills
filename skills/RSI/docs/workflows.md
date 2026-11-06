# Workflows

Seven workflows cover everything the system does. W2 and W3 are the inner loop, W5 is the outer
loop, and the other four feed the system: operators, hardware facts, human corrections and
human conjectures.

| ID | Workflow | Trigger | Frequency |
| --- | --- | --- | --- |
| W1 | Operator onboarding | A new operator enters the system | Once per operator |
| W2 | Single experiment | Each round of a campaign | Most frequent |
| W3 | Optimization campaign | Someone says "optimize operator X" | Once or more per operator |
| W4 | Hardware calibration | New device, runtime upgrade, monthly routine, or a prediction-error trigger | Rare |
| W5 | Outer loop | Every 30 to 50 experiments, or after each campaign | Moderate |
| W6 | Correction capture | A human rejects or fixes an agent's conclusion | Any time |
| W7 | Conjecture test | A human has an unverified idea | Any time |

`orchestrator/sm.py` implements W2 and W3 as a state machine. The tables below are the same
workflow in prose; where they disagree, the code is right and the table is a bug.

## W1 Operator onboarding

1. Write `spec.yaml`: shape distribution, dtype, tolerance, and the byte and flop formulas.
2. Add the reference implementation and the baseline kernel.
3. The evaluator runs an A/A test to get this operator's noise floor.
4. Run `evaluate` and `profile` on the baseline and record it in the ledger as the baseline
   experiment.
5. Compute the roofline bound and the baseline's attainment.
6. A human decides whether the operator goes into dev or holdout.

**Done when** two independent evaluations of the baseline differ by less than the noise floor.

Start from `templates/operator/`. It carries the layout, a spec skeleton and a reference
implementation with the contract the harness expects.

## W2 Single experiment

```
diagnose → retrieve → hypothesize → implement → evaluate → inspect → verify → record → promote
                ↑                        ↑          │            │
                └──── correctness ───────┘          └─ not_implemented ┘
```

1. **Diagnose.** `collect-profile` and `diagnose-bottleneck` produce the bottleneck signature
   and the attainment.
2. **Retrieve.** `retrieve-knowledge` fetches matching rules, anti-patterns and similar
   experiments, capped at 5 rules, 3 anti-patterns, 3 patterns and 3 experiments.
3. **Hypothesize.** `propose-hypothesis` writes a hypothesis card: mechanism, named counters
   with predicted directions, predicted speedup.
4. **Implement.** `implement-change` implements that one card and nothing else.
5. **Evaluate.** `run-evaluator` checks correctness and times the kernel. A correctness
   failure goes back to step 4, at most twice. **The timing result is sealed and kept from the
   verifier.**
6. **Verify the mechanism.** `inspect-binary` and a fresh profile go to `verify-mechanism`. A
   `not_implemented` verdict goes back to step 4, at most twice. If it still fails, the
   experiment is recorded as an implementation failure that counts toward no rule.
7. **Record.** Unseal the timing. `record-experiment` writes it to the ledger with the verdict
   and the prediction error.
8. **Promote.** If the mechanism is confirmed and the speedup clears the noise threshold, the
   evaluator promotes the candidate to `best/`.
9. **Fill the gap.** If the prediction error exceeds its threshold, queue a task for
   `design-microbench`.

Only confirmed or refuted experiments reach the ledger as evidence. A correctness failure and a
`not_implemented` verdict are retries, not evidence.

## W3 Optimization campaign

1. Each round runs W2 once, starting from `best/`.
2. After each round `plan-campaign` decides: continue in this direction, change direction,
   combine two confirmed and independent changes, or stop.
3. Start with greedy search plus a small beam of two or three candidate branches.
4. After three rounds without improvement, re-profile and re-diagnose first, because the
   bottleneck may have moved.
5. Stop when any one holds:
   - attainment reaches the target (85 % suggested);
   - five rounds bring no significant gain and no new hypothesis predicts one above the noise
     floor;
   - the budget is spent.
6. `report-campaign` writes the report: final attainment, confirmed changes, unexplained
   observations, remaining opportunities.

If the campaign stops far from the bound, the report says **"needs an algorithm-level
rewrite"**. A human then decides whether to open a new campaign to fuse neighbouring operators,
change the algorithm or change the data layout.

## W4 Hardware calibration

1. `calibrate-hardware` runs the full microbenchmark suite.
2. Results go to `kb/hardware/measured/` with the environment fingerprint and date.
3. Compare with the previous run. Flag parameters that moved beyond a threshold, and mark the
   rules that depend on them **under review**.
4. Re-run every operator's `best/` to confirm nothing regressed.

A driver, runtime or compiler change is an environment change: re-measure before trusting any
rule bound to the old fingerprint.

## W5 Outer loop

1. `analyze-failures` clusters failure patterns.
2. `distill-rules` creates or updates rule cards.
3. `audit-knowledge` cleans up conflicting and stale entries.
4. `edit-scaffold` proposes **one** change that targets the top failure pattern, on a branch.
5. `replay-benchmark` replays it, dev first and then holdout.
6. Merge if it passes, roll back if not. Both outcomes go into `CHANGELOG-scaffold.md`.
7. Early on, a human approves every merge.

The gates are in the next section. Replay is the most expensive part of the system and is
limited from day one: two-stage screening on two or three cheap dev operators, offline replay
against ledger history for changes that touch only diagnosis, retrieval or ranking, a fixed
weekly budget, and at least three repeats per configuration.

## W6 Correction capture

1. A human points out the problem: which experiment, what the agent concluded, and what the
   right conclusion is.
2. `capture-correction` classifies it: a verifier check, an anti-pattern, a playbook edit, or an
   evaluator loophole. **Only a human can fix the last kind.**
3. A new check is first replayed over the ledger history. It must catch the past cases of the
   same kind and must not flag experiments that were rightly confirmed.
4. Merge.

The goal: each kind of mistake is corrected once.

## W7 Conjecture test

1. `register-conjecture` records the conjecture, where it applies, and what should be observed
   if it is true.
2. Generate one to three minimal experiments, preferring microbenchmarks.
3. One of three outcomes: promoted to a candidate rule, marked refuted, or left open for lack
   of evidence.

An unverified conjecture may inspire hypotheses. It cannot be used as grounds for verification
and does not count as rule evidence.

## What a human gates

| Object | Can the outer loop change it? | Gate |
| --- | --- | --- |
| Evaluator, microbenchmark code, operator specs, tolerances, the holdout list | No | Human only |
| Skill contracts (input and output schemas) | No | Human only |
| Verifier checks | Tighten only | Replay over ledger history; loosening needs human approval |
| Playbooks, hypothesis priority order | Yes | Dev plus holdout replay |
| Skill strategy text, prompts, examples | Yes | Dev plus holdout replay |
| Rule card status | Yes | Decided mechanically by the evidence rules in `kb/README.md` |
| Signature thresholds | Yes | Replay, backed by calibration data |
| New tools and scripts outside the evaluator | Yes | Unit tests plus replay |
| Model routing | Propose only | Human approves: it changes cost directly |
