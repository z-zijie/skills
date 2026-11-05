#!/usr/bin/env python3
"""Assemble one scaffold-change proposal from a failure report.

Turns a chosen cluster into a proposal object plus the branch/change-note scaffolding, and
refuses the cases the outer loop is not allowed to do: contract edits, evaluator or tolerance
edits, rule-status changes, and anything that would loosen a verifier check.

The gate is chosen from the target, never from what would be convenient:

* playbook, skill strategy, pattern text      -> replay_dev_holdout
* verifier check, when it only tightens        -> tighten_only_replay
* signature threshold                          -> replay_dev_holdout
* script or tool outside the evaluator         -> unit_tests_plus_replay
* model routing                                -> human_only
* contracts, evaluator, specs, tolerances      -> refused, human_only by construction
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import paths  # noqa: E402

GATES = {
    "playbook": "replay_dev_holdout",
    "skill_strategy": "replay_dev_holdout",
    "pattern": "replay_dev_holdout",
    "threshold": "replay_dev_holdout",
    "verifier_check": "tighten_only_replay",
    "script": "unit_tests_plus_replay",
    "model_routing": "human_only",
}
REFUSED = ("contract", "evaluator", "spec", "tolerance", "holdout", "benchmark")
_REFUSED_PATTERNS = re.compile(r"(evaluator/|benchmark/(dev|holdout)|\.rsi/|/contract/|spec\.yaml|holdout)", re.I)


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


def choose_target(data: Dict[str, Any]) -> Dict[str, Any]:
    """The single file this change touches, plus the kind of object it is."""
    target = str(data.get("target") or "").strip()
    kind = str(data.get("change_kind") or "").strip()
    if not target:
        # Default to the playbook the cluster's tags route to; the cluster is the justification.
        playbook = str(data.get("playbook") or data.get("primary_bottleneck") or "")
        target = f"playbooks/{playbook}.md" if playbook else ""
    if not kind:
        if target.startswith("playbooks/"):
            kind = "playbook"
        elif "/strategy/" in target:
            kind = "skill_strategy"
        elif target.startswith("kb/patterns"):
            kind = "pattern"
        elif target.startswith("kb/hardware/measured/thresholds"):
            kind = "threshold"
        elif target.startswith("kb/verifier") or "checks" in target:
            kind = "verifier_check"
        elif target.startswith("scripts/") or target.startswith("tooling/"):
            kind = "script"
        elif target.startswith("orchestrator/models"):
            kind = "model_routing"
        elif "/contract/" in target:
            kind = "contract"
        else:
            kind = "skill_strategy"
    return {"target": target, "kind": kind}


def checks(data: Dict[str, Any], target: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if not target["target"]:
        problems.append("no target: a change needs one file or one store entry")
    if target["kind"] in REFUSED:
        problems.append(f"{target['kind']} is human-only: the outer loop may not propose it")
    if _REFUSED_PATTERNS.search(target["target"] or ""):
        problems.append(f"{target['target']} is outside the outer loop's write boundary")
    if not _on_a_branch(target["target"]):
        problems.append(
            f"{target['target']} is not branch-writable: the outer loop changes skills/, "
            "playbooks/ and kb/ strategy text only, and only on a branch")
    cluster = data.get("cluster") or {}
    if not cluster.get("kind"):
        problems.append("no cluster: an edit must target a cluster analyze-failures produced")
    if len(cluster.get("experiments") or []) < 2:
        problems.append("cluster has fewer than two experiments: not a pattern yet")
    if not data.get("change"):
        problems.append("no change text: the proposal must say what the new text is")
    if target["kind"] == "verifier_check" and not data.get("tightens_only"):
        problems.append("verifier checks tighten only; mark tightens_only or send it to a human")
    if data.get("same_change_already_rolled_back"):
        problems.append("this exact change was rolled back before: do not retry it unchanged")
    return problems


def replay_plan(data: Dict[str, Any], gate: str) -> Dict[str, Any]:
    """Two-stage screening, offline-first where the change allows it, fixed budget."""
    offline_first = bool(data.get("offline_replay_possible", gate == "tighten_only_replay"))
    return {
        "screening": "two_stage_screen",
        "stage_1_offline_ledger": offline_first,
        "stage_1_operators": [str(op) for op in (data.get("screen_operators") or [])][:3],
        "stage_2_operators": [str(op) for op in (data.get("full_operators") or [])],
        "experiments_per_operator": int(data.get("experiments_per_operator") or 10),
        "repeats": max(3, int(data.get("repeats") or 3)),
        "from_checkpoint": True,
        "holdout_visible_to": "replay_judge",
        "decision_rule": (
            "merge when the primary metric improves on dev with a confidence interval that "
            "excludes zero, holdout does not regress, and no secondary metric gets clearly worse"
        ),
        "gate": gate,
    }


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    target = choose_target(data)
    problems = checks(data, target)
    kind = target["kind"]
    gate = GATES.get(kind, "human_only")
    cluster = data.get("cluster") or {}
    block = data.get("change") or ""
    proposal_id = str(data.get("proposal_id") or "P-1")

    rationale = (
        f"Failure cluster {cluster.get('kind')} ({cluster.get('count', 0)} experiments, "
        f"tags {', '.join(cluster.get('tags') or []) or 'none'}): {cluster.get('summary', '')} "
        f"This change targets it in {target['target']}."
    )
    proposal = {
        "id": proposal_id,
        "kind": "verifier_check" if kind == "verifier_check" else (
            "playbook" if kind == "playbook" else (
                "threshold" if kind == "threshold" else (
                    "pattern" if kind == "pattern" else (
                        "model_routing" if kind == "model_routing" else "skill_strategy")))),
        "title": str(data.get("title") or f"Target {cluster.get('kind', 'cluster')} in {target['target']}"),
        "rationale": rationale,
        "target": target["target"],
        "diff": block,
        "evidence": [str(exp) for exp in (cluster.get("experiments") or [])],
        "status": "draft" if not problems else "rejected",
        "gate": gate,
        "author": "edit-scaffold",
    }
    return {
        "proposal": proposal,
        "target_kind": kind,
        "branch": {
            "name": f"scaffold/{proposal_id}-{_slug(target['target'])}",
            "base": str(data.get("base_commit") or "HEAD"),
            "files_changed": [target["target"]],
            "commit_message": (
                f"{proposal['title']}\n\nCluster: {cluster.get('kind')} "
                f"({len(proposal['evidence'])} experiments)\nProposal: {proposal_id}\n"
                f"Gate: {gate}\n"
            ),
        },
        "change_note": {
            "one_change": True,
            "why_this_cluster": rationale,
            "what_changed": block,
            "what_did_not_change": str(data.get("what_did_not_change") or
                                       f"nothing outside {target['target']}"),
            "expected_effect": str(data.get("expected_effect") or
                                   "the targeted cluster shrinks without a secondary metric regressing"),
            "revert": f"git revert the merge of {proposal_id}; the branch touches one file",
        },
        "replay": replay_plan(data, gate),
        "refusals": problems,
        "ready_for_replay": not problems,
    }


#: What a scaffold branch may touch. Everything else is written by the inner loop, by the
#: evaluator, or by a human.
BRANCH_WRITABLE = (
    "playbooks/",
    "skills/",  # strategy/ and scripts/ only; contract/ is refused before this point
    "kb/patterns/",
    "kb/rules/",
    "kb/antipatterns/",
    "kb/hardware/measured/thresholds",
    "kb/verifier/",
    "orchestrator/models.yaml",
)


def _on_a_branch(target: str) -> bool:
    return any(target.startswith(prefix) for prefix in BRANCH_WRITABLE)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "change"


def main(argv: List[str]) -> int:
    result = build(read_input(argv))
    json.dump(result, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
