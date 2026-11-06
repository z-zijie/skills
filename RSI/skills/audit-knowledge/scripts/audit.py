#!/usr/bin/env python3
"""Find knowledge-base entries that conflict, are stale, or lack evidence.

Reads a snapshot of the stores as skill input and prints findings plus proposals. Findings are
mechanical: each has a rule, a severity and the entry paths behind it. Proposals are split by
the gate that would have to accept them, because a stale-evidence demotion and a threshold edit
travel different paths and only one of them is mechanical.

Nothing is written to a store. The caller writes the proposals under ``kb/proposals/``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import knowledge, metrics  # noqa: E402

FINDINGS = (
    "conflict",
    "stale",
    "thin_evidence",
    "no_mechanism",
    "unbound_metric",
    "legacy_metric",
    "orphan_evidence",
    "missed_counterexample",
)
#: An entry not verified for this long is stale, unless calibration says otherwise.
STALE_AFTER_DAYS = 90


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


def rule_key(rule: Dict[str, Any]) -> str:
    return str(rule.get("id") or rule.get("_path") or "")


def scope_sig(rule: Dict[str, Any]) -> tuple:
    scope = rule.get("scope") or {}
    return (tuple(sorted(scope.get("hw") or [])), tuple(sorted(scope.get("tags_all") or [])),
            tuple(sorted(scope.get("classes") or [])))


def days_since(stamp: Optional[str], today: dt.date) -> Optional[int]:
    if not stamp:
        return None
    try:
        then = dt.date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return None
    return (today - then).days


_CLAIM_WORDS = __import__("re").compile(r"[a-z0-9_]{4,}")


def _same_claim(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """Whether two rules assert the same thing, judged by the transformation words they use."""
    def words(rule: Dict[str, Any]) -> set:
        return set(_CLAIM_WORDS.findall(str(rule.get("statement") or "").lower()))

    left_words, right_words = words(left), words(right)
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words)
    return overlap / max(len(left_words), len(right_words)) >= 0.8


def _stronger(left: Dict[str, Any], right: Dict[str, Any]) -> tuple:
    def weight(rule: Dict[str, Any]) -> tuple:
        evidence = rule.get("evidence") or {}
        return (len(evidence.get("confirmed") or []), len(evidence.get("refuted") or []))

    return (left, right) if weight(left) >= weight(right) else (right, left)


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    today = dt.date.fromisoformat(str(data.get("today") or dt.date.today().isoformat()))
    stale_after = int(data.get("stale_after_days") or STALE_AFTER_DAYS)
    rules = [rule for rule in (data.get("rules") or []) if isinstance(rule, dict)]
    antipatterns = [entry for entry in (data.get("antipatterns") or []) if isinstance(entry, dict)]
    patterns = [entry for entry in (data.get("patterns") or []) if isinstance(entry, dict)]
    conjectures = [entry for entry in (data.get("conjectures") or []) if isinstance(entry, dict)]
    calibration = data.get("calibration") or {}
    ledger_ids = set(str(exp) for exp in (data.get("ledger_exp_ids") or []))

    findings: List[Dict[str, Any]] = []
    verified_by_ledger = 0

    def add(kind: str, entry: str, detail: str, severity: str, proposal: Dict[str, Any]) -> None:
        findings.append({
            "kind": kind,
            "entry": entry,
            "detail": detail,
            "severity": severity,
            "proposal": proposal,
        })

    # -- rules --------------------------------------------------------------
    by_scope: Dict[tuple, List[Dict[str, Any]]] = {}
    for rule in rules:
        by_scope.setdefault(scope_sig(rule), []).append(rule)

    for rule in rules:
        path = rule_key(rule)
        evidence = rule.get("evidence") or {}
        confirmed = [str(exp) for exp in (evidence.get("confirmed") or [])]
        refuted = [str(exp) for exp in (evidence.get("refuted") or [])]
        error = rule.get("mean_prediction_error")
        age = days_since(rule.get("last_verified"), today)

        if age is not None and age > stale_after:
            add("stale", path,
                f"last verified {age} days ago, older than {stale_after}",
                "medium",
                {"kind": "rule", "action": "demote", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": "under_review",
                 "reason": f"not verified for {age} days"})

        if not confirmed:
            add("thin_evidence", path,
                "no confirmation at all: this is a conjecture wearing a rule id",
                "high",
                {"kind": "rule", "action": "retire", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": "deprecated",
                 "reason": "zero confirmations"})
        elif len(confirmed) < knowledge.ESTABLISHED_MIN_CONFIRMATIONS and rule.get("status") == "established":
            add("thin_evidence", path,
                f"marked established on {len(confirmed)} confirmation(s); the life cycle needs "
                f"{knowledge.ESTABLISHED_MIN_CONFIRMATIONS} across {knowledge.ESTABLISHED_MIN_OPERATORS} operators",
                "high",
                {"kind": "rule", "action": "demote", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": "candidate",
                 "reason": "evidence does not support the stored status"})

        if error is not None and float(error) > knowledge.PREDICTION_ERROR_HOLE:
            add("stale", path,
                f"mean prediction error {float(error):.2f} is above the hole threshold "
                f"{knowledge.PREDICTION_ERROR_HOLE}: the scope is too wide or the evidence is old",
                "high",
                {"kind": "rule", "action": "narrow_scope", "target": "kb/rules/",
                 "gate": "offline_ledger_replay", "to_status": "under_review",
                 "reason": "prediction error above the hole threshold"})

        unbound = [name for name in [rule.get("effect", {}).get("metric")]
                   + list(rule.get("expected_counters") or [])
                   if name and not metrics.is_known(str(name))]
        if unbound:
            add("unbound_metric", path,
                f"names {sorted(set(unbound))}, which is not a canonical metric",
                "high",
                {"kind": "rule", "action": "fix_metric", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": rule.get("status"),
                 "reason": "a rule must cite a canonical metric"})

        legacy = [str(name) for name in [rule.get("effect", {}).get("metric")]
                  + list(rule.get("expected_counters") or [])
                  if name and metrics.is_known(str(name)) and metrics.canonical(str(name)) != name]
        if legacy:
            add("legacy_metric", path,
                f"uses legacy spellings {sorted(set(legacy))} rather than the canonical names",
                "low",
                {"kind": "rule", "action": "fix_metric", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": rule.get("status"),
                 "reason": "a rule must be readable after a device change"})

        if not rule.get("mechanism"):
            add("no_mechanism", path,
                "no mechanism: a hypothesizer cannot write a falsifiable card from it",
                "medium",
                {"kind": "rule", "action": "require_mechanism", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": rule.get("status"),
                 "reason": "mechanism missing"})

        missing = sorted({exp for exp in confirmed + refuted if ledger_ids and exp not in ledger_ids})
        if missing:
            add("orphan_evidence", path,
                f"cites {missing}, which are not in the ledger window supplied",
                "medium",
                {"kind": "rule", "action": "demote", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": "candidate",
                 "reason": "evidence ids could not be located"})
        if ledger_ids and set(confirmed) <= ledger_ids:
            verified_by_ledger += 1

        if refuted and not rule.get("counter_example_note"):
            add("missed_counterexample", path,
                f"carries {len(refuted)} refutation(s) with no counter-example note",
                "low",
                {"kind": "rule", "action": "annotate", "target": "kb/rules/",
                 "gate": "mechanical", "to_status": rule.get("status"),
                 "reason": "refutations without an explanation get re-learned"})

    for scope, group in sorted(by_scope.items()):
        if len(group) < 2:
            continue
        # Two rules conflict when they claim the same scope and the same metric but point in
        # opposite directions, or when they state the same claim twice. Different
        # transformations over one scope are complementary, not contradictory.
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                left_effect, right_effect = left.get("effect") or {}, right.get("effect") or {}
                same_metric = (left_effect.get("metric") and
                               metrics.canonical(str(left_effect.get("metric")))
                               == metrics.canonical(str(right_effect.get("metric"))))
                opposed = (same_metric
                           and left_effect.get("direction") != right_effect.get("direction")
                           and left_effect.get("direction") and right_effect.get("direction"))
                duplicated = _same_claim(left, right)
                if not (opposed or duplicated):
                    continue
                winner, loser = _stronger(left, right)
                reason = ("the two rules claim opposite directions for one metric under one scope"
                          if opposed else "the two rules state the same claim under one scope")
                add("conflict", rule_key(loser),
                    f"{reason}; merge with {rule_key(winner)}",
                    "high",
                    {"kind": "rule", "action": "merge", "target": "kb/rules/",
                     "gate": "mechanical", "to_status": None,
                     "group": f"merge:{rule_key(winner)}",
                     "reason": f"merge into {rule_key(winner)}, which has the most evidence"})

    # -- other stores -------------------------------------------------------
    for entry in patterns:
        path = str(entry.get("id") or entry.get("_path") or "")
        if not entry.get("mechanism"):
            add("no_mechanism", path,
                "pattern without a mechanism: the hypothesizer cannot predict counters from it",
                "medium",
                {"kind": "pattern", "action": "annotate", "target": "kb/patterns/",
                 "gate": "mechanical", "to_status": None, "reason": "mechanism missing"})

    stale_params = [name for name in (calibration.get("changed_parameters") or [])]
    if stale_params:
        for rule in rules:
            if rule.get("status") in ("established", "candidate"):
                add("stale", rule_key(rule),
                    f"depends on measured parameters that moved: {sorted(stale_params)}",
                    "high",
                    {"kind": "rule", "action": "demote", "target": "kb/rules/",
                     "gate": "mechanical", "to_status": "under_review",
                     "reason": "bound hardware changed"})

    for entry in conjectures:
        path = str(entry.get("id") or entry.get("_path") or "")
        age = days_since(entry.get("created"), today)
        if age is not None and age > stale_after:
            add("stale", path,
                f"conjecture open for {age} days with no experiment attached",
                "low",
                {"kind": "conjecture", "action": "close", "target": "kb/conjectures/",
                 "gate": "human_only", "to_status": None,
                 "reason": "open conjecture past its review date"})

    proposals = _proposals(findings, data)
    return {
        "today": today.isoformat(),
        "stores_audited": {
            "rules": len(rules),
            "antipatterns": len(antipatterns),
            "patterns": len(patterns),
            "conjectures": len(conjectures),
        },
        "findings": findings,
        "proposals": proposals,
        "counts": {
            "findings": len(findings),
            "by_kind": {kind: sum(1 for item in findings if item["kind"] == kind)
                        for kind in FINDINGS},
            "by_severity": {level: sum(1 for item in findings if item["severity"] == level)
                            for level in ("high", "medium", "low")},
            "proposals": len(proposals),
            "mechanical": sum(1 for item in proposals if item["gate"] == "mechanical"),
            "needs_replay": sum(1 for item in proposals if item["gate"] != "mechanical"),
        },
        "health": {
            "established_rules": sum(1 for rule in rules if rule.get("status") == "established"),
            "rules_with_evidence_in_window": verified_by_ledger,
            "mean_evidence_per_rule": round(
                sum(len((rule.get("evidence") or {}).get("confirmed") or []) for rule in rules)
                / len(rules), 4) if rules else None,
            "deprecated_share": round(
                sum(1 for rule in rules if rule.get("status") == "deprecated") / len(rules), 4
            ) if rules else None,
        },
        "notes": _notes(findings, rules),
    }


def _proposals(findings: List[Dict[str, Any]], data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Group findings into proposal-shaped objects, one per entry and action."""
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for finding in findings:
        plan = finding["proposal"]
        key = (plan["kind"], plan["action"], plan.get("group") or finding["entry"])
        item = grouped.setdefault(key, {
            "kind": plan["kind"],
            "action": plan["action"],
            "target": plan["target"],
            "gate": plan["gate"],
            "to_status": plan["to_status"],
            "entries": [],
            "reasons": [],
            "evidence": [],
        })
        item["entries"].append(finding["entry"])
        item.setdefault("group", plan.get("group"))
        item["reasons"].append(finding["detail"])
    start = int(data.get("next_proposal_id") or 1)
    out = []
    def worst_severity(item: Dict[str, Any]) -> int:
        levels = [{"high": 0, "medium": 1, "low": 2}.get(finding["severity"], 3)
                  for finding in findings if finding["entry"] in item["entries"]]
        return min(levels) if levels else 3

    for index, item in enumerate(sorted(grouped.values(),
                                         key=lambda entry: (worst_severity(entry), entry["kind"],
                                                            entry["action"],
                                                            entry["entries"][0]))):
        item["reasons"] = sorted(set(item["reasons"]))
        item["entries"] = sorted(set(item["entries"]))
        item["id"] = f"P-{start + index}"
        item["rationale"] = "; ".join(item["reasons"])
        item["evidence"] = sorted({str(exp) for finding in findings
                                   if finding["entry"] in item["entries"]
                                   for exp in _evidence_of(finding, data)})
        item["status"] = "needs_human" if item["gate"] == "human_only" else "draft"
        out.append(item)
    return out


def _evidence_of(finding: Dict[str, Any], data: Dict[str, Any]) -> List[str]:
    for rule in (data.get("rules") or []):
        if str(rule.get("id") or rule.get("_path")) == finding["entry"]:
            evidence = rule.get("evidence") or {}
            return [str(exp) for exp in (evidence.get("confirmed") or [])
                    + (evidence.get("refuted") or [])]
    return []


def _notes(findings: List[Dict[str, Any]], rules: List[Dict[str, Any]]) -> List[str]:
    notes: List[str] = []
    kinds = {finding["kind"] for finding in findings}
    if not findings:
        notes.append("no findings: the window is consistent with its own evidence")
        return notes
    if "conflict" in kinds:
        notes.append("two rules with one scope and different statements cannot both retrieve; "
                     "merge them or narrow one")
    if "orphan_evidence" in kinds:
        notes.append("evidence ids missing from the ledger window mean the rule cannot be "
                     "re-derived, so demote before trusting it")
    if "unbound_metric" in kinds:
        notes.append("a rule citing a non-canonical metric will not survive a device change")
    if all(rule.get("status") != "established" for rule in rules) and rules:
        notes.append("no established rules yet: retrieval will return thin evidence, which is "
                     "honest but slow, so prefer narrowing scopes to promoting cards")
    return notes


def main(argv: List[str]) -> int:
    json.dump(build(read_input(argv)), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
