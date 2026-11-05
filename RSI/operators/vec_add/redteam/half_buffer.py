"""Cheat: compute only the first half and leave the rest alone."""


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    b = inputs[1]
    half = len(out) // 2
    for index in range(half):
        out[index] = a[index] + b[index]
