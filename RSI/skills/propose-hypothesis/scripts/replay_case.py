#!/usr/bin/env python3
"""Offline regression driver for propose-hypothesis.

Prints the decision a case asserts: which cards survived, in what order, what was rejected and
with which reason, and whether the noise-floor warning fired. The card texts are fixtures, so a
case tests the gate rather than the prose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from propose_hypothesis import propose  # noqa: E402


def main() -> int:
    case = json.load(sys.stdin)
    path = Path(str(case["cards_file"]))
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = payload.get("cards") if isinstance(payload, dict) else payload
    request = {
        "cards": cards,
        "unavailable_metrics": case.get("unavailable_metrics") or [],
        "noise_threshold": case.get("noise_threshold"),
        "op": case.get("op", "vec_add"),
        "parent": case.get("parent", "best@exp-0003"),
    }
    outcome = propose(request)
    rejection_reasons = []
    for entry in outcome["rejected"]:
        reason = entry["reason"]
        if "held back" in reason:
            kind = "held_back"
        elif "canonical registry" in reason:
            kind = "unregistered_metric"
        elif "cannot produce" in reason:
            kind = "unavailable_metric"
        elif "hypothesis contract" in reason:
            kind = "contract"
        else:
            kind = "other"
        rejection_reasons.append({"transformation": entry["transformation"], "kind": kind})
    print(json.dumps({
        "status": outcome["status"],
        "card_ids": [card.get("id") for card in outcome["cards"]],
        "priorities": [card.get("priority") for card in outcome["cards"]],
        "card_count": len(outcome["cards"]),
        "rejections": rejection_reasons,
        "rejected_count": len(outcome["rejected"]),
        "noise_warnings": sum(1 for note in outcome["notes"] if "noise floor" in note),
        "ungrounded_warnings": sum(1 for note in outcome["notes"] if "rests on the signature" in note),
        "all_counters_canonical": all(
            isinstance(counter.get("metric"), str)
            for card in outcome["cards"]
            for counter in (card.get("predictions") or {}).get("counters") or []),
        "problems": outcome.get("problems"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
