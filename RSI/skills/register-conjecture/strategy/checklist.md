# register-conjecture strategy

## The three outcomes

The workflow names them, and the plan has to say what each means for this conjecture before the
experiment runs.

| Outcome | Condition | What happens to the card |
| --- | --- | --- |
| `promoted` | The experiment was valid and the expected observation appeared | Becomes a candidate rule with that one experiment as its confirmation |
| `refuted` | The experiment was valid and the observation did not appear | Moves to the anti-pattern store with the experiment as evidence |
| `open` | The experiment could not be made valid: `not_implemented`, correctness failed, or the counters were absent | Stays a conjecture, with the attempt recorded and a date |

`open` is not a failure of the workflow. It is the correct answer when the test did not
happen, and treating it as "probably true" is how an unverified idea becomes superstition.

## Sizing the experiment

Work down this list and stop at the first entry that can refute the claim.

1. **Already answered in the ledger.** Read the related experiments first. If one already shows
   the observation, the conjecture is promoted or refuted without new device time.
2. **An existing microbenchmark answers it.** Cache capacity, coalescing penalty, load width and
   launch overhead are already measured on this device, so the answer may be in
   `kb/hardware/measured/`. Cite the measurement; do not repeat it.
3. **A microbenchmark isolates it.** One mechanism, one sweep, three repeats. This is the
   cheapest new measurement and it answers the question for every operator, not just one.
4. **A campaign experiment is needed.** Only when the conjecture is about an interaction that no
   microbenchmark can isolate. Say why in the plan, because this is the expensive path.

A conjecture that requires more than three experiments is not one conjecture. Split it, and
register the piece that the first experiment can settle.

## Writing the observation

- Make it a comparison, not a hope: "sectors per request stays within 5% as load width doubles"
  can fail; "wide loads help" cannot.
- Name the canonical metric the observation is read from. If the observation is about timing, say
  which attainment or which speedup range, and remember the noise floor.
- State the direction and the size together. "Improves" without a size is a prediction that no
  result can contradict.
- Keep the observation inside what the profiler on this device actually reports. A conjecture
  whose observation names a metric the adapter returns as null can only ever be `open`.

## What a conjecture may and may not do

| May | May not |
| --- | --- |
| Inspire a hypothesis card, which then needs its own mechanism and predictions | Serve as the mechanism on that card |
| Be cited in a rationale as an open question | Enter a rule's `evidence` list |
| Be retrieved into a context, labelled unverified | Be used by the verifier to justify a verdict |
| Be promoted to `candidate` after one valid experiment | Be promoted to `established` on one operator |

## Common failures

| Symptom | Meaning | Fix |
| --- | --- | --- |
| Plan keeps growing past three experiments | The claim is really several claims | Split and register the first |
| `open` every time | The observation names a metric the device does not report, or the experiment is not implementable | Rewrite the observation in terms of a measured metric |
| Promoted, then immediately refuted | The plan used one shape or one operator, and the scope was written as universal | Write the scope at the size the plan tested |
| Conjecture cites a rule as if proven | The quarantine was ignored while writing the statement | State it as a claim, not as a consequence of the rule |

## After the plan runs

- Record which experiment answered it. A promoted conjecture becomes a rule card through
  `distill-rules`, and the workflow's evidence-count rules apply from there, not from here.
- A refuted conjecture is worth keeping as an anti-pattern, because the same idea will be
  proposed again by someone who did not see this exchange.
- An open conjecture gets a date and no status change. If it stays open past the staleness
  window, `audit-knowledge` flags it and a human decides whether to retire it.
