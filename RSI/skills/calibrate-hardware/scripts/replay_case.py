#!/usr/bin/env python3
"""Offline regression driver for calibrate-hardware.

A case supplies a stored calibration report and, optionally, the previous measurement. The driver
runs the skill's own merge-and-derive path in dry-run mode and prints the facts the case asserts:
which parameters moved, which thresholds came out, what could not be measured, and whether the
device needs review.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibrate_hardware import calibrate  # noqa: E402


def main() -> int:
    case = json.load(sys.stdin)
    request = {"measured_file": case["measured_file"], "dry_run": True}
    if case.get("previous_file"):
        request["previous_file"] = case["previous_file"]
    if case.get("hw_id"):
        request["hw_id"] = case["hw_id"]
    outcome = calibrate(request)
    measured = outcome.get("measured") or {}
    thresholds = outcome.get("thresholds") or {}
    print(json.dumps({
        "status": outcome["status"],
        "hw_id": outcome["hw_id"],
        "moved": [item["parameter"] for item in outcome.get("moved") or []],
        "moved_count": len(outcome.get("moved") or []),
        "under_review": bool(measured.get("under_review")),
        "skipped_count": len(outcome.get("skipped") or []),
        "skipped_names": [item["name"] for item in outcome.get("skipped") or []],
        "all_skipped_have_reason": all(bool(item.get("reason")) for item in outcome.get("skipped") or []),
        "min_reportable_speedup": thresholds.get("min_reportable_speedup"),
        "uncoalesced_sectors": thresholds.get("uncoalesced_sectors"),
        "threshold_keys": sorted(key for key in thresholds if key in (
            "mem_saturated_pct", "compute_saturated_pct", "occupancy_low_pct",
            "uncoalesced_sectors", "min_reportable_speedup", "target_attainment_pct")),
        "peak_bandwidth_present": isinstance(measured.get("peak_bandwidth_gbps"), (int, float)),
        "problem_kinds": sorted({problem.split(":", 1)[0] for problem in outcome.get("problems") or []}),
        "notes": outcome.get("notes") or [],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
