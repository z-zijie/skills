---
name: propose-hypothesis
description: Propose one to three ranked hypothesis cards for the current bottleneck, each with
  a mechanism, named canonical counters with predicted directions, and a predicted speedup. Use
  at W2 step 3 after retrieve-knowledge, and again when plan-campaign changes direction.
---

# propose-hypothesis

The one creative step in the inner loop, bounded by evidence. A card is an experiment that can
fail: a mechanism, the counters it moves, and a speedup it predicts. A card that cannot be
falsified by the verifier is not a card.

## When it runs

- Step 3 of W2, after `retrieve-knowledge` has written `context.md`.
- After a confirmed-but-not-faster verdict, which means the diagnosis was wrong and the card was
  right: the next card must attack the newly named bottleneck.
- When `plan-campaign` combines two independently confirmed changes into one card.

## What you receive

One JSON object matching `contract/input.schema.json`: the `signature`, the `context_md` from
retrieval, the candidate `code` (or a path to it), the `parent` version identifier, the
`target_attainment_pct` and the device's `noise_threshold` when one has been measured, plus
`unavailable_metrics` — the list the device cannot produce.

## Steps

1. Read `context.md` first, then the code. Done when every rule and anti-pattern you rely on is
   named in the card's `based_on`.
2. Name the mechanism in one sentence: what the machine does now, what it would do instead, and
   why that changes the named counters. Done when the sentence mentions no tool and no vendor.
3. Choose counters from the canonical registry, and only from the metrics the device produces.
   Done when every `metric` is registered and absent from `unavailable_metrics`; a card naming an
   unavailable counter is rejected, because the verifier could never confirm it.
4. Predict a direction for each counter, and a value or bound where the mechanism implies one.
   Done when each prediction states what would refute it.
5. Predict a speedup as `expect`, `low`, `high`. Done when `low` is above the device's noise
   threshold, or the card says why a sub-noise gain is still worth running.
6. State `evidence_of_implementation` as checkable statements about the diff and the disassembly.
   Done when each statement names something the verifier can locate in a file it receives.
7. State `scope_of_change`: one variable per experiment. Done when the card can be implemented
   without touching anything outside that scope.
8. Rank the cards and emit one to three. Done when the highest-ranked card is the one with the
   strongest mechanism and the best evidence base, and each card validates against
   `schemas/hypothesis.schema.json`.

## Output

`{status, cards, rejected, problems, notes}` per `contract/output.schema.json`. Each card is the
object `schemas/hypothesis.schema.json` defines, which is what `implement-change` implements and
`verify-mechanism` judges.

## Judging principles

- Prefer a mechanism you can state in one sentence over a transformation you have seen work
  elsewhere. A card whose mechanism is vague produces a verdict of `not_implemented` even when the
  code changed correctly.
- Ground each card in the retrieved entries or in the code in front of you, and cite them in
  `based_on`. An unsourced idea may still be proposed, and it is marked as resting on nothing.
- Predict directions, not adjectives. "Fewer device-memory transactions per request" is a
  prediction; "better coalescing" is a hope.
- Keep one variable per card. Two changes in one card make a confirmed mechanism unattributable,
  and an unattributable confirmation is not evidence for a rule.
- Answer `rejected` with the reason when a candidate idea names a counter this device cannot
  produce, or when it would need a change outside the operator. A rejected idea with a reason is
  usable; a silently dropped one is not.
