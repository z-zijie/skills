"""Backend contract.

A backend adapts one execution runtime to the evaluator. It is the only place in the framework
that may name a vendor, and it is loaded by name from the device description rather than
imported directly, so adding a device means adding a file, not editing the system.

A backend must provide:

``name``, ``capabilities``
    Identity and the capability tags the evaluator checks before using a feature
    (``"timing"``, ``"profiling"``, ``"contiguous_guards"``, ``"integer_bit_exact"``, …).

``available() -> (bool, reason)``
    Whether it can run here, with a reason when it cannot.

``zeros(n)``, ``from_list(values)``, ``tolist(array)``, ``fill(array, value)``
    Numeric buffers, contiguous, with a declared guard mode.

``sync()``, ``now()``
    Block until queued work finished; monotonic seconds. The harness syncs before and after
    every timed region and times only with ``now()``, so a backend whose kernels return before
    their work is done cannot fake a fast measurement.

``guards -> GuardSpec``
    ``"inline"`` means guards share the allocation with the buffer, so an indexing write past
    either end is detectable. ``"adjacent"`` means guards are separate allocations and
    detection is best-effort; the correctness report says which mode ran.

``prepare()`` (optional), ``close()`` (optional)
    One-time setup, and release of device resources.

Reference implementations shipped: ``cpu.py`` (always available), ``portable.py``
(array-oriented runtimes such as NumPy, CuPy, PyTorch, torch_npu, which work for both GPU and
NPU through the same code path).
"""
