"""Baseline kernel: the starting point every campaign improvement is measured against.

Deliberately plain, and deliberately correct. A campaign should be able to show a real gain
over it on a memory-bound operator — for example by removing the second pass over the input
or by making the traversal contiguous — and the mechanism verifier will check that the
counters moved rather than trusting the timing.
"""


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    b = inputs[1]
    # Two separate passes: one extra traversal of device memory that a fused loop removes.
    n = len(out)
    for index in range(n):
        out[index] = a[index]
    for index in range(n):
        out[index] = out[index] + b[index]
