"""Adapter for on-device profiling on Ascend NPUs (``msopprof``).

Isolated like every other adapter: this file knows Ascend names, nothing else in the framework
does. The canonical metric names it produces are the same ones a GPU adapter produces, so a
signature, a rule or a playbook written against one device stays readable on the other.

Metric availability on Ascend is not identical to a GPU: there is no DRAM "sectors per
request" counter in the GPU sense, and bank conflicts and register pressure are derived from
profiler fields with different names. Anything derived is marked in ``DERIVED`` so a verifier
knows it was computed rather than measured, and anything genuinely absent is listed in
``unavailable`` instead of being reported as zero.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ProfilerAdapter, register

#: Canonical metric -> expression in msopprof's exported JSON.
_MAP: Dict[str, str] = {
    "device_mem_throughput_pct": "memory_unit_bandwidth_ratio",
    "compute_throughput_pct": "vector_cube_ratio",
    "scratchpad_throughput_pct": "ub_bandwidth_ratio",
    "occupancy_pct": "core_occupancy",
    "stall_memory": "mte2_stall_ratio",
    "stall_barrier": "sync_stall_ratio",
    "cache_hit_pct": "l2_hit_rate",
    "instruction_count": "instruction_count",
    "launch_overhead_us": "launch_overhead_us",
}

#: Metrics whose value is computed from several profiler fields rather than read directly.
DERIVED: Tuple[str, ...] = ("scratchpad_conflict_ratio", "sectors_per_request")

#: Canonical metrics this device/profiler combination does not report.
_ABSENT: Tuple[str, ...] = (
    "sectors_per_request", "scratchpad_conflict_ratio", "spill_bytes",
    "branch_divergence_pct", "registers_per_lane", "scratchpad_bytes_per_workgroup",
)


class MsopprofAdapter(ProfilerAdapter):
    name = "msopprof"
    requires = ("msopprof",)

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.timeout = int(self.config.get("timeout_s", 1800))
        self.kernel = self.config.get("kernel")

    def version(self) -> str:
        try:
            result = subprocess.run(["msopprof", "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else "unknown"

    def metric_map(self) -> Dict[str, Any]:
        return dict(_MAP)

    def profile(self, command: List[str], workdir: Any, out_dir: Any,
                env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        invocation = ["msopprof", "--output", str(out_path)] + list(command)
        try:
            result = subprocess.run(invocation, cwd=str(workdir), capture_output=True, text=True,
                                    timeout=self.timeout, env={**os.environ, **(env or {})})
        except (OSError, subprocess.SubprocessError) as error:
            return {"metrics": {}, "unavailable": sorted(set(_MAP) | set(_ABSENT)),
                    "raw_report": "", "adapter": self.name,
                    "note": f"msopprof failed to run: {error}"}
        raw = out_path / "raw.msopprof.json"
        payload = _find_payload(out_path)
        if payload is not None:
            raw.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        metrics = _canonicalise(payload or {})
        return {
            "metrics": metrics,
            "unavailable": sorted(set(_MAP) - set(metrics) | set(_ABSENT)),
            "raw_report": str(raw) if payload is not None else "",
            "adapter": self.name,
            "adapter_version": self.version(),
            "transaction_granularity_bytes": 32,
            "derived": list(DERIVED),
            "note": "" if result.returncode == 0 else f"msopprof exited {result.returncode}: {result.stderr[-400:]}",
        }


def _find_payload(root: Path) -> Optional[Dict[str, Any]]:
    """Locate the JSON summary msopprof writes, wherever the installed version puts it."""
    for pattern in ("**/metrics.json", "**/*summary*.json", "**/*.json"):
        for path in sorted(root.glob(pattern)):
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict):
                return data
    return None


def _canonicalise(payload: Dict[str, Any]) -> Dict[str, float]:
    """Read canonical metrics out of a profile JSON, tolerating nesting."""
    flat: Dict[str, float] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    flat.setdefault(str(key), float(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    metrics: Dict[str, float] = {}
    for canonical, expression in _MAP.items():
        if expression in flat:
            metrics[canonical] = flat[expression]
    return metrics


def _factory(config: Dict[str, Any]) -> MsopprofAdapter:
    return MsopprofAdapter(config)


register("msopprof", _factory)
