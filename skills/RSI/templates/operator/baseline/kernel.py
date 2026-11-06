"""Baseline: the starting point every campaign improvement is measured against.

Write it plainly, and write it correctly. A campaign should be able to show a real gain over it
on the right signature, and the mechanism verifier will check that the counters moved rather
than trusting the timing.
"""


def <operator-name>(inputs, out, case, dtype):
    source = inputs[0]
    for index in range(len(out)):
        out[index] = source[index]
