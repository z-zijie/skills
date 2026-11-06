#!/usr/bin/env python3
"""propose-hypothesis: the gate a proposed card passes before it reaches the implementer.

The cards come from the model; this script holds the four checks that must not be a judgement:

* every named counter is a canonical metric, so the verifier can find it in a profile;
* no card names a metric this device cannot produce, because that card could never be confirmed;
* every card validates against ``schemas/hypothesis.schema.json``;
* a card whose predicted gain sits inside the measured noise floor is flagged, since a gain that
  small cannot be separated from run-to-run variation.

Ranking is deterministic and documented. Mechanism strength is not scoreable by a script, so the
ranking uses what can be compared: the predicted speedup's lower bound first, because that is the
expected payoff per experiment and it is the one number a card commits to; then how many retrieved
entries or past experiments it cites; then how many counters it names, since each one is a chance
for the verifier to observe the mechanism. Ties keep the model's own order, and a card whose lower
bound sits inside the measured noise floor is flagged in `notes` rather than silently demoted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, metrics, read_request  # noqa: E402

MAX_CARDS = 3


def check_cards(cards: List[Any], unavailable: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Split proposed cards into usable cards and rejections that carry a reason."""
    schema = jsonschema_lite.load_schema(str(ROOT / "schemas" / "hypothesis.schema.json"))
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    blocked = {metrics.canonical(name) for name in unavailable}
    for card in cards:
        if not isinstance(card, dict):
            rejected.append({"transformation": "?", "reason": "the card is not an object"})
            continue
        label = str(card.get("transformation") or card.get("id") or "?")
        named = [str(item.get("metric")) for item in
                 ((card.get("predictions") or {}).get("counters") or []) if isinstance(item, dict)]
        unknown = metrics.check_names(named)
        if unknown:
            rejected.append({"transformation": label, "metric": unknown[0],
                             "reason": f"the card names {unknown}, which the canonical registry does "
                                       "not define; a counter nobody can name cannot be verified"})
            continue
        unusable = sorted({metrics.canonical(item) for item in named} & blocked)
        if unusable:
            rejected.append({"transformation": label, "metric": unusable[0],
                             "reason": f"the card names {unusable}, which this device or profiler "
                                       "cannot produce, so the mechanism could never be confirmed"})
            continue
        errors = jsonschema_lite.validate(card, schema)
        if errors:
            rejected.append({"transformation": label,
                             "reason": "the card does not satisfy the hypothesis contract: "
                                       + "; ".join(errors[:3])})
            continue
        accepted.append(card)
    return {"accepted": accepted, "rejected": rejected}


def signals(card: Dict[str, Any], noise: float) -> Dict[str, Any]:
    """The comparable properties of one card, with the band they produce."""
    based_on = len(card.get("based_on") or [])
    speedup = (card.get("predictions") or {}).get("speedup") or {}
    low = float(speedup.get("low") or 0.0)
    counters = len(((card.get("predictions") or {}).get("counters") or []))
    return {"grounded": based_on, "clears_noise": bool(noise and low > noise),
            "counter_count": counters, "predicted_low": low}


def rank(cards: List[Dict[str, Any]], noise: float) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Order the cards, keeping the incoming order inside an identical band."""
    scored = [(index, card, signals(card, noise)) for index, card in enumerate(cards)]

    def key(item: Tuple[int, Dict[str, Any], Dict[str, Any]]) -> Tuple[float, int, int, int]:
        index, _card, signal = item
        return (-signal["predicted_low"], -int(signal["grounded"] > 0),
                -int(signal["counter_count"]), index)

    ordered = sorted(scored, key=key)
    out: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for position, (_index, card, signal) in enumerate(ordered, start=1):
        card = dict(card)
        card["priority"] = position
        out.append((card, signal))
    return out


def explain(ordered: List[Tuple[Dict[str, Any], Dict[str, Any]]], noise: float) -> List[str]:
    notes: List[str] = []
    for card, signal in ordered:
        if noise and not signal["clears_noise"]:
            notes.append(f"{card.get('id')}: predicted low {signal['predicted_low']:g} does not clear "
                         f"the measured noise floor {noise:g}; a gain that small is not separable "
                         "from run-to-run variation")
        if not signal["grounded"]:
            notes.append(f"{card.get('id')}: cites no retrieved entry or past experiment, so it "
                         "rests on the signature and the code alone")
    return notes


def propose(request: Dict[str, Any]) -> Dict[str, Any]:
    cards = request.get("cards")
    if not isinstance(cards, list):
        cards = []
    noise = float(request.get("noise_threshold") or 0.0)
    if request.get("op"):
        cards = [dict(card, op=str(request["op"])) if isinstance(card, dict) else card
                 for card in cards]
        for card in cards:
            if isinstance(card, dict) and not card.get("parent"):
                card["parent"] = str(request.get("parent") or "")
    checked = check_cards(cards, [str(name) for name in (request.get("unavailable_metrics") or [])])
    accepted = checked["accepted"][:MAX_CARDS]
    rejected = list(checked["rejected"])
    for card in checked["accepted"][MAX_CARDS:]:
        rejected.append({"transformation": str(card.get("transformation") or card.get("id") or "?"),
                         "reason": f"held back: at most {MAX_CARDS} cards are proposed per round"})
    ordered = rank(accepted, noise)

    notes = explain(ordered, noise)
    problems: List[str] = []
    if not cards:
        problems.append("no_card: no candidate cards were supplied for validation")
    if not accepted and cards:
        problems.append("no_card: every proposed card was rejected; the reasons are in rejected")
    return {
        "status": "ok" if ordered else "no_card",
        "cards": [card for card, _signal in ordered],
        "rejected": rejected,
        "problems": problems,
        "notes": notes,
    }


def main(argv: List[str]) -> int:
    request = read_request(argv)
    stored = request.get("cards_file")
    if stored:
        path = Path(str(stored))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            request = dict(request, cards=payload.get("cards") or [])
        elif isinstance(payload, list):
            request = dict(request, cards=payload)
    outcome = propose(request)
    try:
        schema = jsonschema_lite.load_schema(
            str(ROOT / "skills" / "propose-hypothesis" / "contract" / "output.schema.json"))
        jsonschema_lite.check(outcome, schema, label="propose-hypothesis")
    except jsonschema_lite.ValidationError as error:
        outcome["problems"] = list(outcome.get("problems") or []) + \
            [f"schema: {line}" for line in error.errors]
        outcome["status"] = "schema_invalid"
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
