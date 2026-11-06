---
name: edit-scaffold
description: Propose exactly one change to a playbook, a skill strategy, a pattern or a tightening verifier check, aimed at one failure cluster, on a branch with a change note. Use as step 4 of W5, after analyze-failures has ranked the clusters and only one of them is being targeted.
---

# edit-scaffold

Step 4 of the outer loop. It writes one change, for one cluster, in one file, and packages it so
that a replay can attribute whatever happens to it. It never merges; the gate in
`docs/workflows.md` decides.

The script picks the gate from the target and refuses the targets the outer loop may not touch.
Your job is the wording of the change and the choice of the single cluster worth the replay.

## When it runs

- W5 step 4, after `analyze-failures` and `audit-knowledge`, and after a human has agreed the
  top cluster is worth replay budget.
- Once per cluster. Two open proposals against one cluster are two changes measured together,
  and neither can be attributed.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `cluster` | The ranked cluster with its `tags` and `experiments`; quoted, not recomputed |
| `target`, `change_kind` | The one file; the kind is inferred from the path when omitted |
| `change` | The new text or diff itself, not a description of it |
| `tightens_only` | Required for a verifier check; a loosening needs a human |
| `screen_operators`, `full_operators`, `experiments_per_operator`, `repeats` | The replay plan |
| `same_change_already_rolled_back` | Refuses a change the changelog already rejected |

## Steps

1. Pick the cluster and quote it. Done when `cluster.experiments` names at least two ledger ids
   that `analyze-failures` produced in this window.
2. Decide what kind of object the fix is, before writing it. Done when the answer is one of:
   playbook ordering, strategy wording, a pattern card, a threshold, a tool script, a tightening
   check. A fix that needs two of those is two changes.
3. Write the change as new text. Done when the text can be applied to the target without
   further editing, and touches nothing else in that file.
4. Run `python3 scripts/propose.py --input <file>`. Done when it prints one proposal object and
   `refusals` is empty. A refusal is the boundary working; take the change to a human instead of
   routing around it.
5. Check the object against `contract/output.schema.json`. Done when validation reports no
   problems.
6. Read `strategy/checklist.md` section "Before you open the branch" when the target is a
   playbook order. Priority-order edits are the cheapest change and the easiest one to get
   wrong.
7. Create the branch, apply the change, commit with `branch.commit_message`, and write the
   proposal to `kb/proposals/scaffold/<proposal-id>.yaml`. Done when the branch differs from its
   base in exactly the one file in `branch.files_changed`.

## Output

`contract/output.schema.json`. Read these:

- `proposal` - `kind`, `title`, `rationale`, `target`, `diff`, `evidence`, `gate`, `status`.
  `status` is `draft` when the change may go to replay and `rejected` when it was refused.
- `branch` - one file, a commit message that names the cluster and the proposal id.
- `change_note` - why this cluster, what changed, what deliberately did not, and how to revert.
- `replay` - two-stage screening, a fixed experiment budget, at least three repeats, and
  `holdout_visible_to: replay_judge`. The plan is binding on `replay-benchmark`.
- `refusals` - names the rule that blocked the change. Empty means ready.

## Judging principles

- One change, one cluster, one file. The whole value of the outer loop rests on attribution, and
  a branch with two edits answers nothing however good the edits are.
- Target the cluster with the best return per replay, not the most interesting one. A
  not-implemented cluster is usually the cheapest to shrink and the least likely to be a real
  knowledge gap.
- Prefer text a future reader can act on. "Check coalescing before tiling under this signature"
  survives a replay; "be more careful" does not.
- State the evidence. `rationale` names the cluster kind, its count and its tags, so a reader can
  disagree with the choice rather than with the result.
- Write the change as a tightening. Wording that widens a playbook's scope, weakens a check or
  relaxes a threshold is the one direction this loop must not drift, so a change that needs to
  loosen anything goes to a human.
- Expect to be rolled back. A change that fails replay is a successful experiment, and the
  changelog entry for it is worth as much as the one for a merge.
