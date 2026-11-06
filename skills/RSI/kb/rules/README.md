# Rules store

Conditional, scoped claims: **under condition C, transformation T changes metric M by delta**.
A rule is never "always do T", and a rule whose statement still makes sense with its scope deleted
was written wrong.

## What a card must carry

The shape is `schemas/rule.schema.json`, enforced by `knowledge.validate_rule()`. Beyond the
schema:

| Field | What it is for |
| --- | --- |
| `scope.hw` | the hardware ids this claim was verified on; a device change demotes the card |
| `scope.tags_all` / `tags_any` / `classes` | the signature conditions under which the claim applies |
| `scope.elem_bytes`, `scope.op_traits` | shape and operator conditions, when the evidence supports narrowing |
| `effect.metric` + `direction` | the canonical counter the transformation moves, and which way |
| `effect.speedup_median` + `range` | the effect size, as observed, not as hoped |
| `evidence.confirmed` / `refuted` | experiment ids; both lists, always |
| `confirm_rate` | confirmations over attempts; the life cycle reads this |
| `mean_prediction_error` | \|log(measured / predicted)\|; above 0.2 forces `under_review` |
| `last_verified` | when the evidence was last checked |
| `counter_example_note` | what the refutations actually showed |

## The life cycle

| State | Entry condition | Use |
| --- | --- | --- |
| `conjecture` | no evidence | inspire hypotheses only |
| `candidate` | mechanism confirmed at least once | retrievable, labelled thin evidence |
| `established` | 3+ confirmations, 2+ operators, rate >= 0.70 | enters the playbook priority order |
| `under_review` | calibration changed, new counterexample, or prediction error above 0.2 | retrievable with a warning |
| `deprecated` | fails review, or rate below 0.40 | kept as a worked negative; the actionable form moves to `antipatterns/` |

Status is computed by `tooling/rsi/knowledge.py::lifecycle()`. An author proposes evidence; the
code decides the state.

## What is in the store now

Four seed cards, one per life-cycle state:

| Card | State | Purpose |
| --- | --- | --- |
| `R-001-contiguous-access-splits.yaml` | established | shows a card that has earned its place in a playbook's priority order |
| `R-002-wide-loads-candidate.yaml` | candidate | shows a single-confirmation lead, thin evidence and all |
| `R-003-register-pressure-review.yaml` | under_review | shows a card demoted by prediction error, with the counterexamples attached |
| `R-004-workgroup-size-deprecated.yaml` | deprecated | shows a refuted claim, and its anti-pattern counterpart in `A-002` |

**These are seed examples.** Every experiment id in them (`exp-0001` and so on) is illustrative
and does **not** exist in `ledger/experiments.jsonl`; each file says so in a comment at the top.
They exist to exercise the validator, the retrieval path and the playbook cross-references before
the first real campaign distils anything.

Replace them, do not patch them: a distilled card carries real experiment ids, a real
`last_verified`, and a `mean_prediction_error` computed from measured prediction errors. A seed
entry silently amended into looking real is worse than no entry at all.
