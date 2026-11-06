"""Reference backend: plain Python numeric buffers, runnable anywhere.

This is not a way to optimize an accelerator. It is the backend that makes the evaluator,
the red-team tests, the calibration suite and the whole scaffold exercisable on a machine with
no device, and it is what a device backend's behaviour is checked against.

It is also the one backend that can place canaries **inside** one allocation: a buffer is a
window onto a shared list, so a write past the end lands on a guard element instead of being
invisible. That is how the tests prove the evaluator catches out-of-bounds writes.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from . import BackendBase, GuardSpec, register

SENTINEL = 1.0e38


class GuardedBuffer:
    """A window onto one flat allocation, with guard elements on both sides.

    ``arr[i] = x`` writes into the shared list at ``offset + i``. A kernel that indexes past
    ``n`` corrupts the trailing guard instead of silently passing, which is exactly the case
    the correctness check is built to catch.
    """

    __slots__ = ("_store", "_offset", "_length", "_guard", "name")

    def __init__(self, store: List[float], offset: int, length: int, guard: int,
                 name: str = "") -> None:
        self._store = store
        self._offset = offset
        self._length = length
        self._guard = guard
        self.name = name

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index: int) -> float:
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(f"{self.name or 'buffer'}: index {index} outside 0..{self._length - 1}")
        return self._store[self._offset + index]

    def __setitem__(self, index: int, value: float) -> None:
        # Out-of-range writes are deliberately not clamped: writing into the guard region is
        # the detectable failure, and the guard check reports it.
        self._store[self._offset + index] = value

    def __iter__(self):
        for index in range(self._length):
            yield self._store[self._offset + index]

    def to_list(self) -> List[float]:
        start = self._offset
        return list(self._store[start:start + self._length])

    @property
    def guards(self) -> List[float]:
        start = self._offset - self._guard
        end = self._offset + self._length + self._guard
        return list(self._store[start:end])

    @property
    def guard_intact(self) -> bool:
        start = self._offset - self._guard
        end = self._offset + self._length + self._guard
        for position, value in enumerate(self._store[start:end]):
            if start + position in range(self._offset, self._offset + self._length):
                continue
            if value != SENTINEL:
                return False
        return True

    def __repr__(self) -> str:
        return f"GuardedBuffer(len={self._length}, guard={self._guard})"


class CpuBackend(BackendBase):
    name = "cpu"
    capabilities = ("timing", "contiguous_guards", "integer_bit_exact")

    def available(self) -> Tuple[bool, str]:
        return True, "reference backend: always available"

    def zeros(self, n: int) -> GuardedBuffer:
        guard = self.guards.guard_elements
        store = [SENTINEL] * guard + [0.0] * int(n) + [SENTINEL] * guard
        return GuardedBuffer(store, guard, int(n), guard)

    def from_list(self, values: List[float]) -> GuardedBuffer:
        buffer = self.zeros(len(values))
        store, offset = buffer._store, buffer._offset
        for index, value in enumerate(values):
            store[offset + index] = float(value)
        return buffer

    def tolist(self, array: GuardedBuffer) -> List[float]:
        return array.to_list()

    def fill(self, array: GuardedBuffer, value: float) -> None:
        store, offset, length = array._store, array._offset, array._length
        for index in range(length):
            store[offset + index] = float(value)

    def sync(self) -> None:
        return None

    def now(self) -> float:
        return time.perf_counter()

    @property
    def guards(self) -> GuardSpec:
        return GuardSpec("inline", 8,
                         "guards share the allocation with the buffer, so an indexing write "
                         "past either end is visible")

    def device_description(self) -> Dict[str, Any]:
        import platform

        return {
            "backend": self.name,
            "capabilities": list(self.capabilities),
            "host": f"{platform.system()}-{platform.machine()}",
            "note": "reference backend: numbers describe the host, not an accelerator",
        }


def _factory(config: Dict[str, Any]) -> CpuBackend:
    return CpuBackend()


register("cpu", _factory)
