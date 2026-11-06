---
name: audit-knowledge
description: Audit the knowledge stores for entries that conflict, are stale, or lack evidence, and propose demotion, retirement or merge. Use as step 3 of W5, after distill-rules has proposed new cards, and whenever calibration has moved a measured parameter.
---

# audit-knowledge

Step 3 of the outer loop. Distillation adds cards; this skill keeps the store from filling
with cards that cannot be trusted. Every finding is mechanical — a comparison of an entry
against the life cycle thresholds, the ledger window, the canonical metric registry or another
entry — so the same store always yields the same findings.

It proposes. It never demotes a card itself.

## When it runs

- W5 step 3, over the whole store, after `distill-rules` has run.
- After calibration, to find the rules a moved parameter invalidates.
- Before a replay, to make sure a proposed scaffold change is measured against a healthy store.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `rules`, `patterns`, `antipatterns`, `conjectures` | The store snapshot, read from `kb/` |
| `ledger_exp_ids` | Experiment ids the ledger window holds, used to check that evidence is real |
| `calibration` | `changed_parameters` flags every rule that depends on a moved measurement |
| `stale_after_days` | Default 90; the age at which `last_verified` stops being trusted |
| `today`, `next_proposal_id` | Dates the findings and numbering the proposals |

## Steps

1. Read each store with `rsi.knowledge.load_store` so the same loader sees the same files. Done
   when the snapshot's entry count matches the files under `kb/`.
2. Run `python3 scripts/audit.py --input <file>`. Done when it prints one report object.
3. Check it against `contract/output.schema.json`. Done when validation reports no problems.
4. For every finding of kind `conflict`, read `strategy/checklist.md` section "Conflicts".
   Deciding which of two conflicting rules survives is the only judgement here, and the script
   only names the better-evidenced one.
5. Write each proposal under `kb/proposals/audit/` as `<proposal-id>.yaml` carrying `kind`,
   `rationale`, `target`, `evidence`, `gate` and `status` per `schemas/proposal.schema.json`.
   Done when each validates and its `gate` matches the one the report gave it.

## Output

`contract/output.schema.json`. Read these:

- `findings` - one per problem, with `kind`, the `entry` it is about, a `detail` a reader can
  check, and the proposal it feeds.
- `proposals` - grouped by entry and action, each with its `gate`. A `mechanical` gate is a
  status change the life cycle already justifies; anything else needs replay or a human.
- `counts.by_kind` and `health` - the store's shape over time. Growing evidence per rule means
  knowledge is settling; a rising deprecated share means it is being retired as fast as it is
  written.
- `notes` - what the counts imply, phrased as a reading of the store.

## Judging principles

- Trust the life cycle over the stored status. A card marked established on one confirmation is
  wrong by the rules the system itself publishes, and saying so is the whole point of the audit.
- Separate "conflicting" from "complementary". Two rules over one scope with different
  transformations are what a playbook wants; two rules claiming opposite directions for one
  metric cannot both be retrieved.
- Prefer demotion to deletion. A deprecated card still explains why a route was abandoned, and
  an audit that erases history makes the same mistake available again.
- Treat missing evidence as missing, not as false. An evidence id outside the ledger window
  means the claim cannot be re-derived, so it drops to candidate and waits.
- Keep the mechanical and the judged apart. A stale date or a non-canonical metric is decided by
  the thresholds; a conflict is a judgement, and it must be argued in the proposal's rationale.
- Report a healthy store as healthy. Zero findings is a valid, useful answer.
