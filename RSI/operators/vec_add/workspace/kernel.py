"""The candidate under test.

This is where `implement-change` writes. Each experiment replaces this file with the parent
version plus exactly one transformation, so the diff against `../best/` is the experiment.
"""


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    b = inputs[1]
    n = len(out)
    for index in range(n):
        out[index] = a[index]
    for index in range(n):
        out[index] = out[index] + b[index]
