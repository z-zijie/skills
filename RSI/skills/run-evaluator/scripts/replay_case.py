#!/usr/bin/env python3
"""Offline regression driver: the same checks the skill runs, reduced to a compact verdict.

`skills.replay_tests` compares one JSON object in to one JSON object out, so a full signed
result would drown a case in boilerplate. This driver calls the real check functions and prints
only the decision they produced, which is what a labelled regression case is about.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_evaluator import classify, finish, split_schema_errors  # noqa: E402

from _shared import ROOT  # noqa: E402


def load(path: str) -> dict:
    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = ROOT / path
    return json.loads(candidate.read_text(encoding="utf-8"))


def main() -> int:
    request = json.load(sys.stdin)
    result = load(str(request["result_file"]))
    if isinstance(result.get("result"), dict):
        result = result["result"]
    key = request.get("signing_key")
    outcome = finish(result, ["replay"], key=key.encode("utf-8") if key else None)
    split = split_schema_errors(result)
    print(json.dumps({
        "status": outcome["status"],
        "failed_checks": sorted({problem.split(":", 1)[0] for problem in outcome["problems"]}),
        "blocking_schema_errors": len(split["blocking"]),
        "drift_keys": len(split["drift"]),
        "correctness_passed": bool((result.get("correctness") or {}).get("passed")),
        "significant": (result.get("timing") or {}).get("significant"),
        "implausible": bool((result.get("guards") or {}).get("implausible_speedup")),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
