# RSI contracts reference (for authors of skills)

This file is the single page a skill author needs. It is a cache: everything here is
authoritative somewhere else, and the pointers say where.

## Where things live

```
RSI/
├── evaluator/          frozen Layer 0 — call its CLI, never read or edit its source
├── tooling/rsi/        shared code: contracts, units, statistics, signatures, ledger
├── schemas/            JSON Schemas; frozen contracts between skills
├── skills/<name>/      one skill: SKILL.md + contract/ + strategy/ + scripts/ + tests/
├── playbooks/          tactics ordered by bottleneck signature
├── kb/                 documented, measured, patterns, rules, antipatterns, conjectures, proposals
├── ledger/             experiments.jsonl (append-only) + artifacts/<exp_id>/
├── operators/<op>/     spec.yaml, reference/, baseline/, best/, workspace/
├── benchmark/          dev.txt, holdout.txt (holdout is invisible to agents)
├── orchestrator/       state machine, model routing, replay judge
├── operators/          see templates/operator/
└── docs/               vocabulary, workflows, roadmap, permissions
```

## Canonical vocabulary

Read `docs/vocabulary.md`. Two rules matter in every sentence a skill writes:

- Name a **role in the machine**, never a vendor: `device_mem`, `scratchpad`, `work_group`,
  `work_lane`, `wave`. Never CUDA, never `sm`, never `dram`, never `ncu`, never `npu-smi`.
- Name a **canonical metric** from `tooling/rsi/metrics.py`; never a raw profiler expression.

## The data structures a skill reads and writes

Every one has a schema in `schemas/`. Validation is mandatory before a step's output is used
by the next step; a failed validation retries in place.

| Structure | Schema | Produced by | Consumed by |
| --- | --- | --- | --- |
| Bottleneck signature | `signature.schema.json` | `diagnose-bottleneck` | `retrieve-knowledge`, `propose-hypothesis`, `plan-campaign` |
| Hypothesis card | `hypothesis.schema.json` | `propose-hypothesis` | `implement-change`, `verify-mechanism` |
| Evaluator result | `result.schema.json` | `run-evaluator` (signed) | `record-experiment` |
| Normalized profile | `profile.schema.json` | `collect-profile` | `diagnose-bottleneck`, `verify-mechanism` |
| Mechanism verdict | `verdict.schema.json` | `verify-mechanism` | `record-experiment`, `runtime` |
| Ledger entry | `ledger-entry.schema.json` | `record-experiment` | `distill-rules`, `plan-campaign`, `report-campaign` |
| Rule card | `rule.schema.json` | `distill-rules` (as a proposal) | `retrieve-knowledge`, playbooks |
| Proposal | `proposal.schema.json` | outer loop, `capture-correction` | the gates in `docs/workflows.md` |
| Replay report | `replay-report.schema.json` | `replay-benchmark` | merge or rollback |

## Layout of one skill

```
skills/<name>/
├── SKILL.md        frontmatter (name, description) plus the body: when to use, steps, output
├── contract/       JSON Schemas for this skill's input and output — frozen, only a human edits
├── strategy/       judgement text, checklists, examples — the outer loop may edit these
├── scripts/        deterministic logic: parsing, comparison, validation
└── tests/          regression cases with labelled expected outcomes
```

`contract/` and `strategy/` are split on purpose: the outer loop may change strategy freely
but cannot change interfaces, so a bad change is rolled back alone without breaking other
skills.

## SKILL.md frontmatter

```
---
name: verify-mechanism
description: One sentence naming what the skill does and the branches that should trigger it.
---
```

The description is the skill's context pointer: it is loaded on every turn, and its wording —
not the skill's contents — decides when the agent reaches for it. State the trigger branches:
"use after X passes and before Y is unsealed". Keep the body under about 200 lines; push long
material into `strategy/` and point at it.

## Writing rules that come from the repository

The repository has a `writing-for-agents` skill and it applies here in full. Its rules that
matter most for these skills:

- **Steps and completion criteria.** Every step ends on a condition that says when it is done,
  and the condition is checkable and exhaustive ("every evidence item on the card is located
  in the diff or the disassembly", not "the review is done").
- **Prune no-ops.** If the model already does it by default, the sentence pays load to say
  nothing; delete the whole sentence rather than trim its words.
- **Leading words over repeated explanation.** Use one compact word consistently and let it
  accumulate meaning.
- **Prompt the positive.** State the target behaviour; a prohibition drags the forbidden act
  into context. A ban earns its place only as a hard guardrail, and then it is paired with the
  positive target.
- **Single source of truth.** Do not restate a schema field by field in prose; name the field
  and point at the schema.

## Hard rules no skill may break

- A skill never calls another skill. `orchestrator/` decides what runs next.
- A skill reads only the files its contract names. The verifier in particular receives no
  timing result and no implementer notes.
- The evaluator is invoked only through its CLI: `rsi evaluate …`.
- Nothing in the inner loop writes to `kb/`, `skills/` or `playbooks/`; those go through
  `proposals/` and a gate.
- Knowledge injection is on demand: `retrieve-knowledge` returns at most 5 rules, 3
  anti-patterns, 3 patterns and the 3 most similar past experiments.
