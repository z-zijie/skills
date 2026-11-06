# How to verify this scaffold before trusting it

Every claim the framework makes about itself is checkable by a command. This page lists the
claims and the command that checks each one, so "it works" is an observation rather than an
assertion.

Run them in order. The later ones assume the earlier ones hold, which is the same order the
roadmap's phases use.

## Run everything

```sh
python3 tools/demo.py
```

Nine steps: calibration, the red-team check, correctness, signature refusal, signature and
roofline, retrieval, the ledger, the state machine, and the test suite. On a machine with no
accelerator it runs against the reference backend.

## Claim by claim

| Claim | Check | What a failure means |
| --- | --- | --- |
| The contracts accept real documents and reject malformed ones | `python3 tests/run_all.py` | A schema that is too loose accepts bad data downstream; one that is too strict blocks a valid step. |
| The evaluator catches cheating | `tools/rsi-cli redteam --op vec_add` | A hole in Layer 0. Everything measured afterwards is suspect. |
| Timing distinguishes signal from noise | `python3 tests/run_all.py` (the timing cases) | The win rule would report gains that are jitter. |
| The ledger refuses forged and duplicate results | `python3 tests/run_all.py` (the ledger cases) | Experience could be fabricated. |
| The write policy refuses what it should | `python3 tests/run_all.py` (the paths cases) | An agent could edit the evaluator or the holdout split. |
| Every skill's own regression cases pass | `python3 -c "import sys;sys.path.insert(0,'tooling');from rsi import skills;[print(p.name, skills.replay_tests(p.name)['failed']) for p in skills.discover()]"` | A strategy change broke a labelled past case. |
| The documented layer is sourced and indexed | `python3 skills/ingest-docs/scripts/index_documented.py --check` | A hardware fact with no source is circulating. |
| The knowledge base is internally consistent | `tools/rsi-cli kb-audit` | A rule's status disagrees with its evidence, or a conjecture is cited as evidence. |
| Both retry loops are bounded and stop conditions fire | `python3 tests/run_all.py` (the campaign cases) | The loop could manufacture evidence from changes that never landed. |
| The replay judge refuses noise and holdout regressions | `python3 tests/run_all.py` (the replay cases) | A scaffold change could merge on noise. |
| The framework is device-neutral | `python3 tests/run_all.py` (the vendor-vocabulary case) | A skill or playbook would only make sense on one device. |

## What cannot be checked without a device

Two things, and they are the two that matter most:

- **The measured layer.** Peak bandwidth, peak compute, the noise floor and the rest are measured
  on the machine that will run the operators. Without them the roofline refuses to compute rather
  than inventing a bound, and that refusal is deliberate.
- **Attribution.** The ledger is empty on a fresh clone, so no rule can cite evidence and no
  scaffold change has been replayed. `tools/rsi-cli kb-audit` reports this honestly as "not
  evaluable: the ledger has no experiments yet".

The seed rule cards are labelled examples that describe the *shape* of evidence, not evidence.
They exist so a reader can see what a scoped, evidenced claim looks like before there is data to
write one from.

## Verifying the verifier

The verifier makes mistakes too, so it is audited rather than trusted:

- From every 50 experiments, sample 5 for human review and record agreement in the disagreement
  log.
- When agreement falls below 90 %, add a check through W6 before reaching for a stronger model. A
  missing check is a specification problem; a stronger model only guesses better at an
  unspecified task.
- Each skill's `tests/` directory is that skill's own regression suite: a strategy change is
  replayed over labelled past cases first, and a change that breaks one is caught before it
  reaches a campaign.
