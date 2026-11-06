# Disagreement log

When the agent's judgment and a human's judgment diverge, the divergence is a scaffold defect:
either a check is missing, a threshold is wrong, or a playbook ranks the wrong tactic first.
Recording it turns a one-off correction into something the outer loop can act on.

The log is also the raw material for the human-intervention rate, which is a system metric: if it
does not fall, the system is not absorbing human judgment.

## Format

| Date | Operator | Experiment | The agent said | The human said | Kind | Classified as | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |

`Kind` is one of: `verdict` (the mechanism verifier was wrong), `diagnosis` (the signature or the
primary bottleneck was wrong), `hypothesis` (a card that should have been proposed was not, or one
that was proposed was not worth testing), `prediction` (the predicted speedup was badly off), or
`implementation` (the change did not land and the agent thought it had).

`Classified as` names the proposal the divergence produced: a new verifier check, an anti-pattern,
a playbook edit, a threshold change, or `evaluator hole` when the divergence is one only a human
can close.

`Status` is `open`, `proposed (P-<n>)`, `replayed`, or `closed`.

## Entries

| Date | Operator | Experiment | The agent said | The human said | Kind | Classified as | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| — | — | — | No divergence recorded yet: the ledger is empty, so there is nothing to diverge on. | — | — | — | — |

The empty state is the honest one. A populated log with no entries would mean nobody was
checking, not that the system was always right.
