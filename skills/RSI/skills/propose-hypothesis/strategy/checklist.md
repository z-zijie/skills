# propose-hypothesis: how a card earns its place

A card is an experiment that can fail. Everything in it is there so the verifier can say "this did
not happen", which is why a vague card is worse than no card: it costs a round and produces a
`not_implemented` that teaches nothing.

## The five things a card must decide

| Part | The question it answers |
| --- | --- |
| `mechanism` | What does the machine do now, what would it do instead, and why does that move a counter? One sentence, no tool names. |
| `predictions.counters` | Which canonical metrics move, in which direction, and to what bound? |
| `predictions.speedup` | What gain does the mechanism buy, as `expect`, `low`, `high`? |
| `evidence_of_implementation` | What in the diff and the disassembly would prove the change landed? |
| `scope_of_change` | What is *not* being changed, so one variable moves per experiment? |

A card with all five is checkable. A card missing the third is a wish; a card missing the fourth
cannot be verified at all.

## Choosing counters

Name counters the device produces. `unavailable_metrics` lists what it cannot, and a card naming one
of those is rejected here rather than at verification time, because a mechanism nobody can observe
cannot be confirmed or refuted and the round is wasted either way.

Prefer two counters to one when the mechanism implies both. A widening change should move
`sectors_per_request` down and `device_mem_throughput_pct` up at the same speed; if only one moves,
the mechanism probably did not happen.

Prefer a counter with a documented reliability over one without. `scratchpad_conflict_ratio` is
known to mislead on some devices, so a conflict claim needs a second observable.

## Predicting a speedup

The bound is arithmetic, not intuition:

1. `attained_pct` says how far the kernel is from its roofline bound.
2. The mechanism removes a fraction of the gap. Removing all of a stall that occupies 40% of issue
   slots is the ceiling, and no change removes all of it.
3. `low` must clear the device's measured noise floor, or the round cannot produce a significant
   result even if the mechanism works.

When `noise_threshold` is unknown, predict a range and say the floor is unmeasured. A number
invented to look decisive becomes a prediction error that pollutes the rule it was supposed to
support.

## What the retrieved context is for

`context.md` exists so a card can be grounded: the rule that already worked, the pattern with the
mechanism spelled out, the anti-pattern that shows the idea already failed here. Cite what was used
in `based_on`. A card that cites nothing is allowed and is flagged, because "this rests on nothing"
is information the campaign needs.

Anti-patterns matter more than rules for the second and third card of a round: after the obvious
idea, the next most likely suggestion is usually one that has already been refuted.

## Ranking

The gate orders the cards by the lower bound of their predicted speedup, then by how many entries
they cite, then by how many counters give the verifier something to see. Ties keep the model's
order.

The lower bound comes first because each experiment costs a round, and the lower bound is the payoff
the card is willing to stand behind. A card that predicts 1.30 with a floor of 1.12 is worth more
than one that predicts 1.40 with a floor of 1.01, because the second cannot clear the noise floor.

A card whose floor sits inside the noise floor is flagged in `notes` rather than dropped: sometimes
a small, certain gain is exactly what is needed, and the flag lets a human decide that.

## Rejection reasons are output

A rejected card leaves with the reason attached: an unregistered metric, an unavailable metric, or a
contract violation. That list is what `capture-correction` and `distill-rules` read when a class of
idea keeps being proposed and keeps being unusable.

## What this skill never does

- It never edits the code. That is `implement-change`.
- It never sees the timing result of a previous round, only the signature, the context and the code.
- It never proposes two variables in one card, even when both changes are obviously good.
