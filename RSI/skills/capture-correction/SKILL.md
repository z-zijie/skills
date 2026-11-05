---
name: capture-correction
description: Turn one human correction into a verifier check, an anti-pattern, a playbook edit or an evaluator-loophole report, after replaying a proposed check over ledger history. Use during W6, whenever a human says an experiment's conclusion or verdict was wrong.
---

# capture-correction

W6. A human points at one experiment and says what the agent got wrong. This skill turns that
into the smallest asset that stops the mistake recurring, and it proves the asset works before
proposing it. The goal is stated in the workflow: each kind of correction is needed once.

A new verifier check is replayed over ledger history first. It must catch the past cases of the
same kind, and it must not flag an experiment that was rightly confirmed. A check that fails
either half becomes an anti-pattern instead, because a check nobody can trust is worse than no
check.

## When it runs

- W6, at any time, when a human rejects a verdict, a speedup claim or a diagnosis.
- After a verifier audit, when agreement with the human has fallen below the threshold the
  roadmap names.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `experiment_id` | The experiment being corrected |
| `correction_note` | The human's words: what was concluded, what is right |
| `classification` | Named when known; inferred from the note otherwise |
| `check` | The proposed check, with `predicate`, `checks_metric`, `must_catch`, `must_not_flag` |
| `ledger` | The window to replay over. Read-only |

## Steps

1. Classify the correction. Done when it is exactly one of `verifier_check`, `antipattern`,
   `playbook` or `evaluator_loophole`. A correction that seems to be two of these is two
   corrections, and the human should pick the more important one.
2. For `evaluator_loophole`, stop after step 4. Only a human edits the evaluator, the tolerances
   or the holdout list, and the proposal's job is to describe the loophole precisely enough to
   fix.
3. Express the correction as a predicate. Done when a reader can tell, from the predicate and
   its band, which past experiments it would have caught.
4. Run `python3 scripts/capture.py --input <file>`. Done when it prints one result object. When
   `replay.passes` is false, read `replay.flagged_rightly_confirmed` and `replay.missed_expected`
   and narrow the predicate rather than accepting the downgrade to anti-pattern.
5. Check the object against `contract/output.schema.json`. Done when validation reports no
   problems.
6. Write the proposal under `kb/proposals/corrections/` and, for an accepted check, the check
   card under `kb/verifier/checks/`. Done when both validate and `proposal.gate` is the one the
   result reported.

## Output

`contract/output.schema.json`. Read these:

- `classification` - what the correction became. It can differ from the input when a check failed
  replay and was downgraded.
- `proposal` - `kind`, `rationale` (the human's note, quoted), `target`, `evidence`, `gate`,
  `status`. `needs_human` marks the evaluator loophole.
- `replay` - `caught`, `flagged_rightly_confirmed`, `could_not_evaluate`, `missed_expected` and
  `passes`. Quote the ids; "the check works" is not a claim a reader can check.
- `check` - the check card, when one was accepted.
- `problems` - one entry per refusal, naming the rule.

## Judging principles

- Quote the human, then generalise. The note is the evidence and the rationale; a paraphrase
  loses the detail that made the correction necessary.
- A check is a tightening or it is nothing. If the correction implies letting more experiments
  through, the answer is a human decision on the verifier, not a proposal from this loop.
- Make the predicate small enough to read. A check whose band or metric a reviewer cannot verify
  in under a minute will be trusted blindly, which is how a verifier drifts.
- Prefer the check that catches the specific mistake to the one that catches a family of them.
  Over-broad checks flag rightly-confirmed work, and the replay exists to catch exactly that.
- Write down what could not be evaluated. An entry without the counters a predicate needs is not
  evidence that the check is safe, and `could_not_evaluate` is how that stays visible.
- One correction, one asset. When the human has corrected two things, file two proposals; a
  merged one cannot be replayed against either.
