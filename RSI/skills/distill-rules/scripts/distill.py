#!/usr/bin/env python3
"""Propose rule-card creates and updates from a ledger window.

Groups confirmed and refuted experiments by (transformation, scope), builds one rule card per
group, and asks ``rsi.knowledge.lifecycle`` for the status. The status is never decided here:
the life cycle is mechanical, and this script only assembles the evidence the life cycle reads.

Prints a proposal set. Nothing is written to ``kb/rules``; the caller writes the proposal under
``kb/proposals/rules/`` and the gate decides.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import knowledge, ledger, metrics, signature as signature_mod  # noqa: E402


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


def scope_key(entry: Dict[str, Any]) -> Tuple[str, ...]:
    """The retrieval key a rule distilled from this entry would carry."""
    card = entry.get("hypothesis") or {}
    sig = entry.get("signature") or {}
    tags = sorted({str(tag) for tag in (card.get("signature_tags") or sig.get("tags") or [])})
    classes = [str(sig["class"])] if sig.get("class") else []
    return (str(entry.get("hw_id") or "unknown"),) + tuple(tags) + tuple(classes)


def group_entries(entries: List[Dict[str, Any]]) -> Dict[Tuple[str, ...], List[Dict[str, Any]]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("verdict") not in ("confirmed", "refuted"):
            continue
        if entry.get("implausible"):
            continue
        card = entry.get("hypothesis") or {}
        if not card.get("transformation"):
            continue
        groups.setdefault(scope_key(entry), []).append(entry)
    return groups


def counters_moved(entries: List[Dict[str, Any]]) -> List[str]:
    moved: List[str] = []
    for entry in entries:
        for prediction in ((entry.get("hypothesis") or {}).get("predictions") or {}).get("counters") or []:
            name = metrics.canonical(str(prediction.get("metric")))
            if name not in moved and metrics.is_known(name):
                moved.append(name)
    return moved


def speedup_range(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    won = sorted(float(entry["speedup"]) for entry in entries
                 if entry.get("verdict") == "confirmed" and entry.get("speedup") is not None)
    if not won:
        return {"speedup_median": None, "range": None}
    middle = len(won) // 2
    median = won[middle] if len(won) % 2 else (won[middle - 1] + won[middle]) / 2
    return {"speedup_median": round(median, 4), "range": [round(won[0], 4), round(won[-1], 4)]}


def match_existing(rule: Dict[str, Any], existing: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The stored card this group extends, when one already covers the same claim.

    A group updates a stored rule rather than creating a second card for it: two cards with one
    claim halve each other's confirmation count and neither reaches established.
    """
    scope = rule.get("scope") or {}
    for stored in existing:
        stored_scope = stored.get("scope") or {}
        if list(stored_scope.get("hw") or []) != list(scope.get("hw") or []):
            continue
        if list(stored_scope.get("tags_all") or []) != list(scope.get("tags_all") or []):
            continue
        if str(stored.get("transformation") or "") == str(rule.get("transformation") or ""):
            return stored
        if stored.get("mechanism") and stored.get("mechanism") == rule.get("mechanism"):
            return stored
    return None


def operator_of(entries: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(entry.get("exp_id")): str(entry.get("op")) for entry in entries}


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    rows = [entry for entry in (data.get("entries") or []) if isinstance(entry, dict)]
    op_of_exp = operator_of(rows)
    next_id = int(data.get("next_rule_id") or 1)
    proposals: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for key, members in sorted(group_entries(rows).items()):
        hw, *rest = key
        tags = [item for item in rest if item not in metrics.CLASSES]
        classes = [item for item in rest if item in metrics.CLASSES]
        transformation = str((members[-1].get("hypothesis") or {}).get("transformation"))
        confirmed = [str(entry.get("exp_id")) for entry in members
                     if entry.get("verdict") == "confirmed"]
        refuted = [str(entry.get("exp_id")) for entry in members
                   if entry.get("verdict") == "refuted"]
        if not confirmed:
            skipped.append({
                "reason": "no confirmation: an all-refuted group becomes an anti-pattern, not a rule",
                "experiments": refuted,
                "transformation": transformation,
            })
            continue
        effect = speedup_range(members)
        errors = [float(entry["prediction_error"]) for entry in members
                  if entry.get("prediction_error") is not None]
        counters = counters_moved(members)
        condition = ", ".join(tags) if tags else "this operator's class"
        moved = counters[0] if counters else "the named counters"
        median = effect["speedup_median"] or 1.0
        rule = {
            "id": f"R-{next_id:03d}",
            "statement": (
                f"When the signature is {condition} on {hw}, {transformation} moves {moved} "
                f"and gives about {median:.2f}x."
            ),
            "scope": {
                "hw": [hw],
                "tags_all": tags[:1],
                "tags_any": tags[1:],
                "classes": classes,
            },
            "effect": {
                "metric": counters[0] if counters else None,
                "direction": "down",
                "speedup_median": effect["speedup_median"],
                "range": effect["range"],
            },
            "evidence": {"confirmed": confirmed, "refuted": refuted},
            "confirm_rate": knowledge.confirm_rate({"evidence": {"confirmed": confirmed, "refuted": refuted}}),
            "mean_prediction_error": round(sum(errors) / len(errors), 4) if errors else None,
            "status": "candidate",
            "last_verified": str(data.get("today") or ""),
            "mechanism": str((members[-1].get("hypothesis") or {}).get("mechanism") or ""),
            "expected_counters": counters,
        }
        decision = knowledge.lifecycle(rule, op_of_exp,
                                       hardware_changed=bool(data.get("hardware_changed")))
        rule["status"] = decision["status"]
        if decision["status"] == "deprecated":
            skipped.append({
                "reason": f"life cycle says deprecated: {decision['reason']}",
                "experiments": confirmed + refuted,
                "transformation": transformation,
            })
            continue
        candidate = dict(rule)
        if candidate["effect"].get("metric") is None:
            candidate["effect"] = {k: v for k, v in candidate["effect"].items() if k != "metric"}
        if candidate["effect"].get("range") is None:
            candidate["effect"] = {k: v for k, v in candidate["effect"].items() if k != "range"}
        errors_found = knowledge.validate_rule(candidate)
        if errors_found:
            skipped.append({"reason": "rule card did not validate: " + "; ".join(errors_found),
                            "experiments": confirmed + refuted,
                            "transformation": transformation})
            continue
        existing = [item for item in (data.get("existing_rules") or []) if isinstance(item, dict)]
        stored = match_existing(dict(rule, transformation=transformation), existing)
        action = "create"
        if stored is not None:
            action = "update"
            merged = dict(rule)
            merged["id"] = str(stored.get("id") or rule["id"])
            merged["evidence"] = {
                "confirmed": _union(list((stored.get("evidence") or {}).get("confirmed") or []),
                                    confirmed),
                "refuted": _union(list((stored.get("evidence") or {}).get("refuted") or []),
                                  refuted),
            }
            merged["confirm_rate"] = knowledge.confirm_rate(merged)
            settled = knowledge.lifecycle(merged, op_of_exp,
                                          hardware_changed=bool(data.get("hardware_changed")))
            merged["status"] = settled["status"]
            decision = settled
            rule = merged
        proposals.append({
            "kind": "rule",
            "action": action,
            "rule": rule,
            "lifecycle_reason": decision["reason"],
            "operators": sorted({op_of_exp[exp] for exp in confirmed + refuted
                                 if exp in op_of_exp}),
            "evidence": confirmed + refuted,
            "title": f"{transformation} under {', '.join(tags) or 'the current signature'} on {hw}",
        })
        next_id += 1

    never_implemented = [str(entry.get("exp_id")) for entry in rows
                         if entry.get("verdict") == "not_implemented"]
    if never_implemented:
        skipped.insert(0, {
            "reason": ("not implemented: these attempts never reached the machine code, so they "
                       "carry no evidence either way"),
            "experiments": never_implemented,
            "transformation": "",
        })

    return {
        "proposals": proposals,
        "skipped": skipped,
        "counts": {
            "proposed": len(proposals),
            "established": sum(1 for item in proposals if item["rule"]["status"] == "established"),
            "candidate": sum(1 for item in proposals if item["rule"]["status"] == "candidate"),
            "under_review": sum(1 for item in proposals if item["rule"]["status"] == "under_review"),
        },
        "lifecycle_thresholds": {
            "min_confirmations": knowledge.ESTABLISHED_MIN_CONFIRMATIONS,
            "min_operators": knowledge.ESTABLISHED_MIN_OPERATORS,
            "min_confirm_rate": knowledge.ESTABLISHED_MIN_CONFIRM_RATE,
            "deprecated_below": knowledge.DEPRECATED_BELOW_CONFIRM_RATE,
            "prediction_error_hole": knowledge.PREDICTION_ERROR_HOLE,
        },
    }


def _union(existing: List[Any], new: List[Any]) -> List[str]:
    out: List[str] = []
    for item in list(existing) + list(new):
        text = str(item)
        if text not in out:
            out.append(text)
    return out


def main(argv: List[str]) -> int:
    json.dump(build(read_input(argv)), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
