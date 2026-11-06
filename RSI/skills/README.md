# Skills

Twenty-three skills, grouped as the build plan groups them. Each is a directory with `SKILL.md`,
`contract/` (frozen), `strategy/` (the outer loop may edit), `scripts/` where the work is
deterministic, and `tests/` with labelled regression cases.

A skill never calls another skill. `orchestrator/` reads a skill's output contract and decides
what runs next, so the sequence is data and every step can be costed, replayed and replaced.

## Entry point

| Skill | Does | Phase |
| --- | --- | --- |
| `optimize-operator` | Starts, resumes or inspects one operator's campaign and hands the orchestrator a budgeted stage plan | P3 |

## Foundation: the evaluator and the ledger

| Skill | Does | Phase |
| --- | --- | --- |
| `run-evaluator` | Measures one candidate through the frozen evaluator; returns the signed result | P0 |
| `collect-profile` | Runs profiler collection and returns the profile reduced to canonical metrics | P0 |
| `record-experiment` | Validates, archives and appends exactly one ledger entry | P0 |
| `inspect-binary` | Summarizes a compiled artefact: machine-level change, registers, scratchpad, launch configuration | P2 |

## Inner loop: optimizing the kernel

| Skill | Does | Phase |
| --- | --- | --- |
| `implement-change` | Edits the kernel for one hypothesis card and makes it compile | P0 |
| `diagnose-bottleneck` | Turns a profile into a signature and roofline attainment, and names the bottleneck | P1 |
| `verify-mechanism` | Returns confirmed, refuted or not implemented, from evidence only | P2 |
| `retrieve-knowledge` | Fetches the few rules, anti-patterns, patterns and past experiments that match | P3 |
| `propose-hypothesis` | Proposes one to three ranked, falsifiable hypothesis cards | P3 |
| `plan-campaign` | Decides one campaign move: continue, change direction, combine or stop | P3 |
| `revisit-closures` | Reopens a written-off direction that was never really tried, before a stop is reported | P3 |

## Hardware knowledge: a feel for the device

| Skill | Does | Phase |
| --- | --- | --- |
| `calibrate-hardware` | Runs the microbenchmark suite and updates measured parameters and thresholds | P1 |
| `ingest-docs` | Turns specifications and profiler definitions into sourced documented entries | P1 |
| `design-microbench` | Proposes a microbenchmark that isolates one mechanism; needs human approval | P4 |

## Outer loop: optimizing the optimizer

| Skill | Does | Phase |
| --- | --- | --- |
| `analyze-failures` | Clusters a ledger window into failure patterns with their experiment ids | P4 |
| `distill-rules` | Proposes rule cards with scope and evidence counts; the life cycle sets status | P4 |
| `audit-knowledge` | Finds conflicting, stale or unsupported entries and proposes demotion or merge | P4 |
| `edit-scaffold` | Proposes one change to one file for one cluster, on a branch | P4 |
| `replay-benchmark` | Replays old and new scaffolds on dev then holdout; returns merge or rollback | P4 |

## Human interface: judgment as system assets

| Skill | Does | Phase |
| --- | --- | --- |
| `capture-correction` | Turns one correction into a check, an anti-pattern or a playbook edit, replayed first | P2 |
| `register-conjecture` | Quarantines a conjecture as unverified and plans the smallest tests for it | P3 |
| `report-campaign` | Produces the campaign report and the periodic system report from the ledger | P3 |

## Gates

Which gate a proposal needs is decided by what it changes, not by the skill that proposed it:
evaluator, microbenchmarks, tolerances and the holdout are human-only; verifier checks tighten
only and are replayed over ledger history; playbooks, skill strategy and thresholds need dev plus
holdout replay; rule status is mechanical. `docs/workflows.md` holds the full table.

## Checks

```
python3 -c "
import sys; sys.path.insert(0, 'tooling')
from rsi import skills
for path in skills.discover():
    print(path.name, skills.validate_skill(path.name))
"
```

Each skill's own regression cases replay without a device:

```
python3 -c "
import sys; sys.path.insert(0, 'tooling')
from rsi import skills
print(skills.replay_tests('<skill-name>'))
"
```
