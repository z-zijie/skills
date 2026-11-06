"""Cheat: write one element past the end.

Defeated by the guard elements on both sides of the output buffer. On a backend whose guards
are separate allocations this is only best-effort, which is why the correctness report records
the guard mode it ran under.
"""


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    b = inputs[1]
    n = len(out)
    for index in range(n):
        out[index] = a[index] + b[index]
    out[n] = 1.0
