"""Device operations the calibration suite measures.

A microbenchmark must time work on the device, not travel across a bus. So instead of writing
a separate measurement kernel per vendor, the suite calls a small set of operations that each
backend implements natively: vectorised array ops when an array runtime is present, plain
loops on the reference backend.

Two implementations share one interface:

``ReferenceOps``
    Python-level loops over the reference backend's storage. Slow, exact, runnable anywhere.

``ArrayOps``
    Whatever array module the portable backend drives (NumPy, CuPy, PyTorch, torch_npu). The
    same call becomes a real device kernel on a GPU or an NPU, so the numbers the calibration
    suite records describe the device that will run the operator.

Nothing here names a vendor. The suite asks for "a fused multiply-add over N elements" and the
backend decides what that is on this machine.
"""

from __future__ import annotations

from typing import Any, List, Optional


class OpsBase:
    """The operation set the microbenchmark suite needs."""

    kind = "base"

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    # -- primitives -------------------------------------------------------
    def copy(self, source: Any, destination: Any) -> None:
        raise NotImplementedError

    def add(self, left: Any, right: Any, out: Any) -> None:
        raise NotImplementedError

    def mul_add(self, a: Any, b: Any, c: Any, out: Any) -> None:
        """``out = a * b + c``: one fused multiply-add per element where the device has FMA."""
        raise NotImplementedError

    def reduce_sum(self, source: Any) -> float:
        raise NotImplementedError

    def strided_read(self, source: Any, stride: int, out: Any) -> None:
        """Read every ``stride``-th element: the coalescing penalty microbenchmark."""
        raise NotImplementedError

    def launch_empty(self) -> None:
        raise NotImplementedError

    def barrier_loop(self, buffers: List[Any], barrier_every: int) -> None:
        """A scratchpad-style loop that synchronises every ``barrier_every`` iterations."""
        raise NotImplementedError

    def atomic_add(self, accumulators: Any, source: Any, buckets: int) -> None:
        """Scatter-add into a small accumulator array: the atomics microbenchmark."""
        raise NotImplementedError

    # -- description ------------------------------------------------------
    def describe(self) -> dict:
        return {"kind": self.kind}


class ReferenceOps(OpsBase):
    """Python-level loops: exact, portable, and honest about being slow."""

    kind = "reference-loops"

    def copy(self, source: Any, destination: Any) -> None:
        store_s, offset_s = source._store, source._offset
        store_d, offset_d, length = destination._store, destination._offset, destination._length
        for index in range(length):
            store_d[offset_d + index] = store_s[offset_s + index]

    def add(self, left: Any, right: Any, out: Any) -> None:
        store_l, off_l = left._store, left._offset
        store_r, off_r = right._store, right._offset
        store_o, off_o, length = out._store, out._offset, out._length
        for index in range(length):
            store_o[off_o + index] = store_l[off_l + index] + store_r[off_r + index]

    def mul_add(self, a: Any, b: Any, c: Any, out: Any) -> None:
        store_a, off_a = a._store, a._offset
        store_b, off_b = b._store, b._offset
        store_c, off_c = c._store, c._offset
        store_o, off_o, length = out._store, out._offset, out._length
        for index in range(length):
            store_o[off_o + index] = store_a[off_a + index] * store_b[off_b + index] + store_c[off_c + index]

    def reduce_sum(self, source: Any) -> float:
        store, offset, length = source._store, source._offset, source._length
        total = 0.0
        for index in range(length):
            total += store[offset + index]
        return total

    def strided_read(self, source: Any, stride: int, out: Any) -> None:
        store_s, off_s, length_s = source._store, source._offset, source._length
        store_o, off_o, length_o = out._store, out._offset, out._length
        step = max(1, int(stride))
        written = 0
        position = 0
        while written < length_o and position < length_s:
            store_o[off_o + written] = store_s[off_s + position]
            written += 1
            position += step

    def launch_empty(self) -> None:
        return None

    def barrier_loop(self, buffers: List[Any], barrier_every: int) -> None:
        for buffer in buffers:
            self.backend.fill(buffer, 1.0)
            for index in range(0, len(buffer), max(1, int(barrier_every))):
                buffer[index] = float(index % 7)

    def atomic_add(self, accumulators: Any, source: Any, buckets: int) -> None:
        store_a, off_a = accumulators._store, accumulators._offset
        store_s, off_s, length_s = source._store, source._offset, source._length
        modulus = max(1, int(buckets))
        for index in range(length_s):
            bucket = index % modulus
            store_a[off_a + bucket] += store_s[off_s + index]


class ArrayOps(OpsBase):
    """Array-module operations: a real device kernel on whatever runtime the backend drives.

    One interface, two spellings. A torch-like runtime mutates in place and takes ``out=``; a
    NumPy-like runtime assigns through a slice or a ufunc. Every branch is explicit because a
    silently no-op operation is not a slow measurement, it is a fabricated one: a copy that does
    nothing reports an infinite bandwidth, and the roofline then computes an attainment that
    looks like a bug in the kernel.
    """

    kind = "array-module"

    def __init__(self, backend: Any, module: Any) -> None:
        super().__init__(backend)
        self.module = module
        self._torch = getattr(module, "__name__", "").startswith("torch")

    # -- moves ------------------------------------------------------------
    def copy(self, source: Any, destination: Any) -> None:
        if hasattr(destination, "copy_"):
            destination.copy_(source)
            return
        if hasattr(destination, "__setitem__") and hasattr(destination, "__len__"):
            destination[:] = source
            return
        raise TypeError(
            f"cannot copy into {type(destination).__name__}: the operation set needs an array "
            "type with in-place copy or slice assignment"
        )

    def add(self, left: Any, right: Any, out: Any) -> None:
        if self._torch:
            self.module.add(left, right, out=out)
        else:
            self.module.add(left, right, out=out)

    def mul_add(self, a: Any, b: Any, c: Any, out: Any) -> None:
        if self._torch:
            self.module.addcmul(c, a, b, out=out)
        else:
            self.module.multiply(a, b, out=out)
            self.module.add(out, c, out=out)

    def reduce_sum(self, source: Any) -> float:
        value = source.sum()
        return float(value.item()) if hasattr(value, "item") else float(value)

    def strided_read(self, source: Any, stride: int, out: Any) -> None:
        step = max(1, int(stride))
        count = len(out)
        out[:] = source[::step][:count]

    def launch_empty(self) -> None:
        return None

    def barrier_loop(self, buffers: List[Any], barrier_every: int) -> None:
        for buffer in buffers:
            if hasattr(buffer, "fill_"):
                buffer.fill_(1.0)
            else:
                buffer[:] = 1.0
            stride = max(1, int(barrier_every))
            if hasattr(buffer, "fill_"):
                buffer[::stride] = 0.5
            else:
                buffer[::stride] = 0.5

    def atomic_add(self, accumulators: Any, source: Any, buckets: int) -> None:
        modulus = max(1, int(buckets))
        index = self.module.arange(len(source)) % modulus
        if self._torch:
            self.module.index_add_(0, index, source)
        else:
            self.module.add.at(accumulators, index, source)


def for_backend(backend: Any) -> OpsBase:
    """The operation set that suits a backend."""
    module = getattr(backend, "_module", None)
    if module is not None and getattr(backend, "name", "") == "portable":
        return ArrayOps(backend, module)
    return ReferenceOps(backend)
