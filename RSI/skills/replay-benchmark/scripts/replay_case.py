#!/usr/bin/env python3
"""Offline regression driver for replay-benchmark.

Runs the skill's own decision path over a stored set of configurations. The changelog is pinned to
a scratch path and the run is a dry run, so a case exercises the protocol without touching the real
scaffold changelog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_benchmark import replay  # noqa: E402


def main() -> int:
    case = json.load(sys.stdin)
    payload = json.loads((Path(case["fixture"])).read_text(encoding="utf-8"))
    request = {
        "proposal": payload.get("proposal"),
        "configs": payload.get("configs") or [],
        "change_summary": payload.get("change_summary"),
        "repeats": case.get("repeats", payload.get("repeats", 3)),
        "stage": case.get("stage", payload.get("stage", "full")),
        "budget": payload.get("budget") or {"experiments_per_operator": 10, "repeats": 3},
        "dry_run": True,
        "id": "RP-9000",
        "seed": 0,
    }
    if case.get("single_change_confirmed"):
        request["single_change_confirmed"] = True
    outcome = replay(request)
    report = outcome.get("report") or {}
    metric = report.get("primary_metric") or {}
    totals = outcome.get("split_totals") or {}
    print(json.dumps({
        "status": outcome["status"],
        "decision": outcome.get("decision"),
        "report_decision": report.get("decision"),
        "excludes_zero": metric.get("excludes_zero"),
        "holdout_regressed": report.get("holdout_regressed"),
        "secondary_regressed": report.get("secondary_regressed"),
        "screening_stage": report.get("screening_stage"),
        "changelog_applied": outcome.get("changelog_applied"),
        "dev_configs": (totals.get("dev") or {}).get("configs"),
        "holdout_configs": (totals.get("holdout") or {}).get("configs"),
        "split_names_only": sorted(totals.keys()),
        "leaks_operator_names": "operator" in json.dumps(totals) or "op-" in json.dumps(totals),
        "problems": outcome.get("problems"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
