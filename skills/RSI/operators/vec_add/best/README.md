# best

The current best kernel for this operator. Only the evaluator promotes into this directory, and
only when a candidate's mechanism was confirmed *and* its speedup cleared the measured noise
floor. Every campaign round starts from here, so this file is what the next experiment is
measured against.

Empty on a fresh onboarding: `W1` records the baseline as experiment zero, and the first
confirmed and significant candidate lands here.

Copy `baseline/kernel.py` here to start from a working kernel.
