#!/usr/bin/env python3
"""Offline regression driver for inspect-binary.

A case names a saved dump and an adapter; the driver runs the skill's own extraction and prints
the parts a verdict depends on: the instruction mix, the static pressure figures, the launch
geometry, and what the adapter could not read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inspect_binary import summarize  # noqa: E402


def main() -> int:
    case = json.load(sys.stdin)
    request = {
        "artifact": case.get("artifact", "build/vec_add.o"),
        "disassembly_file": case["disassembly_file"],
        "work_group": case.get("work_group", 64),
        "lane_count": case.get("lane_count", 1048576),
    }
    if case.get("adapter_file"):
        request["adapter_file"] = case["adapter_file"]
    if case.get("scratchpad_capacity_bytes"):
        request["scratchpad_capacity_bytes"] = case["scratchpad_capacity_bytes"]
    outcome = summarize(request)
    summary = outcome.get("summary") or {}
    mix = summary.get("instruction_mix") or {}
    print(json.dumps({
        "status": outcome["status"],
        "adapter": summary.get("adapter"),
        "instruction_total": summary.get("instruction_total"),
        "instruction_mix": mix,
        "wide_loads": mix.get("load_wide", 0),
        "compute_total": mix.get("compute", 0) + mix.get("fma", 0),
        "barriers": summary.get("barrier_count"),
        "registers_per_lane": summary.get("registers_per_lane"),
        "scratchpad_bytes_per_workgroup": summary.get("scratchpad_bytes_per_workgroup"),
        "spill_bytes": summary.get("spill_bytes"),
        "work_groups": (summary.get("launch") or {}).get("work_groups"),
        "launch_source": (summary.get("launch") or {}).get("source"),
        "missing": summary.get("missing") or [],
        "problem_kinds": sorted({problem.split(":", 1)[0] for problem in outcome.get("problems") or []}),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
