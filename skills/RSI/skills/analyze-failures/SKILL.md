---
name: analyze-failures
description: Cluster a ledger window into failure patterns - not implemented, large prediction error, repeated dead ends, wrong diagnosis, unexplained speedup. Use as step 1 of W5, and before any scaffold edit, because a cluster is the only justification an edit is allowed to have.
---

# analyze-failures

Step 1 of the outer loop. It reads what the ledger already recorded and says which kinds of
failure are costing the most. A cluster is a count plus the experiment ids behind it; it is
not a theory about why the failure happened.

The clusters are the input `edit-scaffold` chooses from, so one cluster has to be pickable on
its own. That is why the script decides the cluster mechanically and writes its rule down.

## When it runs

- W5 step 1, every 30 to 50 experiments or at the end of a campaign.
- Before `edit-scaffold`, which may target only a cluster this run produced.
- On demand, over one operator, when a campaign report has to explain a poor return.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `entries` | Ledger entries, oldest first. Read-only; this skill appends nothing |
| `op` | Restricts to one operator; omit to cluster the whole window |
| `prediction_error_hole` | Defaults to the shared threshold in `tooling/rsi/knowledge.py` |
| `window` | Optional `since_exp` and operator filter, applied before clustering |

The ledger is the only evidence. Verifier notes are read as text and never override a verdict.

## Steps

1. Load the window with `rsi.ledger.read_entries` and filter it to `window` and `op`. Done when
   every entry in the window came from the ledger rather than from a report or a summary.
2. Run `python3 scripts/cluster.py --input <file>`. Done when it prints one report object.
3. Check the report against `contract/output.schema.json`. Done when validation reports no
   problems.
4. When `top_cluster` is `large_prediction_error` or `wrong_diagnosis`, read
   `strategy/checklist.md` under "Reading a cluster" before choosing what it means: those two
   point outside the playbooks, at the hardware model and at the signature thresholds.
5. Hand on `top_cluster`, the cluster's `tags` and its `experiments` unchanged. Done when the
   three are quoted from the report, because `edit-scaffold` caches them as its target.

## Output

`contract/output.schema.json`. Read these:

- `clusters` - ordered by count, each with `kind`, the `experiments` in it, the
  `transformations` and `tags` it involves, and a one-line `summary`.
- `top_cluster` - null when the window holds no failures, which is a good answer and not an
  empty one.
- `rates` - not-implemented rate, confirmation rate, mean prediction error, and the
  transformations already refuted twice for this operator.
- `unattributed` - failures with no signature tags. They cannot be routed to a playbook, so
  they are a diagnosis gap rather than a tactic gap.
- `notes` - what the counts imply for the next step, phrased as a reading rather than a change.

## Judging principles

- Count what the ledger recorded, not what the verifier notes suggest. An entry's `verdict`
  and `significant` are the facts; the notes are an opinion about them.
- Keep `not_implemented` separate from every other cluster. It measures the implementer and the
  clarity of the cards, and it is never evidence against a mechanism.
- Treat a refuted transformation as a dead end for that operator only after the second
  refutation, and keep the count visible. One refutation is a data point; two is a direction
  the playbook should stop offering.
- Rank by count and by cost, and say which one you used. A cluster of two experiments that
  burned half the budget outranks a cluster of five cheap misses, and the report should show
  the experiment ids so a reader can check that claim.
- Report a clean window as clean. A missing cluster is not a gap to fill with speculation.
