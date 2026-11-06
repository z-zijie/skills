# Roadmap

Build in five phases: make results trustworthy, automate verification, let the agent propose
hypotheses, and only then let the system modify itself. Each phase ends with a system you can
use, and no phase starts until the gate before it is met. Every gate is something measured.

| Phase | Deliverables | Skills it adds | Exit criteria |
| --- | --- | --- | --- |
| P0 Evaluator and ledger | Evaluator CLI, anti-cheating and red-team tests, A/A noise floor, ledger schema, first operator onboarded | `run-evaluator`, `collect-profile`, `record-experiment`, `implement-change` | Two evaluations of the same kernel differ by less than the noise floor; every red-team cheating kernel is caught; one operator's manual optimization is fully recorded |
| P1 Diagnosis and hardware calibration | Bottleneck signature, first microbenchmark suite, documented and measured hardware layers, roofline bound | `diagnose-bottleneck`, `calibrate-hardware`, `ingest-docs` | Signatures agree with a human's manual diagnosis on at least 80 % of past samples; no measured result beats the roofline bound |
| P2 Mechanism verifier | Verifier and checklist, all past corrections encoded as checks, first anti-patterns | `inspect-binary`, `verify-mechanism`, `capture-correction` | On past attempts a human has labelled, verdicts agree at least 90 % of the time and fewer than 10 % of superficial implementations slip through |
| P3 Agent proposes hypotheses | Orchestrator, seeded `patterns` store, eight playbooks, campaign reports, disagreement log | `optimize-operator`, `retrieve-knowledge`, `propose-hypothesis`, `plan-campaign`, `register-conjecture`, `report-campaign` | A human vetoes fewer than 20 % of hypotheses on two operators in a row; one operator completes unattended at 90 % or more of the human's manual result; the benchmark reaches 5 to 10 operators |
| P4 Outer loop | Holdout mechanism, replay judge, rule life cycle, scaffold changelog | `analyze-failures`, `distill-rules`, `audit-knowledge`, `edit-scaffold`, `replay-benchmark`, `design-microbench` | At least 3 scaffold changes pass replay and merge; the primary metric trends up on holdout |

The percentages are suggested starting values. Adjust them once there is data.

## Two places where the order cannot change

- **Do not skip P2 and go straight to P3.** Without a verifier, the agent's own hypotheses
  produce "experience" mixed with superficial implementations, and no rule distilled from it can
  be trusted.
- **Do not start P4 early.** With fewer than 5 operators in the benchmark, replay cannot tell
  signal from noise, and the outer loop will only manufacture superstition.

## What the ten P0–P2 skills already give you

A working semi-automatic loop: a human proposes hypotheses, and the system implements, verifies
and records. That is the minimum viable version, and it is honest output before the system is
allowed to judge itself.

## First week

- [ ] Pick an operator already optimized by hand as the first to onboard. You know the answer, so
      you can check the system against it.
- [ ] Run `tools/rsi-cli calibrate --suite core` on the target machine and confirm the measured
      peaks look sane before any campaign starts.
- [ ] Run `tools/rsi-cli redteam --op <op>` and confirm every deliberately cheating kernel is
      caught.
- [ ] Collect the cases where you corrected an agent into a list. That is the raw material for
      the P2 checks.

## Status of this repository

Shipped: the evaluator, the shared tooling, the schemas, all 23 skills, eight playbooks, the
knowledge-base stores with seed content, the orchestrator state machine and the replay judge.
Not yet done, because it needs a real device and real experiments: calibration on the target
machine, the first onboarded operator's baseline, and any replayed scaffold change. Every
playbook records `Last replay: never` for that reason, and the seed rule cards are labelled as
examples.
