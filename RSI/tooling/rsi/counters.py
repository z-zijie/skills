"""Counter comparison for the mechanism verifier.

A counter move only counts if it is larger than that counter's own run-to-run variation. The
verifier never sees the timing result, so this module plainly does not take one: it compares
predicted counter directions with before/after profiles, and says for each counter whether
the direction matched and whether the size cleared the measured jitter.

The jitter comes from the calibration suite (``kb/hardware/measured/<hw>.yaml``,
``counter_variation``). Without a measurement the fallback is a conservative relative band,
and the verdict is labelled provisional rather than confident.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from . import metrics as metrics_mod

#: Used only when the calibration suite has not measured a counter's jitter yet.
FALLBACK_RELATIVE_VARIATION = 0.05

_OPERATOR_RE = re.compile(r"^\s*(<=|>=|<|>|==|=)?\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*$")


def parse_expect(text: Any) -> Optional[Dict[str, Any]]:
    """Parse a card's ``expect`` string such as ``'<= 1.5'`` or ``'> 80'``."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return {"op": "==", "value": float(text)}
    match = _OPERATOR_RE.match(str(text))
    if not match:
        return None
    operator = match.group(1) or "=="
    if operator == "=":
        operator = "=="
    return {"op": operator, "value": float(match.group(2))}


def satisfies(value: float, expect: Optional[Dict[str, Any]]) -> bool:
    if expect is None:
        return True
    operator, bound = expect["op"], expect["value"]
    if operator == "<=":
        return value <= bound
    if operator == ">=":
        return value >= bound
    if operator == "<":
        return value < bound
    if operator == ">":
        return value > bound
    return abs(value - bound) <= 1e-9


def direction_ok(before: Optional[float], after: Optional[float], direction: str,
                 threshold: float = 0.0) -> bool:
    """Whether the counter moved in the predicted direction by more than ``threshold``."""
    if before is None or after is None:
        return False
    delta = after - before
    if direction == "up":
        return delta > threshold
    if direction == "down":
        return delta < -threshold
    return False


def variation_for(metric: str, measured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run-to-run variation for one counter, measured when possible."""
    canon = metrics_mod.canonical(metric)
    table = ((measured or {}).get("counter_variation") or {})
    entry = table.get(canon)
    if isinstance(entry, dict) and entry.get("rel") is not None:
        return {"rel": float(entry["rel"]), "source": "measured", "abs": entry.get("abs")}
    if isinstance(entry, (int, float)):
        return {"rel": float(entry), "source": "measured", "abs": None}
    return {"rel": FALLBACK_RELATIVE_VARIATION, "source": "fallback", "abs": None}


def compare(card: Dict[str, Any], counters_before: Dict[str, Any], counters_after: Dict[str, Any],
            measured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compare every counter the card names, plus flag unnamed counters that got worse.

    Returns ``{"checks": [...], "all_ok": bool, "any_failed": bool, "provisional": bool}``.
    """
    before = metrics_mod.normalise(counters_before or {})
    after = metrics_mod.normalise(counters_after or {})
    predictions = (card.get("predictions") or {}).get("counters") or []
    checks: List[Dict[str, Any]] = []
    provisional = False
    for prediction in predictions:
        metric = metrics_mod.canonical(str(prediction.get("metric")))
        direction = str(prediction.get("direction", "up"))
        expect = parse_expect(prediction.get("expect"))
        variation = variation_for(metric, measured)
        if variation["source"] == "fallback":
            provisional = True
        before_value = _number(before.get(metric))
        after_value = _number(after.get(metric))
        changed = direction_ok(before_value, after_value, direction,
                              threshold=_noise_floor(before_value, after_value, variation))
        reached = satisfies(after_value, expect) if after_value is not None else False
        note = ""
        if before_value is None or after_value is None:
            note = "counter not reported by this device's profiler; cannot be judged"
        elif not changed:
            note = (f"change {before_value:g} -> {after_value:g} is inside the "
                    f"{variation['source']} variation band")
        checks.append({
            "metric": metric,
            "predicted_direction": direction,
            "before": before_value,
            "after": after_value,
            "direction_ok": bool(changed),
            "exceeds_noise": bool(changed),
            "expect": prediction.get("expect"),
            "expect_met": reached if expect is not None else None,
            "variation": variation,
            "note": note,
        })

    # Unnamed counters that got clearly worse undercut a claimed mechanism.
    named = {check["metric"] for check in checks}
    regressions: List[str] = []
    for metric in sorted(set(before) & set(after)):
        if metric in named:
            continue
        worse_direction = metrics_mod.better_direction(metric) if metrics_mod.is_known(metric) else None
        if worse_direction is None:
            continue
        variation = variation_for(metric, measured)
        if direction_ok(_number(before[metric]), _number(after[metric]), "up" if worse_direction == "higher" else "down",
                        threshold=_noise_floor(_number(before[metric]), _number(after[metric]), variation)):
            regressions.append(metric)

    all_ok = bool(checks) and all(check["direction_ok"] and check["expect_met"] is not False for check in checks)
    return {
        "checks": checks,
        "all_ok": all_ok,
        "any_failed": any(not check["direction_ok"] for check in checks),
        "unnamed_regressions": regressions,
        "provisional": provisional,
        "unreliable_metrics": [
            check["metric"] for check in checks
            if check["metric"] in metrics_mod.UNRELIABLE_BY_DESIGN
        ],
    }


def _noise_floor(before: Optional[float], after: Optional[float], variation: Dict[str, Any]) -> float:
    base = max(abs(before or 0.0), abs(after or 0.0))
    floor = variation["rel"] * base
    if variation.get("abs") is not None:
        floor = max(floor, float(variation["abs"]))
    return floor


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarise_directions(checks: Iterable[Dict[str, Any]]) -> str:
    parts = []
    for check in checks:
        arrow = "down" if check["predicted_direction"] == "down" else "up"
        status = "ok" if check["direction_ok"] else "no"
        parts.append(f"{check['metric']} predicted {arrow} -> {status}")
    return "; ".join(parts)
