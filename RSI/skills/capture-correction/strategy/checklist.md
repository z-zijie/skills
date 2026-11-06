# capture-correction strategy

## The four classifications

| Classification | The human is saying | Asset | Gate |
| --- | --- | --- | --- |
| `verifier_check` | the mechanism did not really land, and the verdict said it did | a predicate added to the verifier | `tighten_only_replay` |
| `antipattern` | that hypothesis was wrong, and it will be proposed again | an anti-pattern card | `mechanical` |
| `playbook` | the right tactic was tried in the wrong order, or not tried | a playbook edit | `replay_dev_holdout` |
| `evaluator_loophole` | the candidate got credit it did not earn | a precise description for the human | `human_only` |

The classification decides the gate, and the gate decides how expensive the correction is. A
misclassification that turns an anti-pattern into a check costs a full replay and, worse, leaves
the wrong asset behind.

## Choosing a predicate

The predicates the script supports, and when each is right:

| Predicate | Fires when | Use for |
| --- | --- | --- |
| `metric_unmoved` | the named counter moved less than `band` | "the change was cosmetic; the counter never moved" |
| `metric_moved_wrong_way` | the counter moved against the card's prediction | "the verifier accepted a regression as a win" |
| `no_gain_but_confirmed` | the mechanism confirmed and the timing did not clear noise | "a confirmed mechanism with no speedup is a wrong diagnosis, not a win" |
| `evidence_line_missing` | no implementation-evidence line contains the required text | "the diff never showed what the card claimed" |
| `scope_mismatch` | the card's scope word does not appear in `scope_of_change` | "the change went outside the scope the card declared" |
| `unnamed_regression` | a counter outside the card got clearly worse | "the win was paid for somewhere the card did not look" |
| `implausible_speedup` | the review cap was crossed | "this needs a human, not a verdict" |
| `tag_overlap` | the entry's tags intersect a required set | "this check only applies under this signature" |

Choose the narrowest predicate that catches the corrected case. `metric_unmoved` with a band
that admits normal variation catches cosmetic changes; the same predicate with a wide band
catches honestly partial ones too, and that is the failure mode the replay is looking for.

## Reading the replay

- `caught` must contain the corrected experiment. If it does not, the predicate is not expressing
  the correction, whatever it does catch.
- `flagged_rightly_confirmed` must be empty. Every id in it is a real win the new check would
  have thrown away, and each one costs more than the mistake being corrected.
- `missed_expected` names the past cases of the same kind that the check let through. A check
  that catches one of five is a check that will be re-learned four more times.
- `could_not_evaluate` is not a pass. An entry whose counters are absent tells you nothing, so a
  check that is only safe because the data was missing is not safe.
- `passes` is the conjunction of the first two and an empty `missed_expected`. When it is false,
  narrow the predicate and rerun before accepting the downgrade to anti-pattern.

## When the correction is an evaluator loophole

This is the one classification this loop must never fix itself.

- Describe the loophole in terms of what the candidate did and what the evaluator failed to
  check. Name the guard from the anti-cheating checklist that should have caught it.
- Do not propose a change to `evaluator/`, the tolerances, `spec.yaml` or the holdout list. The
  proposal's target names the file, and its status is `needs_human`.
- A loophole is a red-team case. Once the human fixes it, the same kernel should be added to the
  red-team set, and that is the durable correction rather than the prose.

## Keeping the check set small

The verifier's checklist grows with every correction, and a checklist nobody can hold in mind
stops being applied. Prune as you add:

- When a new check subsumes an old one, propose replacing both with the general form.
- When two checks fire on the same entry for the same reason, keep the one that states the
  mechanism.
- Track how often each check fires over recent history. A check that has not fired in a hundred
  experiments is either solved or too narrow, and both are worth knowing.

## After capture

- The correction is evidence about the verifier, not only about the experiment. Note it in the
  verifier's own quality record: the roadmap asks for agreement with the human to stay above 90%,
  and each correction is one disagreement with a known cause.
- If the same classification recurs, the problem is upstream. Repeated `verifier_check`
  corrections on one signature mean the hypothesis cards for that signature are written too
  loosely, and that belongs to `edit-scaffold` against a real cluster.
