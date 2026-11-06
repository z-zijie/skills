# audit-knowledge strategy

## The findings, and what each one is worth

| Finding | Mechanical test | Gate | Why that gate |
| --- | --- | --- | --- |
| `thin_evidence` | status claims more than the life cycle allows | `mechanical` | The life cycle already computes the status from counts |
| `stale` | `last_verified` older than `stale_after_days`, or a measured parameter moved | `mechanical` | A date and a calibration hash are facts |
| `conflict` | one scope, one metric, opposite directions | `mechanical` | Only one can be true; evidence counts break the tie |
| `orphan_evidence` | an evidence id not in the ledger window | `mechanical` | The claim cannot be re-derived from what exists |
| `missed_counterexample` | refutations with no `counter_example_note` | `mechanical` | The build plan requires the note on every refutation |
| `unbound_metric` | a metric not in `tooling/rsi/metrics.py` | `mechanical` | An unregistered name will not survive a device change |
| `legacy_metric` | a registered but non-canonical spelling | `mechanical` | Accepted on read, never written |
| `no_mechanism` | a rule or pattern with no `mechanism` | `mechanical` | The hypothesizer cannot predict counters without it |
| `narrow_scope` | `mean_prediction_error` above the hole | `offline_ledger_replay` | A narrower scope must still rank historical confirmations higher |

The split matters: a `mechanical` finding can be merged as soon as it is written, and everything
else has to survive a replay or a human. Getting this wrong turns a cheap audit into an
expensive one, so when a finding could be either, choose the stricter gate.

## Conflicts

This is the only finding that needs judgement, and the judgement is narrow.

- Same scope, same metric, opposite directions: one of the two is wrong. Keep the one with more
  confirmations across more operators, and put the other into `deprecated` with a note naming
  the winner. The script names both; check its arithmetic rather than re-ranking by feel.
- Same scope, same metric, same direction, but two different transformations: complementary,
  not conflicting. A playbook wants both and orders them by predicted effect.
- Same claim twice, worded differently: merge the evidence into the better-evidenced card and
  retire the other. Two cards for one claim halve both confirmation counts and neither ever
  reaches established.
- Different scopes: not a conflict, however similar the statements. The scope is the claim.

When neither card can be shown wrong — for example, both were confirmed on the same operator
with different shapes — do not pick a survivor. Propose `narrow_scope` on both and let the next
replay separate them.

## Staleness

- Age alone is weak evidence. A rule that has not been touched because nothing re-tested it is
  not wrong, so staleness demotes to `under_review`, never to `deprecated`.
- A moved measured parameter is strong evidence. Every rule whose effect depends on that
  parameter is wrong until re-verified, and `under_review` is the honest label.
- Re-verification is cheap when the ledger holds a recent experiment on the rule's scope: the
  window itself re-verifies the card. Prefer citing one than scheduling new device time.
- A conjecture past its review date is not stale knowledge, it is an open question. Close it
  with `human_only`, because only its author can decide whether it was answered.

## Evidence hygiene

- Evidence ids are the card's whole claim to truth. An id that is not in the ledger is a broken
  citation, and a rule built on one cannot be defended in a review.
- Check that ids match the operator the card names. A confirmation from a different operator
  under a different signature does not confirm this scope, even if the id exists.
- `not_implemented` and `implausible` experiments are never evidence. If one appears in a card's
  confirmed list, that is a `thin_evidence` finding at high severity, whatever the count says.
- A `counter_example_note` is not decoration. It is the only record of why a refutation did not
  kill the rule, and a card without one will be re-proposed as an anti-pattern by the next
  distillation run.

## Store health, over time

Watch three numbers across audits rather than within one:

- **Established rules** should grow and then plateau. Growth that never plateaus means scopes
  are too narrow to accumulate.
- **Mean evidence per rule** should rise. A flat number with a rising card count means new cards
  are being added faster than old ones are confirmed.
- **Deprecated share** should stay small. A large share means the store is churning, and the
  distillation scope is probably too wide.

A store with many candidates and no established rules is not unhealthy, it is young. Say so, and
resist the urge to promote cards to make the dashboard look better.
