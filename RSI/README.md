# RSI — self-improving kernel optimization

A scaffold that lets agents push an already-correct kernel close to its hardware's theoretical
limit without step-by-step human guidance, and that makes every finished operator the next one
faster to optimize.

The thing that improves is the scaffold around the model — the knowledge base, playbooks,
skills, prompts and tools. Model weights never change. So the system has one final test: on
operators it has never seen, does the speedup gained per experiment keep rising?

**Hardware-agnostic by construction.** Everything is written against a canonical vocabulary of
roles in a machine — `device_mem`, `scratchpad`, `work_group`, `work_lane` — and canonical
metrics that any profiler can be mapped onto. Three parts are device-specific, and all three are
adapters: the profiler adapter, the hardware knowledge base and the device-bound part of the
pattern library. See [`docs/moving-to-a-new-device.md`](docs/moving-to-a-new-device.md).

## Where to start

| If you want to… | Read |
| --- | --- |
| understand the shape of the system | this file, then [`docs/vocabulary.md`](docs/vocabulary.md) |
| set up a new machine | [`docs/moving-to-a-new-device.md`](docs/moving-to-a-new-device.md) |
| run an optimization | [`docs/workflows.md`](docs/workflows.md) W3, then [`skills/optimize-operator/`](skills/optimize-operator/SKILL.md) |
| know what an agent may write | [`docs/permissions.md`](docs/permissions.md) |
| write or change a skill | [`docs/AUTHORING.md`](docs/AUTHORING.md) and [`docs/contracts.md`](docs/contracts.md) |
| know what is guaranteed and what is not | [`docs/guarantees.md`](docs/guarantees.md) |
| know what is built and what is not | [`docs/roadmap.md`](docs/roadmap.md) |

## The seven principles

Every skill, workflow and store below follows from these. They are load-bearing; a change that
breaks one is not a local change.

1. **The evaluator is frozen.** Agents can read its results but cannot change its code.
2. **Hypotheses must be falsifiable.** Each states its mechanism, which counters should move,
   and the predicted speedup.
3. **Verify the mechanism before the speedup.** Confirm the change really landed and behaved as
   predicted; only then ask how much faster it is.
4. **Only confirmed or refuted experiments count as experience.** An attempt that was "not
   actually implemented" triggers a retry and never enters the knowledge base.
5. **Knowledge carries scope and evidence counts.** Anything without experimental support is a
   conjecture, not a rule.
6. **Every scaffold change is replayed.** It merges only if held-out operators improve,
   otherwise it is rolled back.
7. **Strong models judge, cheap models execute.** Proposing hypotheses, verifying mechanisms and
   distilling rules use a strong model; writing code and running experiments use a cheap one.

## Layout

```
RSI/
├── evaluator/          Layer 0: frozen. Call the CLI, never read or edit its source
│   ├── oracle/         correctness: reference, sentinels, canaries, random shapes
│   ├── harness/        timing: warm-up, interleaving, bootstrap, the win rule
│   ├── profiler/       adapters mapping a profiler onto canonical metrics
│   ├── backends/       execution adapters: reference, portable array runtime
│   ├── microbench/     the 14 calibration microbenchmarks
│   └── cli.py          the only entry point: evaluate / profile / calibrate / aa / redteam
├── tooling/rsi/        shared code: contracts, units, statistics, signatures, ledger, paths
├── schemas/            frozen JSON Schemas, the interface between skills
├── skills/             23 skills, one entry point plus five groups
├── playbooks/          eight tactics, ordered by bottleneck signature
├── kb/                 documented, measured, patterns, rules, antipatterns, conjectures, proposals
├── ledger/             experiments.jsonl (append-only) + artifacts/
├── operators/          one directory per operator: spec, reference, baseline, best, workspace
├── benchmark/          dev.txt and holdout.txt (holdout is invisible to agents)
├── orchestrator/       state machine, model routing, replay judge
├── templates/          the operator template
├── tools/rsi-cli       one command for everything
└── docs/               vocabulary, contracts, workflows, permissions, roadmap, porting
```

## Quick start

```sh
cd RSI

# 1. What does this machine have?
tools/rsi-cli doctor

# 2. Measure the device: peaks, noise floor, launch cost, cache steps, everything downstream reads.
tools/rsi-cli calibrate --suite core

# 3. Prove the evaluator catches cheating before trusting anything it says.
tools/rsi-cli redteam --op vec_add

# 4. Evaluate a candidate: correctness, then a signed timing result.
tools/rsi-cli evaluate --op vec_add --candidate operators/vec_add/baseline/kernel.py --out /tmp/r.json

# 5. Drive a campaign.
tools/rsi-cli campaign --op vec_add --budget 20
```

Step 3 is not optional. A framework that measures experience is only worth what its
measurement is worth, and the cheapest way to find out is to hand it kernels that cheat on
purpose. Eight of them ship, and each one names the detector responsible for it: six are caught by
correctness, and two — compute once, and cache the answer — are caught by the timing arm, because
those are the cheats a correctness-only evaluator cannot see.

## What is deliberately not automatic

- **Merging a scaffold change.** The replay judge decides; a human approves, early on every
  merge.
- **Editing the evaluator, tolerances, operator specs or the holdout list.** Human only. These
  are the things whose corruption makes every downstream conclusion noise.
- **Model routing.** Proposed by the outer loop, approved by a human, because it changes cost
  directly.
- **Fixing an evaluator loophole.** Only a human closes one, and the correction enters the
  system as a new check (W6).

## Honest status

The evaluator, tooling, schemas, all 23 skills, eight playbooks, the knowledge-base stores with
seed content, the orchestrator state machine and the replay judge are implemented and
exercisable on the reference backend. The seed rule cards are labelled examples, every playbook
records `Last replay: never`, and the ledger is empty: attribution needs real experiments on a
real device. [`docs/roadmap.md`](docs/roadmap.md) records what each phase still needs.
