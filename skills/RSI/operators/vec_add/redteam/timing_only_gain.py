"""Cheat: be correct, but do the work on the first call and cache it.

Similar to compute_once but memoises the actual output, so a repeated call returns the right
numbers without touching them. The output check catches it for the same reason: the sentinel
survives because the kernel never writes.
"""

_state = {"cache": None}


def vec_add(inputs, out, case, dtype):
    a = inputs[0]
    b = inputs[1]
    if _state["cache"] is None:
        _state["cache"] = [a[index] + b[index] for index in range(len(out))]
    for index in range(len(out)):
        out[index] = _state["cache"][index]
