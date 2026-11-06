#!/usr/bin/env python3
"""Group C: did the counters on the card move as predicted, by more than their own jitter?

A thin CLI over ``rsi.counters``, so the verifier and the regression tests agree on what
"moved" means. It never reads a timing result: the file it is given contains counters only.

    python3 compare_counters.py --card hypothesis.yaml --before profile_before.json \
        --after profile_after.json --measured measured.yaml [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLING = Path(__file__).resolve().parents[3] / "tooling"
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

from rsi import counters, yamlite  # noqa: E402


def load(path: str) -> dict:
    target = Path(path)
    if target.suffix in {".yaml", ".yml"}:
        return yamlite.load(target) or {}
    return json.loads(target.read_text(encoding="utf-8"))


def verdict_for(report: dict) -> dict:
    """Turn the comparison into the C-group conclusion and, when useful, a whole verdict."""
    if not report["checks"]:
        return {"C": "fail", "reason": "the card names no counters, so nothing can be verified"}
    if any(check["before"] is None or check["after"] is None for check in report["checks"]):
        unknown = [check["metric"] for check in report["checks"]
                   if check["before"] is None or check["after"] is None]
        return {"C": "unknown",
                "reason": f"this device does not report {unknown}; the mechanism cannot be confirmed from them"}
    if not report["all_ok"]:
        failed = [check["metric"] for check in report["checks"]
                  if not check["direction_ok"] or check["expect_met"] is False]
        return {"C": "fail", "reason": f"counters did not move as predicted: {failed}"}
    if report["unnamed_regressions"]:
        return {"C": "unknown",
                "reason": f"unnamed counters got worse: {report['unnamed_regressions']}"}
    return {"C": "pass", "reason": counters.summarise_directions(report["checks"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--card", required=True)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--measured")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    card = load(args.card)
    before = load(args.before).get("metrics", {})
    after = load(args.after).get("metrics", {})
    measured = load(args.measured) if args.measured else {}
    report = counters.compare(card, before, after, measured)
    report["C"] = verdict_for(report)
    if report["unreliable_metrics"]:
        report["cross_check_required"] = report["unreliable_metrics"]
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["C"]["C"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
