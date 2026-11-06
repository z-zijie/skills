#!/usr/bin/env python3
"""Start, resume or report one operator's optimization campaign.

The entry point a human invokes. It does not optimize anything itself: it validates that the
operator is ready, opens a campaign with a budget and a stop condition set, and emits the stage
sequence the orchestrator will drive. Skills are never called from here; the orchestrator reads
the stages and runs them in order.

``mode`` is one of:

* ``start``   open a campaign, after the preflight checks pass
* ``resume``  re-enter an open campaign from its state file
* ``status``  report where a campaign stands without changing anything
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import hardware, ledger, paths, spec as spec_mod  # noqa: E402

#: The campaign's stage sequence, in order. Each stage names the skill the orchestrator runs;
#: this skill sequences nothing itself.
STAGES = (
    ("preflight", "optimize-operator", "operator, budget and noise floor are all in place"),
    ("baseline", "run-evaluator", "baseline measured, profiled, diagnosed and recorded as exp-0000"),
    ("round", "propose-hypothesis", "one round of W2, from hypothesis to recorded experiment"),
    ("decide", "plan-campaign", "one campaign move: continue, change_direction, combine or stop"),
    ("report", "report-campaign", "the campaign report, including a rewrite recommendation"),
)
#: Stages inside one round, in the order W2 fixes. Named for the orchestrator's trace.
ROUND_STEPS = (
    "collect-profile", "diagnose-bottleneck", "retrieve-knowledge", "propose-hypothesis",
    "implement-change", "run-evaluator", "inspect-binary", "verify-mechanism",
    "record-experiment",
)
STOP_CONDITIONS = (
    "target_attainment_reached",
    "five_rounds_without_significant_gain",
    "budget_exhausted",
    "no_hypothesis_above_noise",
)


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


def preflight(data: Dict[str, Any]) -> Dict[str, Any]:
    """Every check that must pass before a campaign may spend budget."""
    op = str(data.get("op") or "")
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    spec_path = paths.op_dir(op) / "spec.yaml"
    check("operator_exists", spec_path.is_file(),
          f"{paths.rel(spec_path)} is the operator's contract; onboarding runs first otherwise")
    check("budget_granted", int((data.get("budget") or {}).get("experiments") or 0) > 0,
          "a campaign needs an experiment budget; zero means nothing may be spent")
    check("target_set", 0 < float(data.get("target_attainment_pct") or 0) <= 100,
          "the stop condition is a target attainment between 0 and 100")
    noise = data.get("noise_threshold")
    check("noise_floor_known", noise is not None,
          "without a measured noise floor no win can be told from run-to-run variation")
    check("baseline_on_record", bool((data.get("baseline_exp_id") or "").strip()
                                     or (data.get("baseline_recorded") is True)),
          "experiment 0 is the baseline; a campaign that starts from nothing cannot measure a gain")

    if spec_path.is_file():
        try:
            parsed = spec_mod.Spec.load(str(spec_path))
            check("spec_parses", True,
                  f"shapes and a cost model are present for dtype(s) {parsed.dtypes or ['fp32']}")
        except spec_mod.SpecError as error:
            check("spec_parses", False, str(error))
    return {
        "op": op,
        "checks": checks,
        "passed": all(item["ok"] for item in checks),
        "failed": [item["check"] for item in checks if not item["ok"]],
    }


def campaign_state(data: Dict[str, Any]) -> Dict[str, Any]:
    op = str(data.get("op") or "")
    entries = ledger.entries_for_op(op) if op and data.get("read_ledger", True) else []
    counts = ledger.evidence_counts(entries)
    budget = data.get("budget") or {}
    planned = int(budget.get("experiments") or 0)
    spent = int(budget.get("spent_experiments")
                if budget.get("spent_experiments") is not None else len(entries))
    target = float(data.get("target_attainment_pct") or 85.0)
    attainment = data.get("attainment_pct")
    attainment = float(attainment) if attainment is not None else None
    rounds = counts["confirmed"] + counts["refuted"]

    stopped: Optional[str] = None
    if attainment is not None and attainment >= target:
        stopped = "target_attainment_reached"
    elif planned and spent >= planned:
        stopped = "budget_exhausted"
    elif rounds >= 5 and not data.get("last_round_gained", True):
        stopped = "five_rounds_without_significant_gain"
    elif data.get("no_hypothesis_above_noise") is True:
        stopped = "no_hypothesis_above_noise"

    return {
        "op": op,
        "state": "stopped" if stopped else ("started" if entries else "ready_to_start"),
        "stop_condition": stopped,
        "progress": {
            "experiments": len(entries),
            "rounds": rounds,
            "confirmed": counts["confirmed"],
            "refuted": counts["refuted"],
            "not_implemented": counts["not_implemented"],
            "implausible": counts["implausible"],
        },
        "budget": {
            "experiments": planned,
            "spent_experiments": spent,
            "remaining": max(0, planned - spent) if planned else 0,
            "device_seconds": (data.get("budget") or {}).get("device_seconds"),
        },
        "attainment_pct": attainment,
        "target_attainment_pct": target,
    }


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(data.get("mode") or "start")
    pre = preflight(data)
    state = campaign_state(data)
    campaign_id = str(data.get("campaign_id") or f"CAMPAIGN-{pre['op'] or 'unnamed'}-1")
    stage_plan = [
        {"stage": name, "skill": skill, "done_when": done} for name, skill, done in STAGES
    ]
    result: Dict[str, Any] = {
        "campaign_id": campaign_id,
        "mode": mode,
        "op": pre["op"],
        "preflight": pre,
        "state": state,
        "stage_plan": stage_plan,
        "round_steps": list(ROUND_STEPS),
        "stop_conditions": list(STOP_CONDITIONS),
        "gates": {
            "evaluator_microbench_tolerances_holdout": "human_only",
            "contracts": "human_only",
            "verifier_checks": "tighten_only_replay",
            "playbooks_skill_strategy_thresholds": "replay_dev_holdout",
            "rule_status": "mechanical",
            "model_routing": "human_only",
        },
        "write_boundary": {
            "inner_loop": ["operators/<op>/workspace/**", "ledger/experiments.jsonl"],
            "outer_loop": ["kb/proposals/**", "a branch off the scaffold"],
            "never_by_an_agent": ["evaluator/**", "schemas/**", "benchmark/holdout*",
                                  "operators/<op>/spec.yaml"],
        },
        "budget_guard": {
            "stops_on": "budget_exhausted",
            "counts": ["experiments", "device_seconds", "tokens"],
            "replay_is_outside_the_campaign_budget": True,
        },
    }

    if mode == "start":
        result["ready"] = pre["passed"]
        result["next"] = (
            {"stage": "baseline", "skill": "run-evaluator",
             "instruction": "measure, profile and record the baseline as experiment 0"}
            if pre["passed"] else
            {"stage": "preflight", "skill": "optimize-operator",
             "instruction": f"fix {', '.join(pre['failed'])} before any budget is spent"}
        )
    elif mode == "resume":
        result["ready"] = pre["passed"] and state["stop_condition"] is None
        result["next"] = (
            {"stage": "decide", "skill": "plan-campaign",
             "instruction": "re-enter at the decision stage; the ledger moved since the pause"}
            if state["stop_condition"] is None else
            {"stage": "report", "skill": "report-campaign",
             "instruction": f"the campaign already stopped: {state['stop_condition']}"}
        )
    else:
        result["ready"] = state["stop_condition"] is None
        result["next"] = (
            {"stage": "decide", "skill": "plan-campaign",
             "instruction": "read the state; nothing is started in status mode"}
            if state["stop_condition"] is None else
            {"stage": "report", "skill": "report-campaign",
             "instruction": f"the campaign stopped: {state['stop_condition']}"}
        )

    if not pre["passed"] and mode == "start":
        result["refusals"] = [
            f"preflight failed: {check}" for check in pre["failed"]
        ]
    else:
        result["refusals"] = []
    return result


def main(argv: List[str]) -> int:
    json.dump(build(read_input(argv)), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
