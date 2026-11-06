#!/usr/bin/env python3
"""retrieve-knowledge: fetch the entries that match a signature and render ``context.md``.

All matching is done by ``rsi.knowledge``: exact tag matching first, then relaxation with the
hardware constraint dropped, then tags relaxed to "any". This script enforces the caps, ranks
nothing on its own, and renders the fixed reading order the hypothesizer expects.

Two properties are deliberate. The match level is carried into the rendered file, so a thin match
is visible where it is read rather than implied by a count. And every entry keeps its id and its
status, so a card can cite what it was based on and a warning about thin evidence travels with the
rule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, ledger, read_request  # noqa: E402
from rsi import knowledge  # noqa: E402

CAPS = dict(knowledge.RETRIEVAL_CAPS)


def resolve_root(named: str) -> Path:
    """The repository root a knowledge base hangs off.

    ``kb_root`` names a directory that contains ``kb/`` — the same shape the real repository has,
    so a fixture and the real store are read by identical code.
    """
    path = Path(named)
    if not path.is_absolute():
        path = (ROOT / path) if (ROOT / path).is_dir() else path
    path = path.resolve()
    return path.parent if path.name == "kb" else path


def cap_request(request: Dict[str, Any]) -> Dict[str, int]:
    caps = dict(CAPS)
    for key, value in (request.get("caps") or {}).items():
        if key in caps and isinstance(value, int) and value >= 0:
            caps[key] = min(int(value), CAPS[key])
    return caps


def render(signature: Dict[str, Any], rules: List[Dict[str, Any]], patterns: List[Dict[str, Any]],
           antipatterns: List[Dict[str, Any]], experiments: List[Dict[str, Any]],
           measured: Dict[str, Any], match_level: Dict[str, str]) -> str:
    """The context file, in the order the hypothesizer reads it."""
    lines: List[str] = ["# Retrieved context", ""]
    lines.append(f"op: {signature.get('op')}  hw: {signature.get('hw_id')}  "
                 f"class: {signature.get('class')}")
    lines.append(f"tags: {', '.join(signature.get('tags') or []) or '(none)'}")
    lines.append(f"primary bottleneck: {signature.get('primary_bottleneck') or '(unnamed)'}")
    lines.append(f"match level: rules={match_level['rules']}, patterns={match_level['patterns']}, "
                 f"antipatterns={match_level['antipatterns']}")
    if any(level in ("hw_relaxed", "tags_any") for level in match_level.values()):
        lines.append("")
        lines.append("This match was relaxed: entries come from a neighbouring situation and are "
                     "suggestions, not precedent.")
    lines.append("")

    if measured:
        lines.append("## Measured hardware parameters")
        for key, value in measured.items():
            lines.append(f"- {key}: {value}")
        lines.append("")

    def block(title: str, entries: List[Dict[str, Any]], fields: List[str]) -> None:
        lines.append(f"## {title}")
        if not entries:
            lines.append("_nothing matched_")
            lines.append("")
            return
        for entry in entries:
            identifier = entry.get("id") or entry.get("exp_id") or "?"
            status = entry.get("status") or entry.get("verdict") or ""
            lines.append(f"### {identifier}" + (f" ({status})" if status else ""))
            scope = entry.get("scope") or {}
            if scope:
                lines.append(f"- scope: {json.dumps(scope, sort_keys=True)}")
            for field in fields:
                if entry.get(field):
                    lines.append(f"- {field}: {entry[field]}")
            if entry.get("_warning"):
                lines.append(f"- warning: {entry['_warning']}")
            if entry.get("_path"):
                lines.append(f"- source: {entry['_path']}")
            lines.append("")

    block("Rules", rules, ["statement", "mechanism", "effect", "confirm_rate",
                           "mean_prediction_error", "last_verified", "counter_example_note"])
    block("Patterns", patterns, ["statement", "mechanism", "expected_counters",
                                 "implementation_traps"])
    block("Anti-patterns", antipatterns, ["statement", "mechanism", "effect",
                                         "counter_example_note"])
    block("Past experiments", experiments, ["hypothesis", "verdict", "speedup", "significant",
                                            "prediction_error"])
    return "\n".join(lines).rstrip() + "\n"


def retrieve(request: Dict[str, Any]) -> Dict[str, Any]:
    signature = request.get("signature")
    if not isinstance(signature, dict):
        return {"status": "unavailable", "context_md": None, "problems": ["unavailable: no signature"],
                "notes": [], "counts": {"rules": 0, "antipatterns": 0, "patterns": 0, "experiments": 0},
                "match_level": {"rules": "none", "antipatterns": "none", "patterns": "none"}}
    signature = dict(signature)
    if request.get("op"):
        signature["op"] = str(request["op"])

    kb_root = resolve_root(str(request["kb_root"])) if request.get("kb_root") else ROOT
    notes: List[str] = []
    problems: List[str] = []
    caps = cap_request(request)

    result = knowledge.retrieve(signature, root=kb_root, caps=caps)
    # Uncapped match counts, so "dropped" means "matched but cut by the cap" rather than
    # "did not match at all". Those are different facts and the hypothesizer needs the first.
    uncapped = knowledge.retrieve(signature, root=kb_root,
                                 caps={"rules": 10_000, "antipatterns": 10_000,
                                       "patterns": 10_000, "experiments": 10_000})
    entries = ledger.read_entries(ROOT)
    if request.get("ledger_file"):
        path = Path(str(request["ledger_file"]))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        if path.is_file():
            entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                       if line.strip()]
    experiments = knowledge.similar_experiments(signature, entries, limit=caps["experiments"])

    measured: Dict[str, Any] = {}
    if request.get("include_hardware", True):
        try:
            measured = knowledge.measured_parameters(signature, root=kb_root)
        except (OSError, ValueError):
            measured = {}
        if not measured:
            notes.append("no measured hardware parameters for this hardware id; the hypothesizer "
                         "works without them")

    rules = result.get("rules") or []
    patterns = result.get("patterns") or []
    antipatterns = result.get("antipatterns") or []
    matched = knowledge.similar_experiments(signature, entries, limit=10_000)
    total = {
        "rules": len(uncapped.get("rules") or []),
        "patterns": len(uncapped.get("patterns") or []),
        "antipatterns": len(uncapped.get("antipatterns") or []),
        "experiments": len(matched),
    }
    dropped = {
        "rules": max(0, total["rules"] - len(rules)),
        "patterns": max(0, total["patterns"] - len(patterns)),
        "antipatterns": max(0, total["antipatterns"] - len(antipatterns)),
        "experiments": max(0, total["experiments"] - len(experiments)),
    }
    counts = {"rules": len(rules), "antipatterns": len(antipatterns),
              "patterns": len(patterns), "experiments": len(experiments)}
    if not any(counts.values()):
        notes.append("nothing matched this signature: the hypothesizer works from the code and the "
                     "signature alone, and the cards it writes will cite no prior evidence")

    context_md = render(signature, rules, patterns, antipatterns, experiments, measured,
                        result.get("match_level") or {})
    refs = [str(entry.get("id") or entry.get("exp_id")) for entry in
            [*rules, *patterns, *antipatterns, *experiments]
            if entry.get("id") or entry.get("exp_id")]
    return {
        "status": "ok" if any(counts.values()) else "empty",
        "context_md": context_md,
        "counts": counts,
        "match_level": result.get("match_level") or {},
        "dropped": dropped,
        "refs": refs,
        "problems": problems,
        "notes": notes,
    }


def main(argv: List[str]) -> int:
    outcome = retrieve(read_request(argv))
    try:
        schema = jsonschema_lite.load_schema(
            str(ROOT / "skills" / "retrieve-knowledge" / "contract" / "output.schema.json"))
        jsonschema_lite.check(outcome, schema, label="retrieve-knowledge")
    except jsonschema_lite.ValidationError as error:
        outcome["problems"] = list(outcome.get("problems") or []) + \
            [f"schema: {line}" for line in error.errors]
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
