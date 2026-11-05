# report-campaign strategy

## The campaign report

Four questions, in this order, and the answer to each must be a figure from the file.

1. **Where did it end?** Attainment against the target, and the gain over the baseline in
   points. A campaign that reached 71% from 48% is a success even if it missed 85%, and saying so
   requires both numbers.
2. **What worked?** The confirmed changes with their measured speedups. This is the list a human
   reads first and the list the next campaign reuses.
3. **What is still unknown?** Unexplained speedups and wrong diagnoses. These are the two entry
   points for the next campaign, and they are invisible in a speedup-sorted summary.
4. **What is left?** The open routes, priced by the hardware model. A campaign report without
   this reads as a dead end even when it stopped on budget.

## When the campaign stops far from the bound

The build plan's instruction is direct: the report must say "needs an algorithm-level rewrite".
Say it in the verdict, and say what kind:

- **Fusion** — the operator is one pass of a chain, and the gap is the round trip between passes.
- **Algorithm change** — the current decomposition cannot reach the bound whatever the schedule.
- **Data layout** — the bound assumes a contiguity the operator's interface does not offer.
- **Precision** — the bound is computed at a dtype the output contract does not require.

Then hand the decision to a human. Opening a new campaign on a rewrite is a different budget and
a different risk, and the report's job is to make that decision easy, not to make it.

## Reading a flat primary metric

Check the secondary metrics in this order, because each one has a different fix:

| Secondary symptom | Reading | Where the fix belongs |
| --- | --- | --- |
| Not-implemented rate above 0.3 | Cards are unclear or the implementer is too weak | Card wording first, model routing second, and routing is a human decision |
| Median prediction error above the hole | The hardware model has a gap | A microbenchmark proposal, not a playbook edit |
| Confirmation rate near 1.0 | Only safe hypotheses are being proposed | The playbook's priority order has stopped exploring |
| Implausible results above zero | Something is being credited that should not be | A human review before any of this is believed |
| Deprecated share rising | Knowledge is churning as fast as it is written | Narrow distillation scopes |

A flat primary metric with healthy secondaries usually means the benchmark is too easy: the
operators are already near their bound and there is little left to gain. That is a benchmark
problem, and the report should say so instead of proposing more scaffold changes.

## The system report

The primary metric is log speedup per experiment on holdout, by scaffold version. Everything
else explains it.

- Group by `scaffold_commit`, never by date. A week can contain two scaffold versions and a
  version can span weeks, and only the commit groups experiments that ran under one scaffold.
- Require the same operators and the same budget before comparing two versions. A version that
  ran the three easiest operators will look better for reasons that have nothing to do with the
  scaffold.
- The dev-versus-holdout gap is the overfitting signal. The report does not read the holdout
  itself; it reports the gap the replay judge publishes, and a widening gap pauses merging.
- Knowledge health is a slow signal. Mean evidence per rule rising over months means the store is
  settling; a stable number means new cards are arriving as fast as old ones are confirmed.

## Cost

The weekly dashboard has one page: the primary-metric trend, attainment per operator, the top
three failure types, and the week's cost.

- Report tokens and device seconds per unit of speedup, not the absolute spend. A more expensive
  week that bought more speedup is not a regression.
- Keep the failure types to three. A longer list stops being read, and `analyze-failures` holds
  the full set for anyone who wants it.
- Do not include a recommendation section. The outer loop's inputs are the failure clusters and
  the replay reports, and a report that also recommends changes becomes a second, unaudited
  source of scaffold edits.
