"""Reference implementation. Read-only, and owned by the evaluator's process.

It runs before the candidate and its output is copied out, so a candidate cannot read the
answer it is supposed to produce. Shipped deliberately naive: on the reference backend it is
the same algorithm the baseline uses, so the roofline comparison starts honestly.
"""


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    b = inputs[1]
    for index in range(len(out)):
        out[index] = a[index] + b[index]
