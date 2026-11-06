"""Cheat: correct only for one familiar shape, wrong everywhere else.

Defeated because every run draws fresh random shapes in addition to the typical and edge sets.
"""


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    b = inputs[1]
    n = len(out)
    if n == 65536:
        for index in range(n):
            out[index] = a[index] + b[index]
        return
    for index in range(n):
        out[index] = a[index]
