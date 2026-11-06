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
