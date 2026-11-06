# What this scaffold guarantees, and what it does not

A framework whose job is to detect cheating has to be unusually precise about its own claims. This
page states each guarantee, the mechanism behind it, and the case where it does not hold. Read it
before trusting a number this system reports.

## Guarantees

| Guarantee | Mechanism | Where it holds |
| --- | --- | --- |
| A result was produced by the evaluator, over this code, under these tolerances | HMAC over the body, which includes `candidate_hash` and `tolerance_hash` | Any deployment that keeps the key file from agents |
| A forged result does not enter the ledger | `ledger.append` refuses an entry with no evaluator signature | Always |
| Candidate code cannot read the key or the reference output | Timing runs in a separate interpreter with the key stripped from its environment | Correctness runs in-process, so this covers timing and not the correctness pass |
| A kernel that skips work after the first call is not timed | The timed arm clears the output and calls again | Wherever outputs are observable, which is every shipped backend |
| A kernel that replays a cached answer is not timed | The timed arm perturbs an input and checks the output follows | Same |
| Shapes and inputs are fresh each run | A seed drawn per run and recorded in the result | Always; pass `--seed` to reproduce |
| A gain must beat the device's own noise floor | A/A test, interleaved arms, the lower bound of a bootstrap interval | After calibration; before it, the fallback threshold is conservative |
| An attainment above 100 % is refused rather than reported | The roofline raises on an impossible result | Always |
| A campaign total is not the product of alternatives | The report refuses to compound steps measured against the same parent | Always |
| A scaffold change cannot merge without holdout results | The replay gate refuses an empty holdout and fewer than two dev operators | Full replays; screening declares its own scope |
| History is never rewritten | The ledger is append-only and rejects a duplicate id | Always |

## What is not guaranteed

**The write boundary is a guardrail, not a sandbox.** `require_writable` is enforced at the three
writes the system performs, and it stops an agent from wandering into `evaluator/` or the knowledge
base. It does not stop a process that has the owner's filesystem rights. Run the evaluator as a
different user for that — `permissions.md` has the three commands.

**Correctness runs candidate code in the evaluator's process.** A candidate can, in principle,
patch something during its correctness run that affects the rest of that process. Timing is
insulated (separate interpreter), and the signature covers the result body, so the blast radius is
the one evaluation rather than the ledger. Closing it fully needs correctness in its own process
too, which is a cost this scaffold has not yet paid.

**The measured layer is only as good as its calibration.** Everything about attainment, the noise
floor and a rule's effect depends on peaks measured on this machine. A calibration that measured
the host CPU while declaring an accelerator is recorded as `trustworthy_for_roofline: false`, and
the roofline refuses it — but a wrong `device.yaml` with a plausible-looking calibration is not
detectable from inside.

**Seed rule cards are examples.** They are marked `example: true`, their evidence ids are reserved
(`example-exp-…`), and `kb-audit` reports them separately. A clean audit means no *defect*; it does
not mean the claims are supported. Only the ledger can say that, and it is empty until real
experiments run.

**The verifier is a model judgement.** It is bounded by its checklist, isolated from the timing
result, and audited by sampling, but it can still be wrong. `metrics.md` says how to measure its
agreement and what to do when it falls.

**The playbooks have never been replayed.** Every one records `Last replay: never`. Their tactics
are drawn from the build plan and the documented layer, not from this machine's evidence.

## The one-line version

The evaluator is the only source of truth, its signatures bind the code and the rules they were
made under, and everything above it — diagnoses, hypotheses, rules, playbooks — is a claim that
gets better or worse with evidence but never becomes a fact.
