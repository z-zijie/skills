#!/usr/bin/env python3
"""replay-benchmark: replay one scaffold change and return merge or rollback.

The protocol lives in ``orchestrator/replay.py`` and is called, not re-implemented: the minimum
repeats, the dev interval that must exclude zero, the holdout regression check and the secondary
metric tolerance are all defined there, in code, where a skill cannot weaken them.

This script's own work is the boundary. It confirms the branch holds one change before spending
anything, refuses to decide below the protocol minimum, converts the judge's report into the
frozen ``schemas/replay-report.schema.json`` object, and reduces what leaves the skill to per-split
aggregates and a decision. The held-out operator names are read by the judge and never rendered
outward.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, read_request  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import replay as replay_mod  # noqa: E402

DEFAULT_CHANGELOG = Path("orchestrator") / "state" / "CHANGELOG-scaffold.md"


def next_report_id() -> str:
    directory = ROOT / "orchestrator" / "state"
    highest = 0
    if directory.is_dir():
        for path in directory.glob("RP-*.json"):
            try:
                highest = max(highest, int(path.stem.split("-")[1]))
            except (IndexError, ValueError):
                continue
    return f"RP-{highest + 1:04d}"


def split_totals(configs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Aggregates per split, and no operator names: the split is what may leave this skill."""
    totals: Dict[str, Dict[str, Any]] = {}
    for split in ("dev", "holdout"):
        deltas = [float((item.get("new") or {}).get("attainment_gain", 0.0))
                  - float((item.get("old") or {}).get("attainment_gain", 0.0))
                  for item in configs if item.get("split") == split]
        if not deltas:
            continue
        totals[split] = {"configs": len(deltas),
                         "mean_delta": round(sum(deltas) / len(deltas), 6),
                         "min_delta": round(min(deltas), 6)}
    return totals


def one_change(request: Dict[str, Any]) -> bool:
    """A branch with two changes cannot be attributed, so it is never replayed."""
    summary = str(request.get("change_summary") or "")
    separators = summary.count(" and ") + summary.count("; ")
    if separators > 0 and not request.get("single_change_confirmed"):
        return False
    return True


def replay(request: Dict[str, Any]) -> Dict[str, Any]:
    notes: List[str] = []
    problems: List[str] = []
    configs = request.get("configs") or []
    if not configs:
        return {"status": "unavailable", "report": None, "split_totals": {},
                "problems": ["unavailable: no configurations were supplied"], "notes": notes}

    proposal = request.get("proposal") or {}
    stage = str(request.get("stage") or "full")
    repeats = int(request.get("repeats") or 0)

    if not one_change(request):
        report = {
            "id": str(request.get("id") or next_report_id()),
            "proposal_id": str(proposal.get("id") or ""),
            "change_summary": str(request.get("change_summary") or ""),
            "configs": configs,
            "decision": "human_only",
            "screening_stage": stage,
            "rationale": "the branch holds more than one change, so a replay cannot attribute the "
                         "result to either",
        }
        return {"status": "human_only", "decision": "human_only", "report": report,
                "split_totals": split_totals(configs), "changelog_applied": False,
                "problems": problems, "notes": notes}

    if repeats and repeats < replay_mod.MIN_REPEATS:
        report = {
            "id": str(request.get("id") or next_report_id()),
            "proposal_id": str(proposal.get("id") or ""),
            "change_summary": str(request.get("change_summary") or ""),
            "configs": configs,
            "decision": "needs_more_repeats",
            "screening_stage": stage,
            "rationale": f"{repeats} repeat(s) is below the protocol minimum of "
                         f"{replay_mod.MIN_REPEATS}; a difference from fewer is mostly noise",
        }
        return {"status": "needs_more_repeats", "decision": "needs_more_repeats", "report": report,
                "split_totals": split_totals(configs), "changelog_applied": False,
                "problems": problems, "notes": notes}

    budget = request.get("budget") or {}
    seed = int(request.get("seed") or 0)
    try:
        if stage == "two_stage_screen":
            judged = replay_mod.screen(configs, budget, repeats=max(repeats, replay_mod.MIN_REPEATS),
                                       seed=seed)
        else:
            judged = replay_mod.evaluate_change(configs, budget,
                                               repeats=max(repeats, replay_mod.MIN_REPEATS),
                                               seed=seed)
    except replay_mod.ReplayError as error:
        return {"status": "unavailable", "report": None, "split_totals": split_totals(configs),
                "problems": [f"unavailable: {error}"], "notes": notes}

    report = {
        "id": str(request.get("id") or next_report_id()),
        "proposal_id": str(proposal.get("id") or ""),
        "change_summary": str(request.get("change_summary") or ""),
        "base_commit": str(request.get("base_commit") or ""),
        "candidate_commit": str(request.get("candidate_commit") or ""),
        "budget": budget,
        "configs": configs,
        "primary_metric": judged.get("primary_metric"),
        "holdout_regressed": bool(judged.get("holdout_regressed")),
        "secondary_regressed": bool(judged.get("secondary_regressed")),
        "screening_stage": str(judged.get("screening_stage") or stage),
        "decision": str(judged.get("decision")),
        "rationale": str(judged.get("rationale") or ""),
    }

    try:
        jsonschema_lite.check(report, jsonschema_lite.load_schema(
            str(ROOT / "schemas" / "replay-report.schema.json")), label="replay report")
    except jsonschema_lite.ValidationError as error:
        problems.extend(f"schema: {line}" for line in error.errors)

    applied = False
    if report["decision"] in ("merge", "rollback") and not request.get("dry_run"):
        changelog = Path(str(request.get("changelog") or DEFAULT_CHANGELOG))
        if not changelog.is_absolute():
            changelog = ROOT / changelog
        changelog.parent.mkdir(parents=True, exist_ok=True)
        result = replay_mod.apply_decision(report, proposal, changelog, root=ROOT)
        applied = bool(result.get("applied"))
        if applied:
            notes.append(f"the {report['decision']} decision was appended to {changelog.name}")

    if report["decision"] == "rollback":
        notes.append("both outcomes are logged: a rollback written down is a change nobody repeats")
    totals = split_totals(configs)
    if "holdout" in totals:
        notes.append("holdout aggregates are reported; operator names are not")
    status = "schema_invalid" if problems else "ok"
    return {"status": status, "decision": report["decision"], "report": report,
            "split_totals": totals, "changelog_applied": applied,
            "problems": problems, "notes": notes}


def main(argv: List[str]) -> int:
    outcome = replay(read_request(argv))
    try:
        schema = jsonschema_lite.load_schema(
            str(ROOT / "skills" / "replay-benchmark" / "contract" / "output.schema.json"))
        jsonschema_lite.check(outcome, schema, label="replay-benchmark")
    except jsonschema_lite.ValidationError as error:
        outcome["problems"] = list(outcome.get("problems") or []) + \
            [f"schema: {line}" for line in error.errors]
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
