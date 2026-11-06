#!/usr/bin/env python3
"""Offline regression driver for collect-profile: the skill's own check path, compactly summarized.

A labelled case asserts a decision, not a payload. This driver runs the real reconciliation and
schema check on a stored profile and prints the handful of facts a case is about: which canonical
names came out, how many metrics are unknown to the registry, and whether anything blocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT  # noqa: E402
from collect_profile import finish  # noqa: E402


def main() -> int:
    request = json.load(sys.stdin)
    path = Path(str(request["profile_file"]))
    if not path.is_absolute() and not path.is_file():
        path = ROOT / path
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload.get("profile"), dict):
        payload = payload["profile"]
    outcome = finish(payload, ["replay", str(path)])
    profile = outcome["profile"] or {}
    metrics_out = profile.get("metrics") or {}
    if not isinstance(metrics_out, dict):
        metrics_out = {}
    unavailable = profile.get("unavailable") or []
    if not isinstance(unavailable, list):
        unavailable = []
    from rsi import metrics as metrics_mod

    print(json.dumps({
        "status": outcome["status"],
        "problems": outcome["problems"],
        "metric_names": sorted(metrics_out),
        "metric_count": len(metrics_out),
        "unavailable_count": len(unavailable),
        "unavailable_unknown": [name for name in unavailable if not metrics_mod.is_known(name)],
        "declared": list(profile.get("unavailable") or []) == unavailable,
        "legacy_rewrites": sum(1 for note in outcome["notes"] if note.startswith("legacy metric")),
        "null_fields_dropped": sum(1 for note in outcome["notes"] if "omitted rather than defaulted" in note),
        "no_metric_note": any("produced no canonical metric" in note for note in outcome["notes"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
