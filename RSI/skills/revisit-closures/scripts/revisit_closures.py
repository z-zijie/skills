#!/usr/bin/env python3
"""revisit-closures: which written-off directions must be reopened before anything stops.

A thin CLI over ``rsi.closures``, so the skill's judgement and the campaign state machine read
the same classification. Reads the skill input on stdin (or ``--input``) and prints the plan.

    python3 revisit_closures.py --input revisit.json
    python3 revisit_closures.py --input revisit.json --json-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLING = Path(__file__).resolve().parents[3] / "tooling"
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

from rsi import closures  # noqa: E402


def read_input(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", help="input JSON path; stdin when omitted")
    parser.add_argument("--json-only", action="store_true",
                        help="print the plan without the per-closure lines")
    args, _ = parser.parse_known_args(argv)
    if args.input:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        raw = sys.stdin.read().strip()
        data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        raise SystemExit("input must be a JSON object")
    return data, args


def plan(data):
    return closures.revisit_plan(
        data.get("entries") or [],
        verdicts=data.get("verdicts") or {},
        stalled_rounds=int(data.get("stalled_rounds") or 0),
        proposed_action=data.get("proposed_action"),
        revisit_after_stalled=int(data.get("revisit_after_stalled_rounds") or 3),
        min_open=int(data.get("min_open_closures") or closures.MIN_OPEN_CLOSURES),
        min_points=int(data.get("min_points") or closures.MIN_POINTS),
        max_reopens=int(data.get("max_reopens") or closures.MAX_REOPENS),
        max_reopen_cards=int(data.get("max_reopen_cards") or 3),
    )


def main(argv=None) -> int:
    data, args = read_input(argv if argv is not None else sys.argv[1:])
    report = plan(data)
    if not args.json_only:
        report["summary"] = (
            f"{report['decision']}: {len(report['reopens'])} to reopen, "
            f"{len(report['open'])} open, {len(report['retirable'])} retirable, "
            f"{len(report['capped'])} capped"
        )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
