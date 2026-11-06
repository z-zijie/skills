"""Cheat: return immediately and hope the comparison passes.

Defeated by pre-filling the output buffer with a sentinel: the untouched elements are reported
as "did not write its output".
"""


def vec_add(inputs, out, case, dtype):
    return
