#!/usr/bin/env python3
"""Assemble a microbenchmark proposal from a prediction-error report.

Triggered when the hardware model has a hole: |log measured/predicted| above 0.2, or a cluster
of misses on one mechanism. The proposal isolates one mechanism, states the parameter it would
produce, and says how the hypothesizer would use it.

This always carries ``gate: human_only``: microbenchmark code belongs to the frozen evaluator, so
the proposal is the most this loop may produce. The script refuses anything that would measure
one vendor's implementation detail into a canonical parameter without an adapter mapping.
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

#: The suite's existing items, so a proposal can say "extend this" rather than "add another".
SUITE_ITEMS = (
    "noise_floor", "drift", "memory_bandwidth", "peak_compute", "cache_hierarchy",
    "coalescing_penalty", "load_width", "bank_conflicts", "occupancy_vs_registers",
    "branch_divergence", "instruction_cost", "launch_overhead", "sync_cost", "atomics",
)
#: What each existing item already answers, so a proposal does not duplicate it.
ANSWERS = {
    "coalescing_penalty": ["sectors_per_request"],
    "load_width": ["instruction_count"],
    "cache_hierarchy": ["cache_hit_pct", "cache_throughput_pct"],
    "memory_bandwidth": ["device_mem_throughput_pct"],
    "peak_compute": ["compute_throughput_pct"],
    "bank_conflicts": ["scratchpad_conflict_ratio"],
    "occupancy_vs_registers": ["occupancy_pct", "registers_per_lane"],
    "branch_divergence": ["branch_divergence_pct"],
    "instruction_cost": ["instruction_count"],
    "launch_overhead": ["launch_overhead_us"],
    "sync_cost": ["stall_barrier"],
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


def covered_by(metric: Optional[str]) -> Optional[str]:
    if not metric:
        return None
    canonical = metrics.canonical(metric)
    for item, answered in ANSWERS.items():
        if canonical in answered:
            return item
    return None


def build(data: Dict[str, Any]) -> Dict[str, Any]:
    misses = [item for item in (data.get("prediction_errors") or []) if isinstance(item, dict)]
    hole = float(data.get("prediction_error_hole") or knowledge.PREDICTION_ERROR_HOLE)
    mechanism = str(data.get("mechanism") or "").strip()
    metric = data.get("metric")
    parameter = str(data.get("parameter") or "").strip()
    extend = str(data.get("extend_suite_item") or "").strip()
    problems: List[str] = []

    qualifying = [item for item in misses
                  if abs(float(item.get("prediction_error") or 0.0)) > hole]
    if not qualifying:
        problems.append(
            f"no prediction error exceeds the hole threshold {hole}: the model is not wrong here, "
            "so a new measurement is not justified")
    if not mechanism:
        problems.append("state the one mechanism the microbenchmark isolates")
    if metric and not metrics.is_known(str(metric)):
        problems.append(f"{metric} is not a canonical metric")
    if not parameter:
        problems.append("name the parameter the measurement would produce")
    if extend and extend not in SUITE_ITEMS:
        problems.append(f"{extend} is not an existing suite item; propose a new one or name an existing one")
    covered = covered_by(metric)
    if covered and not extend:
        # An existing item already observes this metric, so the proposal extends it. That is the
        # cheaper answer and the one the suite's design prefers.
        extend = covered
    if covered and data.get("new_item") is True:
        problems.append(
            f"the existing suite item {covered} already answers {metrics.canonical(str(metric))}: "
            "extend it rather than adding a second item that measures the same thing")

    sweep = data.get("sweep") or {}
    proposal = {
        "id": str(data.get("proposal_id") or "P-1"),
        "kind": "microbench",
        "title": str(data.get("title") or f"Microbenchmark: {mechanism[:60]}"),
        "rationale": (
            f"{len(qualifying)} experiment(s) missed their predicted speedup by more than "
            f"{hole}: {', '.join(str(item.get('exp_id')) for item in qualifying)}. "
            f"The hardware model has a hole around {mechanism}."
        ),
        "target": "evaluator/microbench/suite.py",
        "evidence": [str(item.get("exp_id")) for item in qualifying],
        "status": "needs_human",
        "gate": "human_only",
        "author": "design-microbench",
    }
    design = {
        "mechanism": mechanism,
        "extends_suite_item": extend or None,
        "is_new_item": not bool(extend),
        "isolates": str(data.get("isolates") or "one mechanism at a time"),
        "sweep": {
            "variable": str(sweep.get("variable") or "the one dimension the mechanism depends on"),
            "values": [str(value) for value in (sweep.get("values") or [])],
        },
        "repeats": max(3, int(data.get("repeats") or 3)),
        "produces": {
            "parameter": parameter,
            "unit": str(data.get("unit") or ""),
            "used_by": str(data.get("used_by") or "the roofline term and the prediction range"),
        },
        "counters_observed": [metrics.canonical(str(metric))] if metric else [],
        "environment_fingerprint_required": True,
        "expiry": "re-measure after a driver, runtime or compiler change; results carry a date",
        "would_settle": str(data.get("would_settle") or
                             "whether the mechanism's effect is as large as the model assumes"),
        "fallback_if_rejected": (
            "keep the prediction range on the affected rules wide and put them under review, which "
            "is honest but keeps the hole open"),
    }
    return {
        "proposal": proposal,
        "design": design,
        "problems": problems,
        "ready_for_human": not problems,
        "human_decision": {
            "question": "Add this microbenchmark to the frozen evaluator suite?",
            "why_human": "microbenchmark code is Layer 0: agents read its results and never edit it",
            "on_approval": "the code lands through the evaluator's owner, then calibrate-hardware runs it",
            "on_rejection": design["fallback_if_rejected"],
        },
    }


def main(argv: List[str]) -> int:
    json.dump(build(read_input(argv)), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
