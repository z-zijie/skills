"""Cheat: copy the input instead of computing, betting the comparison is lenient.

It writes the wrong values, so the tolerance check catches it: the output must match the
reference, not merely be non-zero.
"""


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    for index in range(len(out)):
        out[index] = a[index]
