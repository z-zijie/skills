"""Adapter for NVIDIA's Nsight Compute (``ncu``).

This is the one shipped adapter that targets a specific vendor profiler. It is deliberately
isolated: it is selected only when the device description asks for it or when ``ncu`` is on
PATH, it declares the canonical metrics it can produce, and everything downstream of it speaks
canonical names only.

Metric names differ between ``ncu`` versions. The map below is a starting point taken from the
build plan; ``--query-metrics`` output on the installed version is authoritative, so
``metric_map()`` records what was asked for and ``profile()`` records what came back missing.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ProfilerAdapter, register

#: Canonical metric -> ncu expression. Lists are summed.
_MAP: Dict[str, object] = {
    "device_mem_throughput_pct": "gpu__dram_throughput.avg.pct_of_peak_sustained_elapsed",
    "compute_throughput_pct": "sm__throughput.avg.pct_of_peak_sustained_elapsed",
    "scratchpad_throughput_pct": "l1tex__throughput.avg.pct_of_peak_sustained_active",
    "occupancy_pct": "sm__warps_active.avg.pct_of_peak_sustained_active",
    "stall_memory": "smsp__average_warps_issue_stalled_long_scoreboard_per_issue_active.ratio",
    "scratchpad_conflict_ratio": [
        "l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_ld.sum",
        "l1tex__data_bank_conflicts_pipe_lsu_mem_shared_op_st.sum",
    ],
    "registers_per_lane": "launch__registers_per_thread",
    "cache_hit_pct": "lts__t_sector_hit_rate.pct",
    "device_mem_bytes": "dram__bytes.sum",
    "spill_bytes": "memory_l2_theoretical_sectors_global_excessive.sum",
    "instruction_count": "inst_executed.sum",
}

#: Canonical metrics this profiler genuinely cannot produce, so the caller is not left guessing.
_STRUCTURALLY_ABSENT = ("branch_divergence_pct",)


class NcuAdapter(ProfilerAdapter):
    name = "ncu"
    requires = ("ncu",)

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.sections = self.config.get("sections", ["SpeedOfLight", "MemoryWorkloadAnalysis",
                                                     "Occupancy", "LaunchStats", "SchedulerStats"])
        self.timeout = int(self.config.get("timeout_s", 1800))

    def version(self) -> str:
        try:
            result = subprocess.run(["ncu", "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        for line in result.stdout.splitlines():
            if "Version" in line or "version" in line:
                return line.strip()
        return result.stdout.splitlines()[0].strip() if result.stdout else "unknown"

    def metric_map(self) -> Dict[str, Any]:
        return dict(_MAP)

    def profile(self, command: List[str], workdir: Any, out_dir: Any,
                env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        raw = out_path / "raw.ncu-rep.csv"
        metrics = [expr for value in _MAP.values() for expr in (value if isinstance(value, list) else [value])]
        invocation = [
            "ncu", "--csv", "--target-processes", "all",
            "--metrics", ",".join(metrics),
            "--force-overwrite", "-o", str(out_path / "raw"),
            *command,
        ]
        try:
            result = subprocess.run(invocation, cwd=str(workdir), capture_output=True, text=True,
                                    timeout=self.timeout, env={**os.environ, **(env or {})})
        except (OSError, subprocess.SubprocessError) as error:
            return {"metrics": {}, "unavailable": sorted(_MAP), "raw_report": "",
                    "adapter": self.name, "note": f"ncu failed to run: {error}"}
        raw.write_text(result.stdout, encoding="utf-8")
        values = parse_csv(result.stdout)
        canonical, unavailable = _canonicalise(values)
        return {
            "metrics": canonical,
            "unavailable": unavailable + list(_STRUCTURALLY_ABSENT),
            "raw_report": str(raw),
            "adapter": self.name,
            "adapter_version": self.version(),
            "transaction_granularity_bytes": 32,
            "note": "" if result.returncode == 0 else f"ncu exited {result.returncode}: {result.stderr[-400:]}",
        }


def parse_csv(text: str) -> Dict[str, Dict[str, float]]:
    """Read ``ncu --csv`` output into {kernel: {metric: value}}."""
    rows: Dict[str, Dict[str, float]] = {}
    reader = csv.reader(text.splitlines())
    header: Optional[List[str]] = None
    for row in reader:
        if not row or all(not cell.strip() for cell in row):
            continue
        if header is None:
            if "Metric Name" in row:
                header = [cell.strip() for cell in row]
                name_index = header.index("Metric Name")
                value_index = header.index("Metric Value") if "Metric Value" in header else 2
                kernel_index = header.index("Kernel Name") if "Kernel Name" in header else 0
                indices = (name_index, value_index, kernel_index)
            continue
        name_index, value_index, kernel_index = indices
        if len(row) <= max(name_index, value_index, kernel_index):
            continue
        kernel = _clean(row[kernel_index])
        metric = _clean(row[name_index])
        try:
            value = float(_clean(row[value_index]).replace(",", ""))
        except ValueError:
            continue
        rows.setdefault(kernel, {})[metric] = value
    return rows


def _clean(cell: str) -> str:
    return cell.strip().strip('"')


def _canonicalise(rows: Dict[str, Dict[str, float]]) -> Tuple[Dict[str, float], List[str]]:
    """Fold per-kernel metric rows into one canonical mapping, summing list expressions."""
    merged: Dict[str, float] = {}
    for metrics in rows.values():
        for name, value in metrics.items():
            merged[name] = merged.get(name, 0.0) + value
    canonical: Dict[str, float] = {}
    unavailable: List[str] = []
    for canonical_name, expression in _MAP.items():
        if isinstance(expression, list):
            present = [merged[expr] for expr in expression if expr in merged]
            if present:
                canonical[canonical_name] = sum(present)
            else:
                unavailable.append(canonical_name)
        elif expression in merged:
            canonical[canonical_name] = merged[expression]
        else:
            unavailable.append(canonical_name)
    return canonical, unavailable


def _factory(config: Dict[str, Any]) -> NcuAdapter:
    return NcuAdapter(config)


register("ncu", _factory)
