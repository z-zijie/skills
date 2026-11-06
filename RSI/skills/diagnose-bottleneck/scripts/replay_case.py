#!/usr/bin/env python3
"""Offline regression driver for diagnose-bottleneck.

A case pins a profile, a spec, a measured snapshot and a timing number, then asserts the class,
the tag set and the primary bottleneck the discretization produced. Everything asserted here comes
out of ``rsi.signature``; the driver only prints it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose_bottleneck import diagnose  # noqa: E402


def main() -> int:
    case = json.load(sys.stdin)
    request = {
        "op": case.get("op", "vec_add"),
        "profile_file": case["profile_file"],
        "spec_file": case.get("spec_file", "skills/diagnose-bottleneck/tests/fixtures/spec.json"),
        "measured_file": case.get("measured_file",
                                  "skills/diagnose-bottleneck/tests/fixtures/measured.json"),
        "measured_us": case["measured_us"],
    }
    for key in ("extra_tags", "shape", "dtype"):
        if case.get(key) is not None:
            request[key] = case[key]
    outcome = diagnose(request)
    sig = outcome.get("signature") or {}
    roofline = sig.get("roofline") or {}
    print(json.dumps({
        "status": outcome["status"],
        "class": sig.get("class"),
        "tags": sig.get("tags"),
        "primary_bottleneck": sig.get("primary_bottleneck"),
        "attained_pct": round(float(roofline["attained_pct"]), 2) if roofline.get("attained_pct") is not None else None,
        "limiting_term": roofline.get("limiting_term"),
        "metrics_used": (outcome.get("based_on") or {}).get("metrics_used"),
        "thresholds_source": (outcome.get("based_on") or {}).get("thresholds_source"),
        "gap_points": round(float((outcome.get("gap") or {}).get("gap_points") or 0), 2),
        "at_target": (outcome.get("gap") or {}).get("at_target"),
        "note_count": len(outcome.get("notes") or []),
        "problems": outcome.get("problems"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
