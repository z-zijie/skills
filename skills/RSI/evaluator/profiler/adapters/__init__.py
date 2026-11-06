"""Profiler adapters: canonical metric names in, one vendor profiler out.

An adapter declares, for each canonical metric, the expression its profiler needs, and gets
back a flat mapping of canonical name to value. Nothing above this layer sees a vendor metric
name, and a metric an adapter cannot produce is reported as unavailable rather than as zero.

Two rules from the build plan are encoded here, not in a skill:

* The expression map is data (``metric_map()``), because metric names differ between profiler
  versions. The authoritative names are whatever the installed profiler reports, so
  ``describe()`` records the adapter and version it ran against.
* Profiling and timing are separate runs. A profile's wall time is never performance data.

Ship with the ``null`` adapter: it produces a profile with every metric unavailable, which is
honest and keeps the rest of the pipeline exercisable on a machine with no profiler.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...tooling_path import metrics as metrics_mod


class ProfilerAdapter:
    """Base class: one installed profiler, mapped onto canonical metric names."""

    name = "base"
    #: Commands the adapter needs on PATH; a missing one makes the adapter unavailable.
    requires: Tuple[str, ...] = ()

    def available(self) -> Tuple[bool, str]:
        import shutil

        missing = [command for command in self.requires if shutil.which(command) is None]
        if missing:
            return False, f"missing executable(s): {', '.join(missing)}"
        return True, "available"

    def version(self) -> str:
        return "unknown"

    def metric_map(self) -> Dict[str, Any]:
        """Canonical metric -> profiler expression (a string, or a list to be summed)."""
        return {}

    def unavailable_reason(self, metric: str) -> str:
        return f"{self.name}: {metric} is not available from this profiler on this device"

    def profile(self, command: List[str], workdir: Any, out_dir: Any,
                env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Run a profiler over ``command`` and return canonical metrics plus the raw report.

        Returns ``{"metrics": {...}, "unavailable": [...], "raw_report": path, "adapter": ...}``.
        """
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        ok, reason = self.available()
        return {
            "adapter": self.name,
            "available": ok,
            "reason": reason,
            "version": self.version() if ok else None,
            "maps": len(self.metric_map()),
        }


class NullAdapter(ProfilerAdapter):
    """No profiler: report every canonical metric as unavailable.

    This is not a stub to be quietly replaced by made-up numbers. A device without a profiler
    adapter still gets signatures (they fall back to class and timing), but the hypothesizer
    is told which counters it may not rely on, and the verifier refuses to confirm a mechanism
    whose named counter is unavailable.
    """

    name = "null"

    def available(self) -> Tuple[bool, str]:
        return True, "no profiler on this device; every counter is reported unavailable"

    def profile(self, command: List[str], workdir: Any, out_dir: Any,
                env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return {
            "metrics": {},
            "unavailable": sorted(metrics_mod.METRICS),
            "raw_report": "",
            "adapter": self.name,
            "note": "the device declares no profiler; signatures use timing only",
        }


_REGISTRY: Dict[str, Any] = {}


def register(name: str, factory) -> None:
    _REGISTRY[name] = factory


def names() -> List[str]:
    return sorted(_REGISTRY)


def create(name: str, config: Optional[Dict[str, Any]] = None) -> ProfilerAdapter:
    if name not in _REGISTRY:
        raise KeyError(f"unknown profiler adapter {name!r}; available: {', '.join(names())}")
    return _REGISTRY[name](config or {})


def select(preferred: Optional[str] = None,
           config: Optional[Dict[str, Any]] = None) -> ProfilerAdapter:
    """Pick a profiler adapter: the device's declared one, then any that works, then ``null``."""
    order: List[str] = []
    for candidate in (preferred, "ncu", "msopprof", "null"):
        if candidate and candidate not in order:
            order.append(str(candidate))
    reasons: List[str] = []
    for name in order:
        if name not in _REGISTRY:
            continue
        adapter = create(name, config)
        ok, reason = adapter.available()
        if ok:
            return adapter
        reasons.append(f"{name}: {reason}")
    return NullAdapter()


from . import null as _null  # noqa: E402,F401
from . import ncu as _ncu  # noqa: E402,F401
from . import msopprof as _msopprof  # noqa: E402,F401
