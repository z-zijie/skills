# edit-scaffold strategy

## What may be changed, and by which gate

| Target | Gate | What a good change looks like |
| --- | --- | --- |
| `playbooks/*.md` | `replay_dev_holdout` | A reordering, a new confirm-first line, a new "do not" with its anti-pattern id |
| `skills/*/strategy/*.md` | `replay_dev_holdout` | A decision rule the skill was guessing at, stated as a check |
| `kb/patterns/` | `replay_dev_holdout` | A mechanism plus the counters that should move |
| `kb/hardware/measured/thresholds.yaml` | `replay_dev_holdout` | A threshold backed by a calibration file, never a guess |
| verifier check | `tighten_only_replay` | A check that catches a past case and flags no rightly-confirmed one |
| script or tool outside the evaluator | `unit_tests_plus_replay` | A unit test plus the same offline screen |
| `orchestrator/models.yaml` | `human_only` | A cost decision; propose, never apply |

Refused outright: the evaluator, microbenchmark code, `operators/*/spec.yaml`, tolerances,
`benchmark/holdout*`, and every `contract/` file. A refusal is not a puzzle to work around.

## Before you open the branch

For a playbook priority-order edit, answer all five:

1. **Which item moves?** Name it by the rule id it cites. An item with no rule id is a guess
   that the playbook inherited, and it should be retired rather than reordered.
2. **What does it move past?** The item above it is the thing currently tried first. If the new
   order would try a refuted transformation before a confirmed one, the change is wrong.
3. **What is the evidence for the new order?** Confirmation counts and prediction error from the
   rule cards, plus this cluster's experiments. Not the plausibility of the mechanism.
4. **What would make it wrong?** State the observation that would refute the reorder. A change
   without a falsifier cannot fail replay in a way anyone can interpret.
5. **Is the cluster behind it big enough?** Two experiments and a tag. One bad round is noise,
   and reordering a playbook on noise is how a scaffold learns superstition.

## Writing the change

- Say the condition, then the action, then the counter that moves. "When sectors per request
  exceeds 2 at this signature, coalesce before tiling, because the bound is set by transactions"
  is a playbook line. "Consider coalescing" is not.
- Keep the change inside the section that already exists. A new section in a playbook changes
  the whole document's reading order and costs more than the cluster is worth.
- Cite the rule or anti-pattern id on every line you add or move. An uncited line cannot be
  audited later.
- Preserve what the file already says about stopping. Stop conditions are the most expensive
  sentences in a playbook to reconstruct and the cheapest to accidentally delete.

## Choosing the cluster

Replay is the most expensive thing in the system, so rank clusters by expected return:

1. **`not_implemented`** first when it is large. The fix is a clearer card or a smaller scope,
   and it is text, not knowledge. It also lifts every later experiment, since fewer rounds are
   wasted on retries.
2. **`wrong_diagnosis`** next. Fixing a signature threshold or a diagnosis line changes what
   every future hypothesis aims at.
3. **`dead_end`** is the cheapest high-confidence edit: move a twice-refuted transformation out
   of the order and the playbook stops offering it.
4. **`large_prediction_error`** usually is not a playbook edit at all. It points at the hardware
   model, so the answer is `design-microbench`, not prose.
5. **`unexplained_speedup`** is a research question. It wants a follow-up experiment, not a
   scaffold change.

Do not target `correctness` or `implausible`. Neither is a scaffold problem.

## The change note is the deliverable

A reviewer reads the note, not the diff, and the replay judge reads it after the numbers are in.

- `why_this_cluster` names the kind, the count, the tags and the experiment ids.
- `what_changed` is the text, or a faithful summary of a diff that speaks for itself.
- `what_did_not_change` is what protects the attribution claim: no other file, no other section,
  no second mechanism.
- `expected_effect` is a prediction. "The not-implemented rate falls from 0.3 to below 0.15 on
  the two screen operators" can be wrong, and being wrong usefully is the point.
- `revert` names the mechanical way back. One file and one merge means one revert.

## After a rollback

- Record it in `CHANGELOG-scaffold.md` with the replay report id, and do not retry the same text.
  The script refuses a change flagged `same_change_already_rolled_back`, and the flag belongs in
  the input rather than in someone's memory.
- The failure is evidence about the cluster. If the change targeted `not_implemented` and did
  not shrink it, the cluster was misdiagnosed, and the next attempt should start from
  `analyze-failures` rather than from a variant of the same wording.
- Two rolled-back changes against the same cluster means the cluster is not addressable by
  strategy text. Hand it to a tool proposal or to a human.
