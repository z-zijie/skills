# revisit-closures strategy

Read the section the decision points at.

## The four decisions

| `decision` | Meaning | What the campaign does next |
| --- | --- | --- |
| `reopen` | At least one closure is open for a reason that is not evidence about the direction | Write the first reopen as the next card, from `reopens[0]` |
| `hold` | Closures stay open and each has used `MAX_REOPENS` | Continue, and report them as unresolved rather than refuted |
| `exhausted` | Every closure is admissible, swept and unconditional | The playbook really is exhausted; stopping is honest |
| `not_triggered` | No stop proposed, no flat streak, fewer than `min_open_closures` open | Nothing to do here; the campaign proceeeds normally |

A `stop` with `exhausted` is a claim that the search space has been covered. It is only worth
reporting when the closures behind it survive the two questions below.

## The two questions every closure has to answer

1. **Was it real?** Did the mechanism land in machine code and did its counters get measured?
   A compile failure, a link failure, wrong output, a hot path that was routed around, or a probe
   whose own baseline check failed all mean no. Those are `implementation`, and they say nothing
   about the direction.
2. **Was it swept?** How many points of the direction's parameter were tried? One point is a
   sample. The parameter is whatever the direction actually varies: block shape, tile width, split
   count, pass count, precision, layout — name it in `parameters` so the next card can widen it.

A closure is retirable when both answers are favourable: `premise`, at least `MIN_POINTS` points,
and no `reopen_when`.

## Reading each reopen

### fix_implementation

The artefact never worked on its own. The reopen is not a new idea; it is the same idea with the
failing piece isolated and asserted before anything is timed.

- Find what the failing artefact needs to pass alone: a minimal shape, a saved copy of a working
  binary, an isolated transfer, a disassembly diff against the parent. The audit that produced
  this skill found four dead hypotheses in one campaign that all died on a single 32-bit shift in
  a parameter word, and one refutation whose own baseline check failed on every run.
- Bank the isolation as a probe under the campaign's fast loop, not as a card: it is apparatus,
  and it should survive the campaign that needed it.

### widen_sweep

The mechanism landed, the counters were measured, they did not move — at one point of a range.
The reopen is a sweep with a prediction against the best point, not the first.

- Sweep the parameter the direction names, at the granularity the hardware allows. If the
  direction is a launch shape, that is every divisor that keeps one wave; if it is a tile width,
  every width that fits the on-chip buffer; if it is a pass count, every count the operand size
  permits.
- Predict `speedup.low` from the best point the sweep found, and state in the card which point
  that is. A card that predicts from the first point will be refuted for the same reason twice.

### bank_and_recheck

The mechanism was confirmed and the score did not follow. This is not a dead direction, it is a
direction whose share of case time is too small today.

- Keep the patch. Record the kernel-level gain and the operator-level non-gain together; that
  pair is the evidence that the kernel is not the limiter.
- Retest when the kernel's share of case time rises, which is usually after another kernel gets
  faster. State that condition in `reopen_when` so the closure is conditional rather than open
  forever.

### classify_then_retry

The verdict carries no checks, so the closure word is unsupported. The reopen re-runs the
verifier's groups on the archived artefact before another experiment is spent.

## What this skill does not do

- It does not propose new directions. A reopen is the same direction with the defect removed or
  the sweep widened; a genuinely new idea is `propose-hypothesis`'s job.
- It does not edit the ledger. The closure records it reads are written by `record-experiment`,
  and a missing record stays `unclassified` rather than being inferred from the verdict word.
- It does not lift a stop on its own. It reports `reopen` and the orchestrator runs the card; a
  human who has stopped the campaign is not overridden by a closure count.

## A worked example

This is the campaign the mechanism was designed against, classified by this skill's own script
(`docs/campaign-audit.md` has the full audit). Three promoted changes took the operator from 41.49
to 42.70, then six rounds were flat, and the planner proposed `stop` with `no_open_route`.

| direction | evidence | points | retirable |
| --- | --- | --- | --- |
| hand-written C-API cube pipeline | `implementation` | 1 | no: nine launches failed on one error code, then a parameter word was packed with a 32-bit shift |
| de-interleave the rope tile | `measurement` | 1 | no: the kernel verified at 1.58x and the operator score did not follow |
| head GEMM N split | `premise` | 1 | no: 128 heads already cover the array, and one width was tried |
| one-wave N slice on every path | `premise` | 1 | no: the sweep tested one width per projection |
| M stripe with ceil instead of floor | `premise` | 1 | no: the rounding rule was tested at one stripe count |
| fold three passes into one GEMM | `unclassified` | 1 | no: the ratio was withdrawn because the baseline mode never verified |
| fuse two projections into one launch | `unclassified` | 1 | no: the probe was invalid and its numbers were never used |

No closure is retirable, so the stop is not admissible: `decision` is `reopen`. The first card
fixes the C-API pipeline's generalisation in isolation — it is the cheapest check, and the report
had named that path as the largest remaining lever without ever measuring it. The second and third
sweep the two launch-geometry parameters that were tried at one width each.

The campaign's report said its remaining gap needed an algorithm-level rewrite. Under the closure
rules it says instead that seven closures were never settled, and names them. That difference is
the whole point: one sentence tells a human the ideas failed, the other tells them the attempts
did, and only the second one tells them what to spend the next budget on.
