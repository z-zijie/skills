"""Closure records: what a failed direction actually proved, and when it must be reopened.

The failure this module exists for is specific and it is not the one the retry loops catch. A
card is refuted, the direction leaves the priority order, and the campaign moves on — when what
actually happened was that a typo did not compile, an API was called with the wrong arity, a
probe's own baseline failed its check, or the one shape that was tried was the wrong point of a
parameter range. Every one of those is recorded as "this direction does not work", and none of
them is evidence about the direction at all.

So a refutation carries a closure record saying *why* it died:

``implementation``
    The mechanism never reached machine code: compile or link failure, wrong output, a change
    that routed around the hot path, a probe whose own check failed. Counts toward no rule and
    proves nothing about the direction.
``measurement``
    The mechanism landed and its counters moved, but the score did not. That is not a refutation
    at all: it is the most valuable outcome the system has, and the record is mislabelled.
``premise``
    The mechanism landed, the counters were measured, and they did not move as predicted. This is
    the only class that is negative evidence about the direction.
``unclassified``
    The verdict carries no checks, so nothing is known. Never admissible.

Two derived questions follow, and the campaign may only stop when both answer yes for every
closure:

* **admissible** — is this negative evidence about the mechanism at all? Only ``premise`` is.
* **retirable** — was the mechanism explored before it was written off? That needs at least
  ``MIN_POINTS`` trials along the direction's parameter, and no recorded condition under which
  it should be tried again.

A direction with any open closure is *resting*, not dead, and ``revisit_plan`` names the cheapest
experiment that would settle it.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

#: Why a direction was written off. Ordered from "proves nothing" to "proves something".
EVIDENCE_CLASSES = ("implementation", "measurement", "premise", "unclassified")

#: The one class that is evidence against the mechanism.
REFUTING_EVIDENCE = "premise"

#: A direction tried at fewer points than this was never swept; the refutation is provisional.
MIN_POINTS = 2

#: How many times one direction may be reopened before the campaign accepts the closure count
#: as its answer. Mirrors the two-retry bound the state machine puts on an implementation.
MAX_REOPENS = 2

#: Open closures that force a revisit even when nothing else asks for one.
MIN_OPEN_CLOSURES = 2

#: Evidence classes in the order a reopen is worth its experiment.
REOPEN_PRIORITY = {"implementation": 0, "premise": 1, "measurement": 2, "unclassified": 3}

#: Why a class says what it says, when the record carries no prose of its own.
CLASS_REASON = {
    "implementation": ("the mechanism never reached the machine code, so the closure says nothing "
                       "about the direction"),
    "measurement": ("the mechanism landed and its counters moved while the score did not: a "
                    "mislabelled confirmation, not a refutation"),
    "premise": ("the mechanism landed and the counters were measured, and they did not move as "
                "predicted"),
    "unclassified": ("no verdict checks were recorded, so whether the mechanism landed is unknown"),
}

_SLUG = re.compile(r"[^a-z0-9]+")
_WORDS = re.compile(r"[a-z0-9]+")


def direction_of(card: Optional[Dict[str, Any]], closure: Optional[Dict[str, Any]] = None) -> str:
    """The stable key one direction is filed under.

    A card's ``transformation`` is prose written fresh each round, so the same direction retried
    with a new sentence looks like a new direction and never accumulates. ``direction`` is the
    slug a card declares for exactly this reason; the prose remains the fallback for cards
    written before the field existed, where accumulation is approximate.
    """
    for value in ((closure or {}).get("direction"),
                  (card or {}).get("direction"),
                  (card or {}).get("transformation")):
        if isinstance(value, str) and value.strip():
            return slug(value)
    return "unknown"


def slug(text: str, limit: int = 64) -> str:
    """A stable, lowercase, hyphenated key from a phrase, cut at a word boundary.

    Cutting mid-word would make two different directions differ twice, once in their last word
    and once in where the cut fell, which is the opposite of what a stable key is for.
    """
    words = [word for word in _SLUG.sub("-", str(text).lower()).split("-") if word]
    key = ""
    for word in words:
        candidate = f"{key}-{word}" if key else word
        if len(candidate) > limit:
            break
        key = candidate
    return key or ("-".join(words))[:limit] or "unknown"


def classify(verdict_doc: Optional[Dict[str, Any]], entry: Optional[Dict[str, Any]] = None
             ) -> Dict[str, Any]:
    """Why this verdict is a closure, from the checks the verifier actually ran.

    The mapping is mechanical so that two readers of the same verdict cannot disagree: landed
    is whether every group A and B check passed, measured is whether every group C check passed,
    and the class follows from those two and the verdict word.
    """
    verdict_doc = verdict_doc or {}
    entry = entry or {}
    checks = [check for check in (verdict_doc.get("checks") or []) if isinstance(check, dict)]
    verdict = str(verdict_doc.get("verdict") or entry.get("verdict") or "")
    exited = [check for check in checks if check.get("group") in ("A", "B")]
    counters = [check for check in checks if check.get("group") == "C"]
    failed = [str(check.get("id")) for check in checks
              if str(check.get("conclusion")) in ("fail", "unknown")]
    landed = bool(exited) and all(str(check.get("conclusion")) == "pass" for check in exited)
    measured = bool(counters) and all(str(check.get("conclusion")) == "pass" for check in counters)

    if verdict == "not_implemented":
        evidence = "implementation"
        reason = ("the verdict is not_implemented: the mechanism never reached the machine code, "
                  "so nothing here is evidence about the direction")
    elif not checks:
        evidence = "unclassified"
        reason = ("the verdict carries no checks, so nothing here says whether the mechanism "
                  "landed; re-run the probe's own check before treating the direction as dead")
    elif not landed:
        evidence = "implementation"
        reason = (f"the mechanism never reached the machine code"
                  f"{' (' + ', '.join(failed) + ')' if failed else ''}; no evidence about the "
                  "direction")
    elif measured:
        evidence = "measurement"
        reason = ("the mechanism landed and its counters moved, but the score did not: the "
                  "record is a mislabelled confirmation and not a refutation")
    else:
        evidence = "premise"
        reason = (f"the mechanism landed and the counters were measured, and they did not move "
                  f"as predicted{' (' + ', '.join(failed) + ')' if failed else ''}")
    return {
        "evidence": evidence,
        "landed": landed,
        "reason": reason,
        "checks_failed": failed,
    }


def from_verdict(verdict_doc: Optional[Dict[str, Any]], card: Optional[Dict[str, Any]],
                 entry: Optional[Dict[str, Any]] = None,
                 declared: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the closure record a refuted or not-implemented experiment carries.

    ``evidence``, ``landed`` and ``reason`` are derived from the verdict and cannot be supplied
    by a caller: a record that can choose why it died can hide a compile failure behind the word
    refuted. The declared part is what the verifier knows and the checks do not — the parameter
    it varied, how many points it tried, and the condition under which it should be retried.
    """
    declared = declared or {}
    derived = classify(verdict_doc, entry)
    points = declared.get("points_tried")
    try:
        points = int(points) if points is not None else 1
    except (TypeError, ValueError):
        points = 1
    closure = {
        "direction": direction_of(card, {"direction": declared.get("direction")}),
        "evidence": derived["evidence"],
        "landed": derived["landed"],
        "reason": str(declared.get("reason") or derived["reason"]),
        "checks_failed": derived["checks_failed"],
        "points_tried": max(1, points),
        "parameters": [str(name) for name in (declared.get("parameters") or [])],
        "reopen_when": str(declared["reopen_when"]) if declared.get("reopen_when") else None,
        "reopened_by": [str(item) for item in (declared.get("reopened_by") or [])],
    }
    mistake = declared.get("evidence")
    if mistake is not None and str(mistake) != closure["evidence"]:
        closure["declared_evidence"] = str(mistake)
    return closure


def reason_of(closure: Optional[Dict[str, Any]], entry: Optional[Dict[str, Any]] = None) -> str:
    """The closure's own prose, or the class's default when a record carries none."""
    written = str((closure or {}).get("reason") or "").strip()
    if written:
        return written
    if closure is None:
        return classify(None, entry)["reason"]
    return CLASS_REASON.get(str(closure.get("evidence") or "unclassified"),
                            CLASS_REASON["unclassified"])


def is_admissible(closure: Optional[Dict[str, Any]]) -> bool:
    """Whether this closure is negative evidence about the mechanism."""
    return str((closure or {}).get("evidence") or "") == REFUTING_EVIDENCE


def is_retirable(closure: Optional[Dict[str, Any]], min_points: int = MIN_POINTS) -> bool:
    """Whether the direction may be written off: admissible, swept, and with no retry condition."""
    closure = closure or {}
    if not is_admissible(closure):
        return False
    if int(closure.get("points_tried") or 1) < int(min_points):
        return False
    return not str(closure.get("reopen_when") or "").strip()


def failure_entries(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The entries that closed something: a refutation, or an attempt that never landed."""
    return [entry for entry in entries
            if str(entry.get("verdict")) in ("refuted", "not_implemented")]


def closure_of(entry: Dict[str, Any],
               verdicts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The closure an entry carries, a record derived from its archived verdict, or one derived
    from the entry alone when the entry is a ``not_implemented``.

    ``not_implemented`` is the one verdict whose meaning *is* "the mechanism did not land", so it
    can be classified without its checks. ``refuted`` cannot: the word does not say whether the
    change reached machine code, and guessing there would hide exactly the closures this module
    exists to surface.
    """
    closure = entry.get("closure")
    if isinstance(closure, dict):
        return closure
    verdict_doc = (verdicts or {}).get(str(entry.get("exp_id")))
    if isinstance(verdict_doc, dict):
        return from_verdict(verdict_doc, entry.get("hypothesis"), entry)
    if str(entry.get("verdict")) == "not_implemented":
        return from_verdict(None, entry.get("hypothesis"), entry)
    return None


def by_direction(entries: Iterable[Dict[str, Any]],
                 verdicts: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Every failure, grouped by the direction it was filed under, oldest first."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for entry in failure_entries(entries):
        closure = closure_of(entry, verdicts) or {}
        direction = direction_of(entry.get("hypothesis"), closure)
        groups.setdefault(direction, []).append(entry)
    return groups


def summarise(entries: Iterable[Dict[str, Any]],
              verdicts: Optional[Dict[str, Any]] = None,
              min_points: int = MIN_POINTS) -> Dict[str, Any]:
    """Every closure classified, with the counts a report has to defend."""
    rows = []
    counts = {name: 0 for name in EVIDENCE_CLASSES}
    for entry in failure_entries(entries):
        closure = closure_of(entry, verdicts)
        evidence = str((closure or {}).get("evidence") or "unclassified")
        counts[evidence] = counts.get(evidence, 0) + 1
        rows.append({
            "exp_id": str(entry.get("exp_id")),
            "direction": direction_of(entry.get("hypothesis"), closure),
            "verdict": str(entry.get("verdict")),
            "evidence": evidence,
            "admissible": is_admissible(closure),
            "retirable": is_retirable(closure, min_points),
            "points_tried": int((closure or {}).get("points_tried") or 1),
            "reason": reason_of(closure, entry),
            "reopen_when": (closure or {}).get("reopen_when"),
        })
    open_rows = [row for row in rows if not row["retirable"]]
    # A direction with two unresolved attempts is one direction to reopen, not two: the count that
    # triggers a revisit and the count a report names are both directions.
    open_directions = []
    for row in open_rows:
        if row["direction"] not in open_directions:
            open_directions.append(row["direction"])
    retirable = [row for row in rows if row["retirable"]]
    return {
        "closures": rows,
        "counts": counts,
        "open": open_rows,
        "open_directions": open_directions,
        "retirable": retirable,
        "admissible": counts.get(REFUTING_EVIDENCE, 0),
    }


def _expected_speedup(entry: Dict[str, Any]) -> float:
    predicted = ((entry.get("hypothesis") or {}).get("predictions") or {}).get("speedup") or {}
    try:
        return float(predicted.get("expect") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _reopens_used(group: List[Dict[str, Any]]) -> int:
    """How many times this direction has already been retried after being written off."""
    extra = 0
    for entry in group:
        closure = entry.get("closure") or {}
        extra += len(closure.get("reopened_by") or [])
    return max(extra, len(group) - 1)


def _retry_for(evidence: str, points_tried: int, min_points: int) -> str:
    if evidence == "implementation":
        return "fix_implementation"
    if evidence == "premise" and points_tried < min_points:
        return "widen_sweep"
    if evidence == "measurement":
        return "bank_and_recheck"
    return "classify_then_retry"


def _why(evidence: str, points_tried: int, min_points: int) -> str:
    if evidence == "implementation":
        return ("the failing artefact was never exercised on its own, so a compile, link, arity "
                "or layout defect is indistinguishable from a mechanism that does not pay")
    if evidence == "premise" and points_tried < min_points:
        return (f"the mechanism landed and was measured at {points_tried} point of its parameter, "
                f"below the {min_points} a refutation of the direction needs")
    if evidence == "measurement":
        return ("the mechanism was confirmed and the score did not follow: the patch is worth "
                "banking and re-testing once the kernel's share of case time rises")
    return ("no verdict checks were recorded, so the closure word is unsupported and the "
            "direction's status is unknown rather than dead")


def _questions(evidence: str, closure: Dict[str, Any], min_points: int) -> List[str]:
    questions = ["did the change land, and does the failing artefact pass its own check alone?"]
    if evidence == "premise":
        questions.append(
            f"was the parameter swept, or was one point of it tried ({closure.get('points_tried')}"
            f" of {min_points} points)?")
        questions.append(
            "can the search space be widened — a finer control granularity, a different launch "
            "shape, another API form, a different precision or layout?")
    elif evidence == "measurement":
        questions.append("is this direction dead, or simply too small a share of case time to "
                         "move the score today?")
    else:
        questions.append("what does the archived artefact say if it is re-run unchanged?")
    return questions


def revisit_plan(entries: Iterable[Dict[str, Any]], verdicts: Optional[Dict[str, Any]] = None,
                 stalled_rounds: int = 0, proposed_action: Optional[str] = None,
                 revisit_after_stalled: int = 3, min_open: int = MIN_OPEN_CLOSURES,
                 min_points: int = MIN_POINTS, max_reopens: int = MAX_REOPENS,
                 max_reopen_cards: int = 3) -> Dict[str, Any]:
    """Decide whether the campaign owes a revisit, and which closures to reopen first.

    Pure over its input, so the same ledger yields the same plan and a campaign that is about to
    stop can be checked against the closures it is about to call exhausted.
    """
    entries = list(entries)
    summary = summarise(entries, verdicts, min_points)
    triggers: List[str] = []
    if proposed_action in ("stop", "change_direction"):
        triggers.append(f"the campaign proposes {proposed_action}")
    if int(stalled_rounds) >= int(revisit_after_stalled):
        triggers.append(f"{int(stalled_rounds)} rounds without a significant gain "
                        f"(threshold {int(revisit_after_stalled)})")
    if len(summary["open_directions"]) >= int(min_open):
        triggers.append(f"{len(summary['open_directions'])} open closures "
                        f"(threshold {int(min_open)})")
    triggered = bool(triggers)

    groups = by_direction(entries, verdicts)
    open_by_direction: Dict[str, List[Dict[str, Any]]] = {}
    for row in summary["open"]:
        open_by_direction.setdefault(row["direction"], []).append(row)
    candidates = []
    capped = []
    for direction, rows in open_by_direction.items():
        group = groups.get(direction, [])
        used = _reopens_used(group)
        row = rows[-1]
        entry = row_entry(entries, row["exp_id"])
        closure = closure_of(entry, verdicts) or {}
        item = {
            "direction": direction,
            "experiment": row["exp_id"],
            "experiments": [str(member.get("exp_id")) for member in group],
            "evidence": row["evidence"],
            "retry": _retry_for(row["evidence"], row["points_tried"], min_points),
            "why_reopen": _why(row["evidence"], row["points_tried"], min_points),
            "first_check": reason_of(closure),
            "parameters": list(closure.get("parameters") or []),
            "sweep": ({"points_tried": row["points_tried"], "min_points": int(min_points)}
                      if row["evidence"] == "premise" else None),
            "refutation_would_be": (
                closure.get("reopen_when")
                or "the mechanism lands, the counters are measured, and they still do not move"),
            "questions": _questions(row["evidence"], closure, min_points),
            "reopens_used": used,
            "priority": REOPEN_PRIORITY.get(row["evidence"], 9),
            "expected_speedup": _expected_speedup(entry),
        }
        if used >= int(max_reopens):
            capped.append(dict(item, cap=int(max_reopens)))
        else:
            candidates.append(item)
    candidates.sort(key=lambda item: (item["priority"], -item["expected_speedup"], item["experiment"]))
    reopens = candidates[:max(0, int(max_reopen_cards))] if triggered else []

    if not triggered:
        decision = "not_triggered"
    elif reopens:
        decision = "reopen"
    elif summary["open"]:
        decision = "hold"
    else:
        decision = "exhausted"

    return {
        "triggered": triggered,
        "triggers": triggers,
        "decision": decision,
        "reopens": reopens,
        "reopen_directions": [item["direction"] for item in reopens],
        "reopen_retries": [item["retry"] for item in reopens],
        "capped": capped,
        "capped_directions": [item["direction"] for item in capped],
        "closures": summary["closures"],
        "counts": summary["counts"],
        "open": summary["open"],
        "open_directions": summary["open_directions"],
        "retirable": summary["retirable"],
        "retirable_directions": [row["direction"] for row in summary["retirable"]],
        "note": _note(decision, summary["counts"], len(reopens),
                      len(summary["open_directions"])),
    }


def row_entry(entries: Iterable[Dict[str, Any]], exp_id: str) -> Dict[str, Any]:
    """The entry behind one closure row."""
    for entry in entries:
        if str(entry.get("exp_id")) == str(exp_id):
            return entry
    return {}


def _note(decision: str, counts: Dict[str, int], reopened: int, still_open: int) -> str:
    if decision == "not_triggered":
        return "no revisit is due: the campaign is not stopping and the closure count is below threshold"
    if decision == "reopen":
        return (f"{reopened} direction(s) reopen before anything is written off; "
                f"{counts.get('implementation', 0)} closure(s) never reached the machine code and "
                f"{counts.get('measurement', 0)} showed a confirmed mechanism with no score")
    if decision == "hold":
        return (f"{still_open} closure(s) stay unresolved and every one of them has used its "
                "reopens: report them as unresolved rather than as refuted")
    return "every closure is admissible, swept and carries no retry condition: exhaustion is honest"
