#!/usr/bin/env python3
"""Register a human conjecture and plan the smallest experiments that would test it.

Writes nothing to a store. It emits the conjecture card, an experiment plan sized by what the
conjecture needs to become decidable, and the outcome table that decides later whether it was
confirmed, refuted or left open.

Minimality is mechanical: prefer a microbenchmark over a campaign experiment, one case over a
sweep, and an existing ledger experiment over new device time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RSI_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RSI_ROOT / "tooling"))

from rsi import knowledge, metrics  # noqa: E402

MAX_EXPERIMENTS = 3
OUTCOMES = ("promoted", "refuted", "open")
_KIND_OF_CHECK = {
    "device_mem_throughput_pct": "roofline",
    "compute_throughput_pct": "roofline",
    "cache_hit_pct": "microbench",
    "scratchpad_conflict_ratio": "microbench",
    "sectors_per_request": "microbench",
    "occupancy_pct": "campaign",
    "launch_overhead_us": "microbench",
    "registers_per_lane": "campaign",
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


def experiment_kind(metric: Optional[str], tags: List[str]) -> str:
    """The cheapest experiment that can move or observe the metric the conjecture names."""
    if metric:
        canonical = metrics.canonical(metric)
        if canonical in _KIND_OF_CHECK:
            return _KIND_OF_CHECK[canonical]
    if "plateau" in tags:
        return "campaign"
    return "microbench" if len(tags) <= 1 else "campaign"


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    note = str(data.get("conjecture") or "").strip()
    op = str(data.get("op") or "")
    tags = [str(tag) for tag in (data.get("signature_tags") or [])]
    observation = str(data.get("expected_observation") or "").strip()
    metric = data.get("metric")
    problems: List[str] = []

    if len(note) < 20:
        problems.append("the conjecture needs a sentence a reader can disagree with")
    if not observation:
        problems.append("state what would be observed if the conjecture were true")
    if metric and not metrics.is_known(str(metric)):
        problems.append(f"{metric} is not a canonical metric")
    for tag in tags:
        if tag not in metrics.TAG_METRICS and tag not in metrics.CLASSES and tag != "plateau":
            problems.append(f"{tag} is not a signature tag or class in docs/vocabulary.md")

    kind = experiment_kind(metric, tags)
    microbench = str(data.get("microbench") or "")
    smallest = {
        "kind": kind,
        "count": 1,
        "why_minimal": (
            "one microbenchmark isolates the mechanism without a campaign"
            if kind == "microbench" else
            "one campaign experiment on the cheapest dev operator is enough to falsify it"
        ),
        "cases": ["the narrowest shape range the conjecture claims"],
        "repeats": max(3, int(data.get("repeats") or 3)),
        "budget_experiments": min(MAX_EXPERIMENTS, int(data.get("budget_experiments") or 1)),
    }
    if kind == "microbench" and microbench:
        smallest["microbench"] = microbench

    existing = [str(exp) for exp in (data.get("related_experiments") or [])]
    if existing:
        smallest["reuse_ledger"] = existing[:3]
        smallest["why_minimal"] += "; it may already be answerable from the ledger, so check the ids first"

    conjecture = {
        "id": str(data.get("conjecture_id") or "C-001"),
        "statement": note,
        "scope": {
            "hw": [str(data.get("hw_id") or "unknown")],
            "tags_all": tags,
        },
        "status": "conjecture",
        "created": str(data.get("today") or ""),
        "expected_observation": observation,
        "metric": metrics.canonical(str(metric)) if metric else None,
        "source": "human",
    }

    plan = {
        "conjecture_id": conjecture["id"],
        "op": op,
        "primary_kind": kind,
        "experiments": [smallest],
        "max_experiments": MAX_EXPERIMENTS,
        "outcome_table": {
            "promoted": f"the observation appears and the metric moves as predicted: {observation}",
            "refuted": "the experiment is valid and the observation does not appear",
            "open": "the experiment could not be made valid: not_implemented, correctness failed, "
                    "or the counters were absent",
        },
        "promotion_path": (
            "promotion makes it a candidate rule with this experiment as its single confirmation; "
            "it still cannot be used as evidence to verify anything until a second operator "
            "confirms it"
        ),
        "limits": {
            "counts_as_evidence": False,
            "may_be_used_for_verification": False,
            "may_inspire_hypotheses": True,
        },
    }
    return {
        "conjecture": conjecture,
        "plan": plan,
        "problems": problems,
        "ready": not problems,
        "notes": _notes(kind, tags, data),
    }


def _notes(kind: str, tags: List[str], data: Dict[str, Any]) -> List[str]:
    notes = [
        "an unverified conjecture may inspire hypotheses and cannot be grounds for verification",
        "keep it in kb/conjectures/ until the plan has run; promotion is a replay-gated move",
    ]
    if kind == "microbench" and not data.get("microbench"):
        notes.append("no microbenchmark named: the orchestrator will ask design-microbench for one")
    if knowledge.PREDICTION_ERROR_HOLE and data.get("prediction_error"):
        notes.append("the conjecture's prediction error should be compared with the hole threshold "
                     "once the plan has run")
    if "plateau" in tags:
        notes.append("a plateau conjecture usually needs an algorithm-level experiment, so expect "
                     "the plan to be a campaign rather than a microbenchmark")
    return notes


def main(argv: List[str]) -> int:
    json.dump(build(read_input(argv)), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
