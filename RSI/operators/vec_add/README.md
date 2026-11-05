# vec_add

The shipped example operator. It exists so the whole framework is exercisable on any machine,
including one with no accelerator: the reference backend runs it, the calibration suite measures
whatever is underneath it, and every step of W1 and W2 closes.

Element-wise addition is the simplest memory-bound operator: three buffers of four bytes per
element, one add per element. The roofline bound is set by the bandwidth term on every device,
which makes it a good first operator to check the measurement machinery against — a kernel
cannot hide behind arithmetic here.

## Layout

| Path | Holds | Who writes |
| --- | --- | --- |
| `spec.yaml` | shapes, dtype, tolerance, byte and flop formulas | a human only |
| `reference/kernel.py` | the oracle | read-only |
| `baseline/kernel.py` | the starting kernel, deliberately naive: it makes two passes | read-only |
| `best/kernel.py` | the current best, promoted by the evaluator | the evaluator only |
| `workspace/kernel.py` | the candidate under test | inner-loop agents |
| `redteam/` | deliberately cheating kernels and their manifest | a human |

## Using it

```sh
tools/rsi-cli redteam --op vec_add           # the evaluator must catch all six cheats
tools/rsi-cli evaluate --op vec_add --candidate operators/vec_add/workspace/kernel.py
tools/rsi-cli campaign --op vec_add --budget 20
```

The baseline's second pass is a real, removable inefficiency: a candidate that fuses the two
loops removes one traversal of `device_mem`. That makes this operator a usable test of the whole
pipeline — a plausible hypothesis, a checkable mechanism, and a prediction the verifier can
compare against counters rather than against a number the implementer reported.
