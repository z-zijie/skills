#!/usr/bin/env python3
"""Offline regression driver for record-experiment.

A ledger test needs a writable repository, a signing key and a clean ledger, none of which the
real tree can offer to a regression case. This driver builds a scratch root that mirrors the real
one (``schemas/``, ``tooling/``, an evaluator key, an optional pre-existing ledger), runs the
skill's own ``record()`` against it, and prints the decision.

Nothing is stubbed: the same validation, the same signature check and the same append are used,
which is why a case can prove that an unsigned result or a duplicate id really is refused.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT  # noqa: E402
from record_experiment import record  # noqa: E402

KEY = b"regression-key-not-a-secret"


def make_scratch(seed_entries) -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="rsi-record-"))
    for name in ("schemas", "tooling", "skills"):
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, scratch / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
    key_file = scratch / ".rsi" / "evaluator.key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(KEY)
    ledger_file = scratch / "ledger" / "experiments.jsonl"
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    ledger_file.write_text("".join(json.dumps(entry) + "\n" for entry in seed_entries))
    return scratch


def seed_entry(exp_id: str) -> dict:
    return {
        "exp_id": exp_id, "op": "vec_add", "hw_id": "accel-a",
        "env_fingerprint": "hw=accel-a|device=generic", "scaffold_commit": "abc123",
        "parent": "baseline", "hypothesis": {"id": "H-0001"},
        "correctness": {"passed": True}, "verdict": "refuted",
    }


def main() -> int:
    case = json.load(sys.stdin)
    scratch = make_scratch([seed_entry(exp_id) for exp_id in case.get("seed_ledger") or []])
    # The verdict arrives as a document, because the skill reads it from the verifier's output
    # rather than from a flag. Writing it to the scratch root keeps the case honest: the value the
    # entry records is the one the verifier produced, not the one the caller asked for.
    verdict_path = scratch / "verdict.json"
    verdict_path.write_text(json.dumps({
        "hypothesis_id": "H-0007",
        "op": "vec_add",
        "verdict": case.get("verdict", "confirmed"),
        "checks": [{"id": "A1", "group": "A", "conclusion": "pass", "evidence": "diff hunk 1"}],
    }), encoding="utf-8")
    request = {
        "root": str(scratch),
        "parent": "best@exp-0003",
        "verdict_file": str(verdict_path),
        "result_file": case["result_file"],
        "hypothesis_file": "skills/record-experiment/tests/fixtures/hypothesis.json",
        "signature_file": "skills/record-experiment/tests/fixtures/signature.json",
        "diff_file": case.get("diff_file", "skills/record-experiment/tests/fixtures/diff.patch"),
        "profile_file": "skills/record-experiment/tests/fixtures/profile.json",
        "binary_file": case.get("binary_file", "skills/record-experiment/tests/fixtures/binary.json"),
        "counters_before": case.get("counters_before") or {
            "dram_throughput_pct": 85.0, "sectors_per_request": 3.8, "stall_long_scoreboard": 0.58},
        "counters_after": {"device_mem_throughput_pct": 88.0, "sectors_per_request": 1.1,
                           "stall_memory": 0.21},
        "timing_before": {"median_us": 140.0, "samples": 30},
        "cost": {"tokens": 12000, "device_seconds": 41.5, "implementation_retries": 0},
        "dry_run": bool(case.get("dry_run")),
    }
    if case.get("exp_id"):
        request["exp_id"] = case["exp_id"]
    outcome = record(request)
    status = outcome["status"]
    if outcome["problems"]:
        first = outcome["problems"][0].split(":", 1)[0]
        status = {"signature": "refused_unsigned", "duplicate": "refused_duplicate",
                  "unregistered_metric": "refused_unregistered_metric",
                  "schema": "schema_invalid"}.get(first, status)
    ledger_file = scratch / "ledger" / "experiments.jsonl"
    lines = [line for line in ledger_file.read_text().splitlines() if line.strip()]
    entry = outcome["entry"] or {}
    print(json.dumps({
        "status": status,
        "exp_id": outcome["exp_id"],
        "problem_kinds": sorted({problem.split(":", 1)[0] for problem in outcome["problems"]}),
        "ledger_lines": len(lines),
        "archived_count": len(outcome["archived"]),
        "missing_artifacts": outcome["missing_artifacts"],
        "entry_prediction_error": round(entry["prediction_error"], 4) if entry.get("prediction_error") is not None else None,
        "entry_speedup": entry.get("speedup"),
        "entry_significant": entry.get("significant"),
        "counter_names": sorted((entry.get("counters_after") or {}).keys()),
        "has_signature_block": bool(entry.get("evaluator_signature")),
        "has_scaffold_commit": bool(entry.get("scaffold_commit")),
        "dry_run_notes": [note for note in outcome["notes"] if "nothing appended" in note],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
