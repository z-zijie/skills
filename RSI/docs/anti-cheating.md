# Anti-cheating

The cautionary tale this design is built against: a system found exploits in the evaluation
code that bypassed correctness checks, and its published speedups were walked back. That is the
worst failure mode available here — worse than not improving, because it is not improving while
appearing to.

So the checklist is a table of attacks with the defence that catches each one, and the
defences are code in `evaluator/`, not instructions in a prompt.

| Way around the evaluator | Defence | Where |
| --- | --- | --- |
| Reuse output memory left by the reference | The reference runs first, its output is copied out, and the candidate's buffer is pre-filled with a sentinel | `evaluator/oracle/correctness.py` |
| Specialise for fixed test shapes | Fresh random shapes every run, plus typical and edge shapes | `spec.cases` |
| Cache the previous result | Random inputs every run, from a seed the evaluator keeps | `evaluator/oracle/correctness.py` |
| Edit evaluation code, tolerances or thresholds | Tolerances live in the operator spec, outside the candidate's reach; the evaluator's source hash is recorded in every result | `schemas/operator-spec.schema.json`, `evaluator/cli.py` |
| Return before asynchronous work finishes | Sync before and after every timed region, and time only the kernel | `evaluator/harness/timing.py` |
| Move computation outside the timed region | Setup is timed separately and capped by the spec | `timing_report` |
| Lower precision or skip part of the computation | Tolerances fixed in the spec; random shapes exercise every path | `spec.compare` |
| Forge a result file | The evaluator signs results; the ledger accepts nothing unsigned | `tooling/rsi/ledger.py` |
| A single step that is implausibly large | Any step at or above the review cap pauses for human review | `evaluator/cli.py`, spec's `speedup_review_cap` |

## The red-team requirement

Before the system goes live on an operator, hand-write kernels that cheat on purpose and confirm
the evaluator catches every one:

```sh
tools/rsi-cli redteam --op <op>
```

`operators/vec_add/redteam/` ships six: a kernel that writes nothing, one that copies an input
instead of computing, one that covers half the output, one that writes past the end, one that
drops precision, and one that is correct only for the shape it was written for. They are caught
by the sentinel, the guard elements, the tolerance comparison and the random shapes respectively.

A cheat that passes is a hole in Layer 0, and every piece of experience built on top of it is
noise. It is also the cheapest test in the system, which makes skipping it indefensible.

## The noise problem, which is the same problem

A measurement system that reports 1 % gains that appear and vanish is not lying about any single
number; it is failing to distinguish signal from noise, and the effect is the same. The
defences are measured rather than assumed:

- An **A/A test** measures the same kernel as if it were two candidates. The "difference" is the
  noise floor for this device, in this environment, today.
- **Interleaved timing** cancels thermal and frequency drift: drift that slows both arms equally
  cannot become a speedup.
- **A bootstrap interval on the speedup**, and the win rule is the interval's lower bound
  clearing the measured noise floor. A single sample cannot win, and neither can a wide interval.
- **Environment fingerprints** on every measurement, so a result from before a runtime upgrade is
  visibly from a different environment rather than quietly averaged with today's.

The noise floor is re-measured when the environment changes, and it is stored in
`kb/hardware/measured/` next to the peaks. A device whose noise floor is 5 % needs a bigger
margin before any rule fires than one whose floor is 0.5 %, and hardcoding one number for both
is how a system starts believing its own noise.

## The one backstop rule

Whenever a result looks too good to be true, suspect the evaluator first, the hardware model
second, and believe the agent last.
