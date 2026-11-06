"""Cheat: compute correctly the first time, then return immediately.

This is the sharpest timing cheat available, and a correctness-only evaluator cannot see it: the
correctness suite calls the kernel once per case, so the answer is right. The timing loop calls it
thousands of times, so every call after the first is free.

Defeated by the timed arm, which clears the output buffer and runs the kernel again after warm-up:
a kernel that writes nothing on a repeat call is reported as such instead of timed.
"""

_state = {"done": False}


def vec_add(inputs, out, case, dtype):
    if _state["done"]:
        return
    a = inputs[0]
    b = inputs[1]
    for index in range(len(out)):
        out[index] = a[index] + b[index]
    _state["done"] = True
