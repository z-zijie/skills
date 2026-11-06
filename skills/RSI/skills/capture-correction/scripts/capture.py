#!/usr/bin/env python3
"""Turn one human correction into a proposal, after replaying a new check over history.

The load-bearing part is the replay. A proposed verifier check is applied to every ledger entry
it could touch: it must catch the past experiments of the same kind as the correction, and it
must not flag experiments that were rightly confirmed. A check that fails either test is not
proposed as a check; it is downgraded to an anti-pattern or returned to the human.

The classification of the correction is mechanical where it can be:

* the human names a loophole the evaluator allowed  -> ``evaluator_loophole``, human only
* the corrected experiment was a superficial confirm -> ``verifier_check``
* the corrected experiment was a rejected mechanism  -> ``antipattern``
* the correction is about try order or stopping      -> ``playbook``
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

from rsi import metrics  # noqa: E402

CORRECTIONS = ("verifier_check", "antipattern", "playbook", "evaluator_loophole")
GATES = {
    "verifier_check": "tighten_only_replay",
    "antipattern": "mechanical",
    "playbook": "replay_dev_holdout",
    "evaluator_loophole": "human_only",
}


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


def subject(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The fields a check can look at: what the entry claims and what it measured."""
    hypothesis = entry.get("hypothesis") or {}
    predictions = hypothesis.get("predictions") or {}
    return {
        "exp_id": str(entry.get("exp_id")),
        "op": entry.get("op"),
        "verdict": entry.get("verdict"),
        "significant": entry.get("significant"),
        "speedup": entry.get("speedup"),
        "implausible": bool(entry.get("implausible")),
        "transformation": hypothesis.get("transformation"),
        "scope_of_change": hypothesis.get("scope_of_change"),
        "tags": list(hypothesis.get("signature_tags") or (entry.get("signature") or {}).get("tags") or []),
        "counters": [str(c.get("metric")) for c in (predictions.get("counters") or [])
                     if c.get("metric")],
        "counter_moves": {
            metrics.canonical(str(c.get("metric"))): _delta(
                (entry.get("counters_before") or {}).get(str(c.get("metric"))),
                (entry.get("counters_after") or {}).get(str(c.get("metric"))))
            for c in (predictions.get("counters") or []) if c.get("metric")
        },
        "evidence_lines": list(hypothesis.get("evidence_of_implementation") or []),
        "correctness_passed": (entry.get("correctness") or {}).get("passed"),
        "unnamed_regressions": list(entry.get("unnamed_regressions") or []),
    }


def _delta(before: Any, after: Any) -> Optional[float]:
    try:
        return float(after) - float(before)
    except (TypeError, ValueError):
        return None


def check_predicate(spec: Dict[str, Any], item: Dict[str, Any]) -> Optional[bool]:
    """Evaluate one check predicate. None means the entry does not carry the fields it needs.

    Predicates are deliberately tiny and declarative so a human can read what their correction
    turned into, and so a check cannot hide a judgement.
    """
    kind = spec.get("predicate")
    metric_name = spec.get("metric") or spec.get("checks_metric")
    if kind == "metric_unmoved":
        metric = metrics.canonical(str(metric_name))
        if metric not in item["counter_moves"]:
            return None
        move = item["counter_moves"][metric]
        if move is None:
            return None
        band = float(spec.get("band") or 0.0)
        return abs(move) <= band
    if kind == "metric_moved_wrong_way":
        metric = metrics.canonical(str(metric_name))
        move = item["counter_moves"].get(metric)
        if move is None:
            return None
        return move > 0 if spec.get("direction") == "down" else move < 0
    if kind == "no_gain_but_confirmed":
        return item["verdict"] == "confirmed" and item["significant"] is False
    if kind == "evidence_line_missing":
        needle = str(spec.get("contains") or "").lower()
        return not any(needle in line.lower() for line in item["evidence_lines"])
    if kind == "scope_mismatch":
        field = str(spec.get("field") or "scope_of_change").lower()
        return field not in str(item.get("scope_of_change") or "").lower()
    if kind == "unnamed_regression":
        return bool(item["unnamed_regressions"])
    if kind == "implausible_speedup":
        return item["implausible"]
    if kind == "tag_overlap":
        required = {str(tag) for tag in (spec.get("tags") or [])}
        return bool(required & set(item["tags"]))
    return None


def replay(check: Dict[str, Any], entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply one check over ledger history and judge it against the two replay rules."""
    kinds_of_interest = set(str(k) for k in (check.get("must_catch") or []))
    rightly_confirmed = set(str(k) for k in (check.get("must_not_flag") or []))
    caught: List[str] = []
    flagged_right: List[str] = []
    skipped: List[str] = []
    for entry in entries:
        item = subject(entry)
        outcome = check_predicate(check, item)
        if outcome is None:
            skipped.append(item["exp_id"])
            continue
        if not outcome:
            continue
        if item["exp_id"] in rightly_confirmed:
            flagged_right.append(item["exp_id"])
        elif item["exp_id"] in kinds_of_interest or not kinds_of_interest:
            caught.append(item["exp_id"])
        elif item["exp_id"] != str((check.get("from_experiment") or "")):
            flagged_right.append(item["exp_id"])
    from_experiment = str(check.get("from_experiment") or "")
    if from_experiment and from_experiment not in caught and from_experiment not in skipped:
        caught.append(from_experiment)
    missed = sorted(kinds_of_interest - set(caught))
    return {
        "caught": sorted(set(caught)),
        "flagged_rightly_confirmed": sorted(set(flagged_right)),
        "could_not_evaluate": sorted(set(skipped)),
        "missed_expected": missed,
        "passes": bool(caught) and not flagged_right and not missed,
    }


def classify(data: Dict[str, Any]) -> str:
    kind = str(data.get("classification") or "").strip()
    if kind in CORRECTIONS:
        return kind
    note = str(data.get("correction_note") or "").lower()
    if any(word in note for word in ("evaluator", "loophole", "tolerance", "holdout", "cheat")):
        return "evaluator_loophole"
    if any(word in note for word in ("try order", "order", "stop", "playbook", "priority")):
        return "playbook"
    if any(word in note for word in ("superficial", "did not implement", "never landed",
                                     "not implemented", "looks like", "claims", "check")):
        return "verifier_check"
    return "antipattern"


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    kind = classify(data)
    gate = GATES[kind]
    entries = [entry for entry in (data.get("ledger") or []) if isinstance(entry, dict)]
    check = data.get("check") or {}
    note = str(data.get("correction_note") or "")
    exp_id = str(data.get("experiment_id") or "")
    proposal_id = str(data.get("proposal_id") or "P-1")
    problems: List[str] = []

    if not note:
        problems.append("no correction note: the proposal needs the human's own words")
    if not re.search(r"exp-[0-9]{4,}", exp_id):
        problems.append("no experiment id: a correction is always about one experiment")
    if kind == "verifier_check" and not check.get("predicate"):
        problems.append("a verifier check needs a predicate; a sentence cannot be replayed")

    replay_report: Optional[Dict[str, Any]] = None
    if kind == "verifier_check" and check.get("predicate"):
        if not check.get("checks_metric"):
            problems.append("a verifier check must name the canonical metric it reads")
        elif not metrics.is_known(str(check.get("checks_metric"))):
            problems.append(f"{check.get('checks_metric')} is not a canonical metric")
        replay_report = replay(check, entries)
        if not replay_report["passes"]:
            problems.append(
                "replay failed: a new check must catch the past cases of the same kind and must "
                "not flag experiments that were rightly confirmed")
            kind = "antipattern"
            gate = GATES["antipattern"]

    target = {
        "verifier_check": "kb/verifier/checks.md",
        "antipattern": "kb/antipatterns/",
        "playbook": str(data.get("playbook_target") or "playbooks/"),
        "evaluator_loophole": "evaluator/",
    }[kind]

    # `evaluator_loophole` is a classification, not a proposal kind: schemas/proposal.schema.json
    # has no evaluator kind, and a loophole is a defect in the contract the evaluator enforces
    # against a candidate, so it is proposed as a `contract` change with a human-only gate.
    proposal_kind = "contract" if kind == "evaluator_loophole" else kind
    proposal = {
        "id": proposal_id,
        "kind": proposal_kind,
        "title": str(data.get("title") or f"Correction from {exp_id or 'an experiment'}"),
        "rationale": (f"Human correction on {exp_id}: {note}".strip() if note else "missing note"),
        "target": target,
        "evidence": sorted({entry_id for entry_id in
                            [exp_id] + (replay_report["caught"] if replay_report else [])
                            if entry_id}),
        "status": "needs_human" if gate == "human_only" else ("draft" if not problems else "rejected"),
        "gate": gate,
        "author": "capture-correction",
    }
    return {
        "proposal": proposal,
        "classification": kind,
        "replay": replay_report,
        "check": _check_card(check, kind) if kind == "verifier_check" else None,
        "problems": problems,
        "ready": not problems,
        "goal": "each kind of human correction is needed only once",
    }


def _check_card(check: Dict[str, Any], kind: str) -> Dict[str, Any]:
    return {
        "id": str(check.get("id") or ""),
        "predicate": str(check.get("predicate")),
        "metric": metrics.canonical(str(check.get("checks_metric") or "")) or None,
        "band": check.get("band"),
        "direction": check.get("direction"),
        "contains": check.get("contains"),
        "field": check.get("field"),
        "tags": [str(tag) for tag in (check.get("tags") or [])],
        "statement": str(check.get("statement") or ""),
        "from_experiment": str(check.get("from_experiment") or ""),
        "kind": kind,
    }


def main(argv: List[str]) -> int:
    result = build(read_input(argv))
    json.dump(result, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
