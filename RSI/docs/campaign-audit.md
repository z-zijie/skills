# Audit: one real campaign against this scaffold

The closure mechanism in [`closures.md`](closures.md) was designed against a real campaign, not
from a design document. This page records what that campaign did, what it followed and what it
skipped, and which recorded refutations turned out not to be refutations — because a scaffold that
adds a check owes the evidence for it.

The campaign is anonymized to the figures that carry the argument: an accelerator operator
optimization run of one operator, driven by an agent under this framework's instructions, with its
own campaign ledger and its own probe loop. Nothing here is a measurement of the framework's
guarantees; it is an observation of how the framework was used.

## What the campaign ran

- Eleven ledger entries across seven agent turns: four confirmed and promoted, seven with a
  negative verdict.
- Three promoted changes brought the operator score from 41.49 to 42.70: a launch-geometry change
  worth about 5 %, a reduction-split change worth about 9 %, and a tile-width change worth about
  1 %. The fourth confirmed entry was a later capability milestone, not a speed change.
- A per-direction probe loop with its own timing protocol, a binary-level profiler capture, and a
  full operator harness for acceptance.
- A written campaign report with corrected claims, including two results it withdrew itself.

That is a serious campaign. The question this audit asks is not whether it did good work; it is
whether its records support the conclusions it drew from them.

## Adherence, by step of the single-experiment workflow

| Step | Followed | Not followed |
| --- | --- | --- |
| Diagnose | Per-kernel time attribution from the harness profiler; a hand-built equivalent of the dominant kernel; a pipe-level capture with the field's critic at 15/15 | No bottleneck signature document was ever written, so no entry could be retrieved by signature and no roofline attainment was computed |
| Retrieve | The framework's stores were read directly by hand and cited informally | No retrieval step, and no context file, so no rule id, anti-pattern id or status annotation reached a card |
| Hypothesize | Cards with mechanism, counters, predicted speedup and `evidence_of_implementation`, validated against the card schema | Cards were written after the probe, not before it; the predicted speedup was therefore a post-hoc number |
| Implement | Probe-first on every direction; smallest diff; a patch archived per experiment | Probe results and card predictions were sometimes decided together, which is exactly what the card's prior prediction exists to prevent |
| Evaluate | Full operator acceptance on all cases after a rebuild; the official harness was the only score | Correctness-gated runs and full 20-case runs in the right order, but with intermittent single-shape probes standing in for a screening gate |
| Verify | Verifier-shaped verdict documents with group A–D checks, validated against the verdict schema | Written by the implementer in eight of eight cases, after the score was known; the one verdict that needed a group C failure to be a refutation was recorded as a pass and the refutation moved to group D |
| Record | Append-only ledger lines with most of the schema's fields, prediction error, costs and per-case deltas, all validating against the ledger schema | No evaluator signature on any line, which the schema requires but no one checked; the scaffold commit was pasted in rather than read from the tree; three entries were later rewritten to correct their mechanism claims |
| Promote | Promotion rule applied honestly, including a revert of a confirmed-mechanism change because the score did not move | A capability milestone was recorded as `not_implemented` with a note explaining that the schema's `confirmed` asserts a promoted speed win |
| Plan | Re-profiles after flat rounds; measured ceilings per lever; a score model for each remaining lever | The plan decision was prose in a state file rather than the planner skill's output; the "no open route" conclusion rested on the campaign's own refutations |

Two of those omissions compound. Without a signature there is no roofline attainment, so the
planner's stop condition "target attainment reached" could never fire on its own terms; and
without retrieval the knowledge base never contributed a rule, so every tactic came from the
agent's own reasoning. The campaign still produced usable engineering. It did not produce
cumulative experience, which is the thing the scaffold exists to accumulate.

## The refutations, classified

Seven entries carried a negative verdict. Three of them have no archived verdict document, so the
classifier derived nothing from them and they stay `unclassified` rather than being guessed from
the verdict word. The four that do have a verdict document classify as follows:

| Class | Count | What it was |
| --- | --- | --- |
| `implementation` | 1 | a capability milestone recorded as `not_implemented` because its generalisation still mismatched, and whose 16×16×16 case verified bit-exact |
| `measurement` | 1 | a mechanism that verified at the kernel level with both counters moving as predicted, recorded as refuted because the operator score did not follow inside run-to-run drift |
| `premise`, one point | 3 | mechanisms that landed, were measured at a single width of a parameter, and did not move their counter |
| `premise`, swept | 0 | nothing in this campaign was refuted after sweeping a parameter |

No closure in the campaign is retirable, so none of its negative verdicts is evidence that its
direction does not pay. Two further observations matter more than the counts:

- **One direction was refuted while its probe's own baseline check was failing.** The comparison
  divided a verified kernel by an unverified one and reported a 2.14× gain; the campaign later
  withdrew the ratio after running the baseline mode under the same check. The withdrawal is in the
  entry's notes; the entry's verdict still reads `refuted`.
- **A direction recorded as refuted was later correct and 1.58× faster at the kernel level.** Its
  first probe failed because two API conventions were used wrongly — a length in bytes where the
  interface takes 32-byte units, and a stride in elements where it takes pairs of blocks. The
  defect was found by isolating the transfer, not by reading the code. Under the closure rules
  that first attempt would have been `implementation`, and the reopen would have named the
  isolation as its first check.
- **The direction the campaign named as its biggest remaining lever was never tried.** Its own
  report projected the largest single gain from porting a pipelined block matrix-multiply into the
  projection kernel, and that port never happened: the direction that would have enabled it stayed
  blocked on a link failure for nine attempts, then on a wrong parameter word, and the campaign
  closed with the lever listed rather than measured.

## What the campaign itself noticed, and what it cost

The campaign's own report contains the finding that motivates this page, written by the agent
that produced it: *"Four hypotheses died on this probe before I diffed against a saved binary and
found the failure predated my edits. The standing rule, learned twice now: run the check on the
baseline mode too, before quoting any ratio."*

It learned the rule and wrote it in prose. Nothing in the ledger made the next campaign apply it,
which is the difference between a lesson and a mechanism: a lesson is remembered by the agent that
learned it, a mechanism is applied to the agent that did not.

## What changed in the scaffold because of this audit

- Closures are recorded on every refutation and every not-implemented attempt, with the class
  derived from the verifier's checks ([`closures.md`](closures.md)).
- A refutation that never landed, that landed without moving the score, or that tried one point of
  a parameter no longer retires a direction: `analyze-failures` clusters it as `open_closure`, and
  `revisit-closures` reopens it before a stop is reported.
- The state machine re-checks closures once before a `stop` or `stop_plateau` is written, and
  reports `hold` closures as unresolved rather than exhausted.

The reverse direction is deliberately untouched: a swept, admissible refutation retires a
direction exactly as it did before, and the campaign is allowed to stop when every closure
survives the two questions. The check is meant to be cheap for a campaign that did the work and
expensive only for one that did not.
