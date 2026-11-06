#!/usr/bin/env python3
"""Cluster an operator's ledger into failure patterns.

Reads the skill input on stdin (or --input) and prints a failure report. The clustering is
deterministic: a failure's cluster is decided by mechanical rules over the entry's verdict,
prediction error, attempt history and diagnosis, so the same ledger always yields the same
clusters and two scaffold versions can be compared offline without a device.

Clusters, in the order of the build plan's list:

* ``not_implemented``   the change never reached the machine code
* ``large_prediction_error``  the mechanism was measured, but not at the size predicted
* ``open_closure``      refuted without evidence about the direction: it never landed, it landed
                        and never moved, or one point of a parameter was tried
* ``dead_end``          the same direction refuted more than once, each time for real
* ``wrong_diagnosis``   confirmed mechanism, no speed: the signature was wrong
* ``unexplained_speedup``  faster, but not for the reason the card gave
* ``correctness``       the candidate did not pass the correctness check
* ``implausible``       speedup above the review cap; excluded from evidence until reviewed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import closures, knowledge  # noqa: E402

CLUSTERS = (
    "not_implemented",
    "open_closure",
    "large_prediction_error",
    "dead_end",
    "wrong_diagnosis",
    "unexplained_speedup",
    "correctness",
    "implausible",
)
#: A direction refuted this many times for one operator is a dead end.
DEAD_END_REFUTATIONS = 2


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


def transformation_of(entry: Dict[str, Any]) -> str:
    return str((entry.get("hypothesis") or {}).get("transformation") or "unknown")


def signature_tags(entry: Dict[str, Any]) -> List[str]:
    tags = (entry.get("hypothesis") or {}).get("signature_tags")
    if tags:
        return [str(tag) for tag in tags]
    return [str(tag) for tag in ((entry.get("signature") or {}).get("tags") or [])]


def failure_kind(entry: Dict[str, Any], refutations: Dict[str, int], hole: float,
                 min_points: int = closures.MIN_POINTS) -> Optional[str]:
    """The single mechanical cluster this entry belongs to, or None when it is a plain win."""
    if entry.get("implausible"):
        return "implausible"
    if (entry.get("correctness") or {}).get("passed") is False:
        return "correctness"
    verdict = entry.get("verdict")
    if verdict == "not_implemented":
        return "not_implemented"
    if verdict == "confirmed" and entry.get("significant") is False:
        return "wrong_diagnosis"
    if verdict == "refuted" and entry.get("significant") is True:
        return "unexplained_speedup"
    if verdict == "refuted":
        # A dead end is a direction that was *refuted*, and only an admissible closure refutes
        # anything. A refutation whose mechanism never landed, whose verdict recorded a confirmed
        # mechanism the score did not follow, or that tried one point of a parameter is *open*: it
        # must not be retired here, or the outer loop quantifies over closures that are not
        # evidence and edits a playbook on the strength of compile failures.
        closure = closures.closure_of(entry, None)
        if not closures.is_retirable(closure, min_points):
            return "open_closure"
        if refutations.get(transformation_of(entry), 0) >= DEAD_END_REFUTATIONS:
            return "dead_end"
        return None
    error = entry.get("prediction_error")
    if verdict == "confirmed" and error is not None and float(error) > hole:
        return "large_prediction_error"
    return None


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    entries = [entry for entry in (data.get("entries") or []) if isinstance(entry, dict)]
    hole = float(data.get("prediction_error_hole") or knowledge.PREDICTION_ERROR_HOLE)

    min_points = int(data.get("min_points") or closures.MIN_POINTS)
    refutations: Dict[str, int] = {}
    for entry in entries:
        if entry.get("verdict") == "refuted":
            name = transformation_of(entry)
            refutations[name] = refutations.get(name, 0) + 1

    groups: Dict[str, List[Dict[str, Any]]] = {kind: [] for kind in CLUSTERS}
    unattributed: List[str] = []
    for entry in entries:
        kind = failure_kind(entry, refutations, hole, min_points)
        if kind is None:
            continue
        groups[kind].append({
            "exp_id": str(entry.get("exp_id")),
            "transformation": transformation_of(entry),
            "signature_tags": signature_tags(entry),
            "verdict": entry.get("verdict"),
            "speedup": entry.get("speedup"),
            "prediction_error": entry.get("prediction_error"),
            "note": _note_for(kind, entry, refutations),
        })
        if not signature_tags(entry):
            unattributed.append(str(entry.get("exp_id")))

    clusters = []
    for kind in CLUSTERS:
        members = groups[kind]
        if not members:
            continue
        clusters.append({
            "kind": kind,
            "count": len(members),
            "experiments": [member["exp_id"] for member in members],
            "transformations": sorted({member["transformation"] for member in members}),
            "tags": sorted({tag for member in members for tag in member["signature_tags"]}),
            "share": round(len(members) / len(entries), 4) if entries else 0.0,
            "summary": _summary_for(kind, members),
        })
    clusters.sort(key=lambda cluster: (-cluster["count"], cluster["kind"]))

    rounds = [entry for entry in entries if entry.get("verdict") in ("confirmed", "refuted")]
    confirmed = sum(1 for entry in rounds if entry.get("verdict") == "confirmed")
    attempts = len(entries)
    not_implemented = groups["not_implemented"]
    return {
        "op": str(data.get("op") or (entries[0].get("op") if entries else "")),
        "entries_seen": len(entries),
        "rounds_seen": len(rounds),
        "clusters": clusters,
        "top_cluster": clusters[0]["kind"] if clusters else None,
        "rates": {
            "not_implemented_rate": round(len(not_implemented) / attempts, 4) if attempts else None,
            "confirmation_rate": round(confirmed / len(rounds), 4) if rounds else None,
            "mean_prediction_error": _mean(
                [float(entry["prediction_error"]) for entry in rounds
                 if entry.get("prediction_error") is not None]),
            "dead_end_transformations": sorted(
                name for name, count in refutations.items()
                if count >= DEAD_END_REFUTATIONS and _swept(entries, name, min_points)),
            "open_closure_rate": round(len(groups["open_closure"]) / attempts, 4) if attempts else None,
        },
        "prediction_error_hole": hole,
        "unattributed": unattributed,
        "notes": _notes(entries, groups, refutations),
    }


def _swept(entries: List[Dict[str, Any]], transformation: str, min_points: int) -> bool:
    """Whether at least one refutation of this direction was an admissible, swept closure."""
    for entry in entries:
        if entry.get("verdict") != "refuted" or transformation_of(entry) != transformation:
            continue
        if closures.is_retirable(closures.closure_of(entry), min_points):
            return True
    return False


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _note_for(kind: str, entry: Dict[str, Any], refutations: Dict[str, int]) -> str:
    if kind == "not_implemented":
        return "the change did not reach the machine code; counts toward no rule"
    if kind == "large_prediction_error":
        error = entry.get("prediction_error")
        return f"prediction error {float(error):.2f} is above the hole threshold"
    if kind == "dead_end":
        count = refutations.get(transformation_of(entry), 0)
        return f"{transformation_of(entry)} refuted {count} times for this operator"
    if kind == "open_closure":
        closure = closures.closure_of(entry) or {}
        return (f"refuted on {closures.reason_of(closure, entry)}: reopen before retiring the "
                "direction")
    if kind == "wrong_diagnosis":
        return "mechanism confirmed and the kernel did not move: the diagnosis was wrong"
    if kind == "unexplained_speedup":
        return "faster without the predicted mechanism: record the real cause first"
    if kind == "correctness":
        failures = (entry.get("correctness") or {}).get("failing_cases") or []
        return f"correctness failed on {len(failures)} case(s)"
    if kind == "implausible":
        return "speedup above the review cap; excluded until a human reviews it"
    return ""


def _summary_for(kind: str, members: List[Dict[str, Any]]) -> str:
    names = ", ".join(sorted({member["transformation"] for member in members}))
    if kind == "not_implemented":
        return f"{len(members)} attempt(s) never reached the machine code: {names}"
    if kind == "large_prediction_error":
        errors = [member["prediction_error"] for member in members
                  if member["prediction_error"] is not None]
        return (f"{len(members)} confirmed change(s) missed their predicted size "
                f"(mean error {_mean([float(e) for e in errors]):.2f}): {names}")
    if kind == "dead_end":
        return f"{len(members)} refutation(s) of exhausted directions: {names}"
    if kind == "open_closure":
        return (f"{len(members)} refutation(s) that are not evidence about the direction: {names}")
    if kind == "wrong_diagnosis":
        return f"{len(members)} confirmed mechanism(s) with no gain: {names}"
    if kind == "unexplained_speedup":
        return f"{len(members)} unexplained speedup(s): {names}"
    if kind == "correctness":
        return f"{len(members)} candidate(s) failed correctness: {names}"
    return f"{len(members)} implausible result(s): {names}"


def _notes(entries: List[Dict[str, Any]], groups: Dict[str, List[Dict[str, Any]]],
           refutations: Dict[str, int]) -> List[str]:
    notes: List[str] = []
    if not entries:
        notes.append("no ledger entries: nothing to cluster yet")
        return notes
    if groups["not_implemented"]:
        notes.append(
            "not-implemented attempts are an implementer or card-clarity problem, not evidence "
            "against the mechanism; fix the card before changing the playbook")
    if groups["wrong_diagnosis"]:
        notes.append(
            "confirmed mechanisms that did not speed the kernel point at the signature, so "
            "check signature thresholds before adding rules")
    if groups["implausible"]:
        notes.append("implausible results are excluded from evidence until a human reviews them")
    if refutations:
        worst = sorted(refutations.items(), key=lambda pair: (-pair[1], pair[0]))[0]
        if worst[1] >= DEAD_END_REFUTATIONS:
            notes.append(f"{worst[0]} is refuted {worst[1]} times: retire it from the priority order")
    return notes


def main(argv: List[str]) -> int:
    json.dump(build(read_input(argv)), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
