#!/usr/bin/env python3
"""Run compare_counters over a test case supplied on stdin, and print its C-group conclusion.

The regression tests drive the real comparator rather than a copy of its rules: a test that
reimplements the logic passes while the shipped logic is broken.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from compare_counters import verdict_for  # noqa: E402

TOOLING = HERE.parents[2] / "tooling"
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

from rsi import counters  # noqa: E402


def main() -> int:
    payload = json.load(sys.stdin)
    data = payload.get("input") or payload
    report = counters.compare(data["card"], data["before"], data["after"], data.get("measured"))
    print(json.dumps(verdict_for(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
