# Documentation

| File | Answers |
| --- | --- |
| [`vocabulary.md`](vocabulary.md) | What every canonical name means: memory levels, metrics, signature tags, work units, hardware identity. Read this first; every other file assumes it. |
| [`contracts.md`](contracts.md) | Where things live, which data structure is produced by which skill, and the layout of one skill. The page a skill author needs. |
| [`AUTHORING.md`](AUTHORING.md) | The binding brief for writing a skill, playbook or knowledge-base entry in this framework. |
| [`workflows.md`](workflows.md) | The seven workflows (W1–W7), the state machine they correspond to, and what a human gates. |
| [`permissions.md`](permissions.md) | Who may write what, how it is enforced, and what the guarantee honestly is. |
| [`anti-cheating.md`](anti-cheating.md) | Every way around the evaluator, the defence that catches it, and where that defence lives in code. |
| [`metrics.md`](metrics.md) | The system metrics, why efficiency is the primary one, and where each is computed. |
| [`moving-to-a-new-device.md`](moving-to-a-new-device.md) | The six-step porting checklist: describe, measure, map the profiler, seed the documented layer, re-tune thresholds, re-baseline. |
| [`roadmap.md`](roadmap.md) | The five phases, their gates, and what this repository has and has not built. |
| [`QUICKSTART.md`](QUICKSTART.md) | From a bare clone to a working loop, in the order that matters. |

## Suggested reading order

**Running it:** QUICKSTART → workflows (W3) → anti-cheating.
**Porting it:** vocabulary → moving-to-a-new-device → QUICKSTART.
**Changing it:** vocabulary → contracts → AUTHORING → permissions → roadmap (what the gates are).
**Deciding whether to trust it:** anti-cheating → metrics → roadmap.
