# Verifier checklist

Four groups. Group A asks whether the change exists, B whether it reached machine code, C
whether the counters moved, D whether it is one of the superficial forms this project has
already been fooled by. Every item gets a conclusion; the verdict follows from them, not from an
impression of the diff.

A check id is `<group><number>`. Cite it in `verdict.json` so a later audit can tell which check
was weak when a verdict turns out wrong.

## A. Does the change exist?

**A1.** The diff is non-empty and touches the kernel's hot path — the loop or the work
distribution — rather than comments, names, formatting or host-side setup code.

**A2.** The change matches `scope_of_change` on the card and carries no extra edits. One
experiment changes one variable; a second unrelated edit makes the result unattributable.

**A3.** The new path really executes. No preprocessor branch, conditional compilation, feature
flag or runtime branch routes around it for the shapes and dtypes the evaluator uses.

**A4.** Compiler flags are unchanged, unless changing them is the hypothesis itself. A flag that
silently changed is a different experiment.

**A5.** The input buffers and output buffer in the new path are the ones the harness times. Work
performed on a copy that the harness does not read is not this experiment.

## B. Did the change reach machine code?

**B1.** The disassembly shows the matching change. A claimed wide load shows wider loads; a
claimed fused multiply-add shows that instruction; claimed scratchpad use shows accesses to it;
a claimed unroll shows fewer loop back-edges.

**B2.** The disassembly before the change did not already have that feature. If the compiler was
already doing the same optimization, the source change is void and the verdict is
`not_implemented` — the transformation was already in the machine code.

**B3.** Register use per lane matches what the card predicts, and did not exceed the point where
occupancy collapses unless occupancy collapse was the hypothesis.

**B4.** Scratchpad bytes per work-group and the launch configuration (work-group size, grid
size) match the card, or the card did not name them and they are unchanged.

**B5.** No spill to local memory appeared that was not on the card. A spill can look like a gain
in one counter while costing more in another.

## C. Did the counters move as predicted?

**C1.** Every counter named in `predictions.counters` moved in the predicted direction.

**C2.** Each of those moves exceeded that counter's own measured run-to-run variation, taken from
`kb/hardware/measured/<hw_id>.yaml`. A move inside the jitter is not a move.

**C3.** The predicted magnitude was reached where the card stated one, for example
`sectors_per_request <= 1.5`.

**C4.** Counters the card did not name did not get clearly worse. A gain in one place paid for by
a bigger loss elsewhere is not this mechanism.

**C5.** A counter listed in `kb/hardware/documented/profiler-metric-pitfalls.md` as unreliable on
this device was cross-checked with the alternative that entry names, and the cross-check agrees.
This one is `unknown` when there is no alternative.

## D. Typical forms of superficial implementation

This group grows through W6: every correction a human makes becomes a check here, after it is
replayed over ledger history. Starting entries:

**D1.** The change covers only some code paths or some shapes — for example the aligned branch
but not the tail loop.

**D2.** A scratchpad buffer was added, but the data is still read repeatedly from device memory.
Adding the buffer is not the transformation; removing the repeated reads is.

**D3.** A loop was "unrolled" that the compiler had already unrolled. B2 catches the machine-code
half of this; D3 catches the source-level claim.

**D4.** The work-group size changed but the grid computation did not follow, so the real
parallelism is unchanged.

**D5.** The code is written differently but the access pattern is the same — AoS converted to
SoA with an index that still strides, or a tiling scheme whose tile is the whole problem.

**D6.** Work moved to the host or outside the timed region, and the kernel itself is no faster.
The evaluator's setup cap is the mechanical defence; this check catches the intent.

**D7.** Precision was quietly lowered, or part of the computation was skipped, and the measured
tolerance happened to survive. Numerically equal for the sampled inputs is not the same as
computing the same function.

**D8.** The change is only correct at the shapes the implementer ran. The correctness suite draws
fresh random shapes every run; a change that specialises for the shapes it saw fails there, and
this check is where that failure is recorded as a mechanism problem rather than a timing one.

## The four combinations

| Mechanism | Speed | Meaning | Action |
| --- | --- | --- | --- |
| Confirmed | Faster | The hypothesis holds | Promote; positive evidence for the rule |
| Confirmed | Not faster | The mechanism worked but was not the real bottleneck | The most valuable case: the diagnosis was wrong, so fix the signature thresholds or the playbook |
| Refuted | Faster | It got faster, but not for the reason the card gave | Mark as an unexplained speedup; run follow-up experiments before counting it as evidence |
| Refuted | Not faster | The hypothesis was wrong | Negative evidence for the rule, or file it as an anti-pattern |

## Auditing this checklist

The verifier makes mistakes too. From every 50 experiments, sample 5 for human review and track
how often the verdict agrees. When agreement falls, add checks through W6 before reaching for a
stronger model: a missing check is a specification problem, and a stronger model only guesses
better at an unspecified task.
