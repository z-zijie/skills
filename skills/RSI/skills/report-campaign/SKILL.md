---
name: report-campaign
description: Produce the per-operator campaign report or the periodic system report from the ledger, with every figure computed from recorded experiments. Use when a campaign stops, at the end of W3, and on the weekly cadence for the system report.
---

# report-campaign

The report a campaign ends with and the report the system produces on a schedule. Neither
involves judgement about the numbers: the script computes them from ledger entries, so the
report is cheap, reproducible, and cannot drift from the ledger it describes.

What the skill adds is the reading: whether a campaign that stopped away from the bound needs an
algorithm-level rewrite, and which secondary metric explains a flat primary one.

## When it runs

- W3 step 6, when a campaign stops, for the per-operator report.
- On the weekly cadence, for the system report: the primary metric by scaffold version, the top
  failure types, the week's cost.
- When a human asks how a campaign is doing, which is the same computation on a shorter window.

## What you receive

One object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `report_kind` | `campaign` for one operator, `system` for a window |
| `entries` | Ledger entries; the only source of every rate and speedup |
| `op`, `attainment_pct`, `baseline_attainment_pct`, `target_attainment_pct`, `stop_reason` | Campaign headline |
| `open_routes` | What is left, for the campaign's closing section |
| `attainment_by_op`, `kb_summary` | System report context that is not in the ledger |
| `report_id`, `today` | Identity of the report |

## Steps

1. Read the ledger window with `rsi.ledger.read_entries` and filter it to the operator or the
   period. Done when the entry count in `generated_from.ledger_entries` matches the window.
2. Run `python3 scripts/report.py --input <file>`. Done when it prints one report object.
3. Check it against `contract/output.schema.json`. Done when validation reports no problems.
4. When `report.needs_algorithm_rewrite` is true, read `strategy/checklist.md` section "When the
   campaign stops far from the bound" before writing the closing paragraph. The report must say
   it plainly; a campaign that stopped at 62% and calls itself a success teaches the wrong lesson.
5. When the report is a system report and the primary metric did not rise, read
   `strategy/checklist.md` section "Reading a flat primary metric" and name the secondary metric
   that explains it.
6. Write the report where the campaign expects it and quote its `verdict` in the hand-off. Done
   when every claim in the prose has a figure or an experiment id behind it in the same file.

## Output

`contract/output.schema.json`. Read these:

- `report.headline` - attainment, baseline, gain and gap in points, total speedup, rounds, and
  the stop reason.
- `report.confirmed_changes`, `unexplained_observations`, `wrong_diagnoses` - the three lists a
  reader needs to judge the campaign, each citing experiment ids.
- `report.efficiency` - log speedup per experiment, experiments still needed for the target, mean
  prediction error.
- `report.needs_algorithm_rewrite` and `report.verdict` - the one-sentence conclusion.
- `report.by_scaffold_version` (system) - the primary metric per scaffold commit, which is the
  only way to answer whether the system is improving itself.
- `report.knowledge_health` (system) - established rules, evidence per rule, deprecated share.

## Judging principles

- Compute, do not estimate. Every figure in the report comes from the ledger through the script;
  a number typed from memory is a number nobody can reproduce.
- Report the campaign that stopped short as stopped short. The build plan is explicit: if the
  campaign ends far from the bound, the report says an algorithm-level rewrite is needed, and a
  human decides whether to open a new campaign.
- Name the unexplained. A speedup whose mechanism is unknown is the most interesting entry in the
  ledger, and a report that quietly files it under wins loses the only lead it had.
- Keep the three lists separate. Confirmed, unexplained and wrong-diagnosis experiments look
  alike in a speedup column and mean completely different things.
- Compare like with like across versions. Only rounds on the same operators with the same budget
  are comparable, so a version that ran three cheap operators is not evidence of an improvement.
- Let the report be boring. It is a reading of the ledger, not an argument for the campaign.
