---
name: verify-mechanism
description: Judge whether a kernel change really implemented the mechanism on its hypothesis
  card. Use after run-evaluator reports that correctness passed and before the timing result is
  unsealed; it answers only "did this change do what it claimed", never "is it faster".
---

# verify-mechanism

The verifier answers one question: did the mechanism on the hypothesis card really happen in
this change? It does not care whether the kernel got faster, which is exactly why it cures
superficial attempts. A change that was written but did not reach machine code, or reached it
without moving the counters it promised, is `not_implemented` and counts toward no rule.

## What you receive

- `hypothesis.yaml` — the card, including `predictions` and `evidence_of_implementation`
- `change.diff` — the code change under test
- `binary_before.json`, `binary_after.json` — disassembly summaries
- `profile_before.json`, `profile_after.json` — canonical counters
- `measured.yaml` — the device's measured parameters, for each counter's run-to-run variation

Match `contract/input.schema.json`. You do not receive the timing result, the implementer's
notes or any summary of them, and you do not look for them: seeing "it got faster" biases a
verdict toward "it was implemented".

## Steps

1. **Confirm the change exists on the hot path.** Run
   `scripts/check_diff.py --diff change.diff --card hypothesis.yaml`. It reports whether the
   change is non-empty, whether it touches the kernel's hot path rather than comments or host
   code, and whether it stays inside the card's `scope_of_change`. Done when every one of those
   three has an answer.

2. **Locate each implementation-evidence item.** For every entry in
   `evidence_of_implementation`, name the place in the diff or the disassembly summary where it
   is visible, or record it as missing. Done when every item on the card has a location or a
   `missing` entry — not merely the plausible ones.

3. **Check the machine code.** Go through group B of `strategy/checklist.md`: the disassembly
   shows the change, the disassembly before the change did not already show it, and register
   use, scratchpad use and launch configuration match the card. Done when B1 through B5 each
   have a conclusion.

4. **Compare the counters.** Run
   `scripts/compare_counters.py --card hypothesis.yaml --before profile_before.json --after
   profile_after.json --measured measured.yaml`. Every counter the card names must have moved
   in the predicted direction by more than its own measured variation. A counter the device
   does not report is `unknown`, which cannot support a `confirmed` verdict.

5. **Cross-check an unreliable counter.** When a cited counter is listed in
   `kb/hardware/documented/profiler-metric-pitfalls.md` as known to misread, corroborate it with
   the alternative that entry names before resting anything on it.

6. **Look for the superficial forms.** Go through group D of `strategy/checklist.md` — the
   growing list that started from past corrections. Done when each D item is answered for this
   specific change.

7. **Decide.** Write `verdict.json` per `contract/output.schema.json`. When the evidence is
   incomplete, the verdict is `not_implemented`. Done when the file passes the contract and every
   check in `checks` carries a conclusion and an evidence location.

8. **Say what the verdict closes.** A `refuted` or `not_implemented` verdict closes a direction,
   and which direction and how widely it was tried are what a later revisit reads. Record the
   `direction` slug, the `parameters` the card varied and the `points_tried`, so `record-experiment`
   can build the closure from your checks. Done when a two-point sweep and a one-point guess are
   distinguishable from the verdict alone.

## Output

`verdict.json`:

- `verdict`: `confirmed`, `refuted` or `not_implemented`
- `checks`: one entry per check id, each with `group`, `conclusion` and `evidence`
- `missing`: for `not_implemented`, the evidence that is absent — the implementer retries from this
- `counter_checks`: before, after, direction and whether it exceeded noise, per counter
- `unreliable_metrics_cross_checked`: which of those you corroborated, and how

## What each verdict means

| Verdict | Condition | What happens next |
| --- | --- | --- |
| `confirmed` | Implementation evidence is complete and the named counters moved as predicted | Recorded; positive evidence for the rule |
| `refuted` | Implementation evidence is complete, counters did not move as predicted | Recorded; negative evidence for the rule |
| `not_implemented` | Implementation evidence is incomplete | Back to the implementer, at most twice; then an implementation failure that counts toward no rule |

The verdict and the speed combine into four outcomes; `strategy/checklist.md` names what each
one means and what to do about it. The most valuable of the four is *mechanism confirmed, not
faster*: the mechanism worked, so the diagnosis was wrong, and the signature thresholds or the
playbook need fixing rather than the kernel.

## Judging principles

- Decide on evidence only. "The code looks reasonable" is not evidence, and neither is a diff
  that merely contains the shape of the change.
- A counter change must exceed that counter's own run-to-run variation. A move inside the
  measured jitter is not a move.
- When unsure, answer `not_implemented`. A wrong `confirmed` pollutes the knowledge base and
  costs more than a retry.
- Report what you saw, not what the card wanted: a check whose evidence is absent is `unknown`,
  and `unknown` in group A or B means `not_implemented`.
- One experiment changes one variable. A diff that carries extra edits fails C and D even when
  the named counters moved.
