#!/usr/bin/env python3
"""Decide one campaign move from one operator's ledger.

Reads the skill input on stdin (or --input), prints the decision as JSON. Pure over its
input, so the same ledger always yields the same action and a strategy change can be
replayed offline before it costs device time.

Stop conditions come from the build plan (section 7, W3): target attainment reached, budget
spent, five rounds without a significant gain, or no open route predicted above the noise
threshold. Three rounds without a gain re-diagnose before the direction changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import knowledge, ledger  # noqa: E402

ROUND_VERDICTS = ("confirmed", "refuted")
REDIAGNOSE_AFTER = 3
STOP_AFTER = 5
STOP_REASONS = (
    "target_reached",
    "budget_exhausted",
    "no_gain_in_five_rounds",
    "no_open_route",
)
_WORD_RE = re.compile(r"[a-z0-9_]+")


def read_input(argv: List[str]) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="input JSON path; stdin when omitted")
    args, _ = parser.parse_known_args(argv)
    if args.input:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        raw = sys.stdin.read().strip()
        data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        raise SystemExit("input must be a JSON object")
    return data


def load_entries(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries = data.get("entries")
    if entries is not None:
        return [entry for entry in entries if isinstance(entry, dict)]
    op = str(data.get("op") or "")
    return ledger.entries_for_op(op) if op else []


def rounds_of(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Experiments that count as a round: confirmed or refuted verdicts, oldest first."""
    return [entry for entry in entries if entry.get("verdict") in ROUND_VERDICTS]


def streak_without_gain(rounds: List[Dict[str, Any]]) -> int:
    streak = 0
    for entry in reversed(rounds):
        if entry.get("significant") is True:
            break
        streak += 1
    return streak


def transformation(entry: Dict[str, Any]) -> Optional[str]:
    return ((entry.get("hypothesis") or {}).get("transformation") or None)


def scope_words(entry: Dict[str, Any]) -> set:
    scope = ((entry.get("hypothesis") or {}).get("scope_of_change") or "").lower()
    return set(_WORD_RE.findall(scope))


def independent(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Two changes are independent when they claim different, non-overlapping scopes."""
    left_t, right_t = transformation(left), transformation(right)
    if not left_t or not right_t or left_t.lower() == right_t.lower():
        return False
    return not (scope_words(left) & scope_words(right))


def combine_candidate(rounds: List[Dict[str, Any]], noise: float) -> Optional[Dict[str, Any]]:
    """Two confirmed, significant, independent changes whose product clears the noise floor."""
    won = [entry for entry in rounds
           if entry.get("verdict") == "confirmed" and entry.get("significant") is True]
    best: Optional[Dict[str, Any]] = None
    for index, left in enumerate(won):
        for right in won[index + 1:]:
            if not independent(left, right):
                continue
            predicted = float(left.get("speedup") or 1.0) * float(right.get("speedup") or 1.0)
            if predicted <= 1.0 + noise:
                continue
            pairs = sorted([str(left.get("exp_id")), str(right.get("exp_id"))])
            candidate = {
                "combine": pairs,
                "transformations": [transformation(left), transformation(right)],
                "predicted_speedup": round(predicted, 4),
            }
            if best is None or candidate["predicted_speedup"] > best["predicted_speedup"]:
                best = candidate
            elif (candidate["predicted_speedup"] == best["predicted_speedup"]
                  and pairs < best["combine"]):
                best = candidate
    return best


def credible_routes(data: Dict[str, Any], noise: float) -> List[Dict[str, Any]]:
    routes = [route for route in (data.get("open_routes") or []) if isinstance(route, dict)]
    keep = []
    for route in routes:
        predicted = route.get("predicted_speedup")
        if predicted is None or float(predicted) > 1.0 + noise:
            keep.append(route)
    keep.sort(key=lambda route: (-float(route.get("predicted_speedup") or 0.0),
                                 str(route.get("transformation") or "")))
    return keep


def decide(data: Dict[str, Any]) -> Dict[str, Any]:
    entries = load_entries(data)
    rounds = rounds_of(entries)
    noise = float(data.get("noise_threshold") or 0.0)
    hole = float(data.get("prediction_error_hole") or knowledge.PREDICTION_ERROR_HOLE)
    target = float(data.get("target_attainment_pct") or 85.0)
    attainment = data.get("attainment_pct")
    attainment = float(attainment) if attainment is not None else None
    budget = data.get("budget") or {}
    planned = int(budget.get("experiments") or 0)
    spent = budget.get("spent_experiments")
    if spent is None:
        spent = len(entries)
    spent = int(spent)
    streak = streak_without_gain(rounds)
    routes = credible_routes(data, noise)
    top_route = routes[0] if routes else None
    combine = combine_candidate(rounds, noise)

    action, reason, next_step, stop_reason = "continue", "", {}, None
    evidence: List[str] = []

    if not entries:
        reason = "campaign start: no experiment recorded yet"
        next_step = {"transformation": top_route.get("transformation") if top_route else None,
                     "parent": str(data.get("start_from") or "baseline"),
                     "combine": [], "predicted_speedup": None, "rediagnose_first": False}
    elif attainment is not None and attainment >= target:
        action, stop_reason = "stop", "target_reached"
        reason = f"attainment {attainment:.1f}% has reached the {target:.1f}% target"
        evidence = [str(entry.get("exp_id")) for entry in rounds[-1:]]
    elif planned and spent >= planned:
        action, stop_reason = "stop", "budget_exhausted"
        reason = f"{spent} of {planned} budgeted experiments are spent"
        evidence = [str(entry.get("exp_id")) for entry in rounds[-1:]]
    elif streak >= STOP_AFTER and not routes:
        action, stop_reason = "stop", "no_gain_in_five_rounds"
        reason = (f"{streak} rounds without a significant gain and no open route predicts a "
                  f"speedup above the {noise:.3f} noise floor")
        evidence = [str(entry.get("exp_id")) for entry in rounds[-STOP_AFTER:]]
    elif streak >= REDIAGNOSE_AFTER:
        action = "change_direction"
        reason = (f"{streak} rounds without a significant gain: re-profile and re-diagnose "
                  "before choosing another route")
        next_step = {
            "transformation": top_route.get("transformation") if top_route else None,
            "parent": str(data.get("start_from") or "best"),
            "combine": [],
            "predicted_speedup": (top_route or {}).get("predicted_speedup"),
            "rediagnose_first": True,
        }
        evidence = [str(entry.get("exp_id")) for entry in rounds[-streak:]]
    elif streak == 0 and top_route is not None:
        reason = "the last round improved attainment and a credible route remains open"
        next_step = {"transformation": top_route.get("transformation"),
                     "parent": str(data.get("start_from") or "best"),
                     "combine": [],
                     "predicted_speedup": top_route.get("predicted_speedup"),
                     "rediagnose_first": False}
        evidence = [str(entry.get("exp_id")) for entry in rounds[-1:]]
    elif combine is not None:
        action = "combine"
        reason = ("no single direction is gaining; two confirmed, significant and independent "
                  "changes are available and their product clears the noise floor")
        next_step = {"transformation": "+".join(combine["transformations"]),
                     "parent": str(data.get("start_from") or "best"),
                     "combine": combine["combine"],
                     "predicted_speedup": combine["predicted_speedup"],
                     "rediagnose_first": False}
        evidence = list(combine["combine"])
    elif not top_route:
        action, stop_reason = "stop", "no_open_route"
        reason = ("no open route predicts a speedup above the noise floor; the remaining gap "
                  "is not addressable by the current playbook")
        evidence = [str(entry.get("exp_id")) for entry in rounds[-1:]]
    else:
        reason = "the last round improved attainment and a credible route remains open"
        next_step = {"transformation": top_route.get("transformation"),
                     "parent": str(data.get("start_from") or "best"),
                     "combine": [],
                     "predicted_speedup": top_route.get("predicted_speedup"),
                     "rediagnose_first": False}
        evidence = [str(entry.get("exp_id")) for entry in rounds[-1:]]

    near_hole = [entry for entry in rounds[-3:]
                 if float(entry.get("prediction_error") or 0.0) > hole]
    needs_rewrite = bool(
        action == "stop"
        and stop_reason in ("budget_exhausted", "no_gain_in_five_rounds", "no_open_route")
        and attainment is not None and attainment < target
    )
    return {
        "op": str(data.get("op") or (entries[0].get("op") if entries else "")),
        "action": action,
        "reason": reason,
        "evidence": evidence,
        "next": {
            "transformation": next_step.get("transformation"),
            "parent": next_step.get("parent"),
            "combine": list(next_step.get("combine") or []),
            "predicted_speedup": next_step.get("predicted_speedup"),
            "rediagnose_first": bool(next_step.get("rediagnose_first")),
        },
        "attainment_pct": attainment,
        "target_attainment_pct": target,
        "streak_without_gain": streak,
        "budget": {"experiments": planned, "spent_experiments": spent,
                   "remaining": max(0, planned - spent) if planned else 0},
        "stop_reason": stop_reason,
        "routes_above_noise": len(routes),
        "prediction_error_watch": [str(entry.get("exp_id")) for entry in near_hole],
        "needs_algorithm_rewrite": needs_rewrite,
    }


def main(argv: List[str]) -> int:
    data = read_input(argv)
    decision = decide(data)
    json.dump(decision, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
