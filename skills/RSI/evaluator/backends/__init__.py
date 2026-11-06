"""Execution backends: the only place that knows how to talk to an accelerator.

A backend is a thin adapter over one execution runtime (plain Python, an array runtime, a
vendor runtime). It knows how to allocate numeric buffers, move data, synchronise and read a
monotonic clock. Nothing above this layer branches on a vendor name: a kernel module receives
whatever backend the evaluator selected and uses only the documented methods.

See ``README.md`` for the contract a new backend must satisfy.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class GuardSpec:
    """How a backend detects writes outside a buffer."""

    def __init__(self, mode: str, guard_elements: int = 8, note: str = "") -> None:
        self.mode = mode
        self.guard_elements = guard_elements
        self.note = note

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "guard_elements": self.guard_elements, "note": self.note}


class BackendBase:
    name = "base"
    #: Capabilities the device offers. Microbenchmarks and adapters declare what they need.
    capabilities: Tuple[str, ...] = ("timing",)

    def available(self) -> Tuple[bool, str]:
        raise NotImplementedError

    def zeros(self, n: int) -> Any:
        raise NotImplementedError

    def from_list(self, values: List[float]) -> Any:
        raise NotImplementedError

    def tolist(self, array: Any) -> List[float]:
        raise NotImplementedError

    def fill(self, array: Any, value: float) -> None:
        raise NotImplementedError

    def sync(self) -> None:
        raise NotImplementedError

    def now(self) -> float:
        raise NotImplementedError

    @property
    def guards(self) -> GuardSpec:
        return GuardSpec("adjacent", 8, "guards are separate allocations; detection is best-effort")

    def device_description(self) -> Dict[str, Any]:
        return {"backend": self.name, "capabilities": list(self.capabilities)}

    def close(self) -> None:
        return None


_REGISTRY: Dict[str, Any] = {}


def register(name: str, factory) -> None:
    _REGISTRY[name] = factory


def names() -> List[str]:
    return sorted(_REGISTRY)


def create(name: str, config: Optional[Dict[str, Any]] = None) -> BackendBase:
    """Instantiate a backend by name, without checking availability."""
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown backend {name!r}; available: {', '.join(names())}. "
            "A backend is declared per device in kb/hardware/measured/device.yaml."
        )
    return _REGISTRY[name](config or {})


def select(preferred: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> BackendBase:
    """Pick a working backend: the preferred one first, then the portable one, then the reference."""
    config = config or {}
    order: List[str] = []
    for candidate in (preferred, config.get("backend"), "portable", "cpu"):
        if candidate and candidate not in order:
            order.append(str(candidate))
    reasons: List[str] = []
    for name in order:
        if name not in _REGISTRY:
            reasons.append(f"{name}: no such backend")
            continue
        backend = create(name, config)
        ok, reason = backend.available()
        if ok:
            return backend
        reasons.append(f"{name}: {reason}")
    raise RuntimeError("no execution backend is available: " + "; ".join(reasons))


# Import the shipped backends so the registry is populated on import.
from . import cpu as _cpu  # noqa: E402,F401
from . import portable as _portable  # noqa: E402,F401
