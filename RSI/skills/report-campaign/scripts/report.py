#!/usr/bin/env python3
"""Build a campaign report or a periodic system report from the ledger.

Every number in the report is computed here from ledger entries, never typed by hand. That is
what makes the report cheap: no model judgement is needed to produce it, and a reader can
recompute any figure from the ids it cites.

Two report kinds:

* ``campaign``  one operator: attainment, confirmed changes, unexplained observations, what is
  left. Stops far from the bound -> the report says an algorithm-level rewrite is needed.
* ``system``    a window across operators: the per-version primary metric, the secondary metrics
  from the build plan, and the health of the knowledge base it was read from.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import ledger, knowledge  # noqa: E402


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


def mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return round(ordered[middle] if len(ordered) % 2 else
                 (ordered[middle - 1] + ordered[middle]) / 2, 4)


def campaign(inputs: Dict[str, Any]) -> Dict[str, Any]:
    entries = [entry for entry in (inputs.get("entries") or []) if isinstance(entry, dict)]
    rounds = [entry for entry in entries if entry.get("verdict") in ("confirmed", "refuted")]
    confirmed = [entry for entry in rounds if entry.get("verdict") == "confirmed"]
    won = [entry for entry in confirmed if entry.get("significant") is True]
    unexplained = [entry for entry in rounds
                   if entry.get("verdict") == "refuted" and entry.get("significant") is True]
    wrong_diagnosis = [entry for entry in confirmed if entry.get("significant") is False]
    target = float(inputs.get("target_attainment_pct") or 85.0)
    attainment = inputs.get("attainment_pct")
    attainment = float(attainment) if attainment is not None else None
    baseline = inputs.get("baseline_attainment_pct")
    baseline = float(baseline) if baseline is not None else None

    total_speedup = 1.0
    for entry in won:
        total_speedup *= float(entry.get("speedup") or 1.0)
    per_experiment = (math.log(total_speedup) / len(rounds)) if rounds and total_speedup > 0 else None
    open_gap = max(0.0, target - attainment) if attainment is not None else None
    rewrite = bool(attainment is not None and attainment < target
                   and inputs.get("stop_reason") in
                   ("budget_exhausted", "no_gain_in_five_rounds", "no_open_route"))

    return {
        "kind": "campaign",
        "op": str(inputs.get("op") or (entries[0].get("op") if entries else "")),
        "headline": {
            "attainment_pct": attainment,
            "baseline_attainment_pct": baseline,
            "gain_points": round(attainment - baseline, 4)
            if attainment is not None and baseline is not None else None,
            "target_attainment_pct": target,
            "gap_points": round(open_gap, 4) if open_gap is not None else None,
            "total_speedup": round(total_speedup, 4),
            "experiments": len(entries),
            "rounds": len(rounds),
            "stop_reason": inputs.get("stop_reason"),
        },
        "confirmed_changes": [
            {"exp_id": entry.get("exp_id"),
             "transformation": (entry.get("hypothesis") or {}).get("transformation"),
             "speedup": entry.get("speedup"),
             "prediction_error": entry.get("prediction_error")}
            for entry in won
        ],
        "unexplained_observations": [
            {"exp_id": entry.get("exp_id"),
             "transformation": (entry.get("hypothesis") or {}).get("transformation"),
             "speedup": entry.get("speedup"),
             "note": "faster without the predicted mechanism; the real cause is unknown"}
            for entry in unexplained
        ],
        "wrong_diagnoses": [
            {"exp_id": entry.get("exp_id"),
             "transformation": (entry.get("hypothesis") or {}).get("transformation"),
             "note": "mechanism confirmed, no gain: the signature pointed at the wrong limiter"}
            for entry in wrong_diagnosis
        ],
        "remaining_opportunities": [
            str(item) for item in (inputs.get("open_routes") or [])
            if isinstance(item, str)
        ],
        "efficiency": {
            "log_speedup_per_experiment": round(per_experiment, 4) if per_experiment is not None else None,
            "experiments_to_target": (
                math.ceil(math.log(target / max(attainment, 1e-9)) / per_experiment)
                if per_experiment and attainment and attainment > 0 and per_experiment > 0 else None
            ),
            "mean_prediction_error": mean([
                float(entry["prediction_error"]) for entry in rounds
                if entry.get("prediction_error") is not None]),
        },
        "needs_algorithm_rewrite": rewrite,
        "verdict": _campaign_verdict(attainment, target, rewrite, len(won)),
    }


def _campaign_verdict(attainment: Optional[float], target: float, rewrite: bool,
                      wins: int) -> str:
    if attainment is None:
        return "inconclusive: no attainment was recorded for this campaign"
    if attainment >= target:
        return f"reached the target with {wins} confirmed change(s)"
    if rewrite:
        return ("stopped away from the bound: the remaining gap needs an algorithm-level rewrite, "
                "not another tactic from the current playbook")
    return "still improving: the campaign ended with budget or direction left"


def system(inputs: Dict[str, Any]) -> Dict[str, Any]:
    entries = [entry for entry in (inputs.get("entries") or []) if isinstance(entry, dict)]
    by_version: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        by_version.setdefault(str(entry.get("scaffold_commit") or "unknown"), []).append(entry)

    versions = []
    for commit, group in sorted(by_version.items()):
        rounds = [entry for entry in group if entry.get("verdict") in ("confirmed", "refuted")]
        won = [entry for entry in rounds
               if entry.get("verdict") == "confirmed" and entry.get("significant") is True]
        total = 1.0
        for entry in won:
            total *= float(entry.get("speedup") or 1.0)
        attempts = len(group)
        versions.append({
            "scaffold_commit": commit,
            "experiments": attempts,
            "rounds": len(rounds),
            "log_speedup_per_experiment": round(math.log(total) / len(rounds), 4) if rounds and total > 0 else None,
            "not_implemented_rate": round(
                sum(1 for entry in group if entry.get("verdict") == "not_implemented") / attempts, 4
            ) if attempts else None,
            "confirmation_rate": round(
                sum(1 for entry in rounds if entry.get("verdict") == "confirmed") / len(rounds), 4
            ) if rounds else None,
            "median_prediction_error": median([
                float(entry["prediction_error"]) for entry in rounds
                if entry.get("prediction_error") is not None]),
            "operators": sorted({str(entry.get("op")) for entry in group}),
        })
    versions.sort(key=lambda item: (item["scaffold_commit"]))

    operators: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        operators.setdefault(str(entry.get("op")), []).append(entry)
    per_operator = []
    for op, group in sorted(operators.items()):
        rounds = [entry for entry in group if entry.get("verdict") in ("confirmed", "refuted")]
        per_operator.append({
            "op": op,
            "experiments": len(group),
            "attainment_pct": (inputs.get("attainment_by_op") or {}).get(op),
            "confirmed": sum(1 for entry in group if entry.get("verdict") == "confirmed"),
            "not_implemented": sum(1 for entry in group if entry.get("verdict") == "not_implemented"),
            "mean_prediction_error": mean([
                float(entry["prediction_error"]) for entry in rounds
                if entry.get("prediction_error") is not None]),
        })

    counts = ledger.evidence_counts(entries)
    kb = inputs.get("kb_summary") or {}
    rules = int(kb.get("rules") or 0)
    return {
        "kind": "system",
        "window": {
            "experiments": len(entries),
            "operators": len(operators),
            "first_exp": entries[0].get("exp_id") if entries else None,
            "last_exp": entries[-1].get("exp_id") if entries else None,
        },
        "by_scaffold_version": versions,
        "per_operator": per_operator,
        "secondary_metrics": {
            "not_implemented_rate": round(counts["not_implemented"] / len(entries), 4)
            if entries else None,
            "confirmation_rate": round(
                counts["confirmed"] / max(1, counts["confirmed"] + counts["refuted"]), 4),
            "implausible": counts["implausible"],
            "median_prediction_error": median([
                float(entry["prediction_error"]) for entry in entries
                if entry.get("prediction_error") is not None]),
        },
        "knowledge_health": {
            "established_rules": int(kb.get("established") or 0),
            "rules": rules,
            "mean_evidence_per_rule": round(int(kb.get("evidence_total") or 0) / rules, 4)
            if rules else None,
            "deprecated_share": round(int(kb.get("deprecated") or 0) / rules, 4) if rules else None,
        },
        "developments": _developments(versions),
    }


def _developments(versions: List[Dict[str, Any]]) -> List[str]:
    notes: List[str] = []
    measurable = [v for v in versions if v["log_speedup_per_experiment"] is not None]
    if len(measurable) >= 2:
        first, last = measurable[0], measurable[-1]
        trend = "up" if (last["log_speedup_per_experiment"] or 0) > (first["log_speedup_per_experiment"] or 0) else "flat or down"
        notes.append(
            f"the primary metric is {trend} from {first['scaffold_commit'][:12]} to "
            f"{last['scaffold_commit'][:12]}")
    if any((v["not_implemented_rate"] or 0) > 0.3 for v in versions):
        notes.append("not-implemented rate above 0.3 on at least one scaffold version: the "
                     "implementer tier or the card wording is the constraint")
    if any((v["median_prediction_error"] or 0) > knowledge.PREDICTION_ERROR_HOLE for v in versions):
        notes.append("median prediction error above the hole threshold: the hardware model has a "
                     "gap, so a microbenchmark is worth more than a playbook edit")
    if not notes:
        notes.append("not enough versions with rounds to state a trend yet")
    return notes


def build(inputs: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(inputs.get("report_kind") or "campaign")
    body = campaign(inputs) if kind == "campaign" else system(inputs)
    return {
        "id": str(inputs.get("report_id") or "REPORT-1"),
        "kind": kind,
        "created": str(inputs.get("today") or ""),
        "generated_from": {
            "ledger_entries": len([entry for entry in (inputs.get("entries") or [])
                                   if isinstance(entry, dict)]),
            "source": "ledger/experiments.jsonl",
        },
        "report": body,
    }


def main(argv: List[str]) -> int:
    json.dump(build(read_input(argv)), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
