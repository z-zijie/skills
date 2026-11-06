# Scaffold changelog

Every scaffold change and its replay result, appended by the replay judge. A merge without a
line here did not happen; a rollback without a line here will be tried again.

Each line is one JSON object: the proposal, the decision, the primary metric and why.

## Initial scaffold

The first version of this framework was not replayed: there was nothing to replay it against.
That is recorded here rather than left implicit, because the changelog's only value is that it is
complete.

- `2026-11-05` — initial scaffold: evaluator, tooling, schemas, 22 skills, 8 playbooks,
  knowledge-base stores, orchestrator state machine, replay judge. No replay evidence; every
  playbook records `Last replay: never`, and the seed rule cards are marked as examples.

## Closures: a refutation now has to be a refutation

Recorded before the replay judge existed for it; the proposal queue is where it would otherwise
have gone. The evidence behind it is one real campaign, written up in `docs/campaign-audit.md`.

- `2026-11-06` — closures on the ledger, a `revisit` state between `gap` and `plan`, and
  `revisit-closures` as the twenty-third skill. A refutation whose mechanism never landed, whose
  counters moved while the score did not, or that tried one point of a parameter no longer retires
  a direction: it is an open closure that reopens before a stop is reported, and it is reported as
  unresolved rather than exhausted. No replay evidence yet: the mechanism is checked by the test
  suite and by the campaign audit, and its effect on the primary metric is what the next campaign
  on this machinery has to show.
