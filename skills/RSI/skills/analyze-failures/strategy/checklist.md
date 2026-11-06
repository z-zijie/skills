# analyze-failures strategy

## The clusters, and what each one licenses

| Cluster | Mechanical rule | What it licenses | What it does not |
| --- | --- | --- | --- |
| `not_implemented` | verdict is `not_implemented` | A clearer card, a smaller scope, or a stronger implementer | Evidence against the mechanism, or a new rule |
| `large_prediction_error` | confirmed and `prediction_error > hole` | Narrowing a rule's scope, or a microbenchmark proposal | A playbook edit, since the tactic worked |
| `dead_end` | refuted twice for one operator | Moving the transformation down or out of the priority order | Deleting the rule: refutations are operator-scoped |
| `wrong_diagnosis` | confirmed and `significant` false | Fixing signature thresholds, or re-diagnosing | Adding negative evidence to the rule |
| `unexplained_speedup` | refuted and `significant` true | A follow-up experiment naming the real mechanism | Counting the win as evidence for the card as written |
| `correctness` | `correctness.passed` false | Nothing in the outer loop; the round never became evidence | Treating it as a performance failure |
| `implausible` | `implausible` true | A human review | Any automatic conclusion |

The order in the report is by count. The order worth acting on is by cost: how many
experiments, how much device time, how much of the campaign's budget each cluster consumed.
Both are visible from the entry list, so state which ordering you used.

## Reading a cluster

### large_prediction_error

The mechanism landed and the size was wrong. That is a hardware-model gap, not a tactic gap.

- Check whether the misses cluster on one transformation or on one signature tag. Misses on
  one transformation mean the rule's effect range is too wide; misses across many mean the
  measured layer is stale.
- Compare against the measured parameters' date. A hardware or compiler change invalidates
  every prediction whose scope crosses it, and the fix is calibration, not prose.
- Hand the experiment ids to `design-microbench` through the orchestrator. A microbenchmark
  proposal is the only thing that can close a hole in the model, and it needs human approval.

### wrong_diagnosis

The mechanism was confirmed, the counters moved, and the kernel did not get faster. In the
build plan's four-way table this is the most valuable outcome, because the tactic was fine and
the signature was wrong.

- Look at which tag the card was built on. If the mechanism moved its counter and time did not
  follow, that counter was not the limiter.
- A cluster of these across operators is evidence that a signature threshold is misplaced.
  Threshold changes are replay-gated and need calibration data, so propose, do not edit.
- Do not file the transformation as an anti-pattern. It may work under the signature it was
  meant for.

### not_implemented

The change never reached the machine code. There is nothing here about the mechanism.

- Two attempts on the same card usually mean the card asked for something the code cannot
  express, or named evidence the implementer cannot see.
- The fix lives in the card's `evidence_of_implementation` list. Vague evidence is the single
  most common cause, and it is cheap to fix without touching a playbook.

### dead_end

A transformation refuted twice for the same operator under the same signature.

- Scoped to the operator, not global: the same transformation may be established elsewhere.
  Record the operator in the summary so a later reader does not over-generalise.
- Dead ends are the cheapest thing to encode. Moving them out of the priority order costs one
  replay and saves every future attempt on that operator.

### unexplained_speedup

Faster, but not for the reason the card gave. The speedup is real; the explanation is missing.

- Mark it "unexplained" and queue a follow-up whose mechanism is the alternative explanation.
  Until then the win must not add evidence to the card's rule.
- Repeated unexplained speedups on one operator usually mean a second mechanism is active and
  the signature is not naming it.

## What makes a cluster worth acting on

Act on the top cluster when all four hold:

1. It has at least two experiments, so it is a pattern rather than one bad round.
2. Its members share a `tags` entry, so a playbook can be pointed at it.
3. The action it licenses is inside the outer loop's write boundary: playbooks, skill strategy,
   verifier checks that tighten, rule status.
4. Two independent scaffolds would plausibly differ on it. If the fix is mechanical, it belongs
   in a script, not in a strategy edit.

When the top cluster fails test 4, say so and hand the mechanical fix to `distill-rules` or to
a tool proposal instead of writing prose about it.
