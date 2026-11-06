---
name: distill-rules
description: Turn a ledger window into rule-card create and update proposals with scope and evidence counts, and let the mechanical life cycle decide each status. Use as step 2 of W5, after analyze-failures has named the clusters, and whenever a campaign closes with evidence worth keeping.
---

# distill-rules

Step 2 of the outer loop. It reads confirmed and refuted experiments, groups them by
transformation and scope, and writes one rule card per group with its evidence list and its
status. The status is not a judgement: `rsi.knowledge.lifecycle` computes it from the evidence
counts, and this skill quotes the reason it returns.

It writes proposals only. Nothing lands in `kb/rules` until the gate in `docs/workflows.md`
accepts it.

## When it runs

- W5 step 2, on the same window `analyze-failures` clustered.
- At the end of a campaign, when its confirmed changes are new enough that no rule carries them.
- After a hardware or compiler change, to re-derive the status of every rule the window touches.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `entries` | Ledger entries, oldest first; only confirmed and refuted ones with a named transformation are evidence |
| `today` | ISO date written into `last_verified` |
| `next_rule_id` | First id to try; read the highest existing id and add one |
| `hardware_changed` | True when calibration moved a parameter the evidence depends on |
| `existing_rules` | Rule cards already stored, so the run can propose an update instead of a duplicate |

## Steps

1. Read the window from `ledger/experiments.jsonl` with `rsi.ledger.read_entries`. Done when
   every entry came from the ledger, so an `implausible` or `not_implemented` entry cannot be
   counted as evidence.
2. Run `python3 scripts/distill.py --input <file>`. Done when it prints one proposal set.
3. Check it against `contract/output.schema.json`. Done when validation reports no problems.
4. When a proposal's `action` is `update`, read `strategy/checklist.md` section "Updating an
   existing rule". The script matched the stored card, kept its id and unioned the evidence; the
   scope, the statement and the counter-example note are yours to re-read against that evidence.
5. Write one file per proposal under `kb/proposals/rules/`, named
   `<proposal-id>-<rule-id>.yaml`, carrying `kind`, `rationale`, `target`, `evidence`, `gate`
   and `status` exactly as `schemas/proposal.schema.json` defines them. Done when every file
   validates against that schema with `gate: mechanical` and `status: draft`.

## Output

`contract/output.schema.json`. Read these:

- `proposals` - each with a full rule card, the `lifecycle_reason` that fixed its status, the
  `operators` the evidence came from, and the `evidence` experiment ids.
- `skipped` - groups the run refused to turn into a rule, each with its reason. Quote it: a
  skipped group that keeps reappearing is a signal about the ledger, not about this run.
- `counts` and `lifecycle_thresholds` - so a reader can check the status without opening
  `tooling/rsi/knowledge.py`.

## Judging principles

- Quote the life cycle; never argue with it. If a card looks wrongly demoted, the inputs are
  wrong — the evidence list, the operator map, or `hardware_changed` — and the fix belongs
  there.
- Write every rule as "under condition C, transformation T moves metric M by delta". A
  statement that says "always do T" has lost its scope and is no longer a rule.
- Keep positive and negative cases together. Confirmation rate is confirmations over attempts
  where the mechanism was really implemented, so a refutation is part of the card, not a reason
  to leave it out.
- Bind to one hardware id. A rule that spans devices is not more general, it is untested, and
  the life cycle will demote it at the next calibration.
- Prefer a narrow rule with three confirmations to a wide one with three confirmations spread
  across incompatible scopes. Prediction error above the hole means the scope is too wide, and
  the life cycle already says so.
- Propose, then stop. The gate decides, and a rule that never merges costs nothing while a rule
  written directly into the store costs the trust in every other rule.
