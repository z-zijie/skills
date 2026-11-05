# System metrics

One primary metric tells you whether the system is improving itself: **on held-out operators,
does the speedup gained per experiment rise with each scaffold version**. Everything else
explains why it does or does not.

| Metric | Definition | Wanted | What it reflects |
| --- | --- | --- | --- |
| Speedup per experiment (primary) | log of a campaign's total speedup divided by the number of experiments, on holdout, per scaffold version | up | overall efficiency of the system |
| Experiments to target | experiments needed to reach the target attainment | down | the same thing from another angle |
| Final attainment | roofline attainment when the campaign ends | up | how close to the limit an operator can be pushed |
| Hypothesis confirmation rate | confirmed over confirmed plus refuted | up, but not near 100 % | hypothesis quality; too high means only safe hypotheses are chosen |
| Not-implemented rate | not-implemented verdicts over implementation attempts | down | implementer capability and clarity of the cards |
| Prediction calibration error | median prediction error | down | quality of the hardware model |
| Human intervention rate | human corrections per 100 experiments | down | how much human judgement the system has absorbed |
| Verifier agreement | share of audited cases where the verifier matches the human | stays above 90 % | whether the verifier can be trusted |
| Dev versus holdout gap | difference in the primary metric between the two splits | stable | overfitting |
| Cost per unit of speedup | tokens and device-seconds per 1 % of speedup | down | economics |
| Knowledge-base health | established rules, mean evidence per rule, share deprecated | steady growth | whether knowledge is settling or piling up |

## Why the primary metric is efficiency, not final speedup

Ablations in the published work this design borrows from showed that optimization memory let
the system reach in 13 iterations the speedup that otherwise took 16 — a 16 to 17 % cost saving
— but that with enough sampling it did not clearly improve the best result. The return on
accumulated experience shows up in **efficiency** first, so efficiency is where self-improvement
can actually be measured.

That has a consequence for what you build first: a scaffold change that reaches the same
attainment in fewer experiments is a success even though no kernel got faster, and it is a
success that a final-speed metric would score as nothing.

## Where each metric is computed

- Speedup per experiment, experiments to target, final attainment: `tools/rsi-cli report` over
  the ledger.
- Confirmation rate, not-implemented rate, prediction error: `ledger.evidence_counts` and the
  ledger entries themselves.
- Verifier agreement and human intervention rate: the audit sample in W6, recorded as labels.
- Dev versus holdout gap: the replay reports in `CHANGELOG-scaffold.md`.
- Knowledge-base health: `tools/rsi-cli kb-audit`.

## Weekly dashboard

One page is enough:

1. The primary metric's trend, with the x-axis being the sequence of scaffold merges.
2. Current attainment per operator.
3. The top three failure types from `analyze-failures`.
4. The week's cost.

If the primary metric is flat while a merge looked good in its replay, the replay's dev subset
was too easy. That is worth finding before the next merge, and it is why the dev/holdout gap is
tracked as its own metric.
