---
name: register-conjecture
description: Register a human conjecture as a quarantined unverified prior and generate the smallest experiments that would test it, with the outcome table that later settles it. Use during W7, whenever a human has an idea the ledger has not verified.
---

# register-conjecture

W7. A human has a belief about the hardware or the operator that no experiment supports yet. It
goes into `kb/conjectures/` marked unverified, with one to three minimal experiments that could
settle it and a table that maps each possible result to an outcome.

A conjecture may inspire hypotheses. It cannot verify anything and it is not rule evidence. The
plan exists so the conjecture stops being an opinion as cheaply as possible.

## When it runs

- W7, when a human states an idea that the knowledge base neither supports nor contradicts.
- When a rule's scope was narrowed and the discarded part deserves recording as an open question.
- When `analyze-failures` sees a cluster no rule explains, and a human offers an explanation.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `conjecture` | The claim in one sentence |
| `expected_observation` | What would be seen if it were true; a conjecture without this is refused |
| `signature_tags`, `metric` | The scope and the canonical metric it predicts to move |
| `microbench` | A microbenchmark that isolates the mechanism, when one exists |
| `related_experiments` | Past experiments that may already answer it |
| `budget_experiments`, `repeats` | Caps; the plan never exceeds three experiments or three repeats |

## Steps

1. Restate the conjecture as a sentence someone can disagree with, and record the observation
   that would settle it. Done when both are in the input, not implied by it.
2. Name the canonical metric and the signature tags. Done when every tag is in
   `docs/vocabulary.md` and the metric is in `tooling/rsi/metrics.py`.
3. Run `python3 scripts/register.py --input <file>`. Done when it prints one result object.
   When `problems` is not empty, fix the input; a refused conjecture is not registered.
4. Read `strategy/checklist.md` section "Sizing the experiment" when the plan proposes a campaign
   experiment. That is the expensive case and it needs a justification the script cannot give.
5. Check the object against `contract/output.schema.json`. Done when validation reports no
   problems.
6. Write the card to `kb/conjectures/<id>.yaml` and place the plan where the orchestrator will
   pick it up. Done when the card validates and the plan's first experiment is the cheapest one
   that could refute the claim.

## Output

`contract/output.schema.json`. Read these:

- `conjecture` - the card, `status: conjecture`, with the statement, scope, expected observation
  and the metric.
- `plan.experiments` - one to three, each with its `kind`, `why_minimal`, `repeats` and, where
  relevant, the microbenchmark or the ledger ids that might already answer it.
- `plan.outcome_table` - what `promoted`, `refuted` and `open` mean for this specific conjecture.
- `plan.limits` - `counts_as_evidence: false`, `may_be_used_for_verification: false`,
  `may_inspire_hypotheses: true`. These are not advisory.
- `notes` - what the orchestrator will do next that this skill cannot.

## Judging principles

- Demand the observation before registering. A conjecture with no observable consequence cannot
  be refuted, and an unfalsifiable entry in the store is worse than no entry.
- Test it with the cheapest thing that could refute it. A microbenchmark before a campaign
  experiment, one case before a sweep, and a re-read of the ledger before any device time.
- Size the plan to the claim. A conjecture about one mechanism needs one case; a conjecture about
  a trade-off needs two. Three is the ceiling, not the target.
- Keep it quarantined. Do not let a conjecture into a rule's evidence list, a verifier's
  reasoning or a retrieval result presented as a fact; the store's status field is what makes
  that visible, and it must stay `conjecture` until an experiment says otherwise.
- Say what would refute it in the same words the experiment will use. An outcome table written
  after the result is not a test.
- Prefer leaving it open to promoting it. `open` is an honest outcome when the experiment could
  not be made valid, and it costs nothing but a dated entry.
