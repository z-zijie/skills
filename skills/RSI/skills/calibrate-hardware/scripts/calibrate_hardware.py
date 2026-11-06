#!/usr/bin/env python3
"""calibrate-hardware: run the microbenchmark suite and fold the result into the measured layer.

The microbenchmark code is inside the frozen evaluator, so this script runs the CLI and then does
the part that must be mechanical: merge the report, compare against the previous run, derive the
thresholds the tag set consumes, and flag the parameters that moved.

Two rules are enforced rather than documented. Every measured parameter is stored with the
environment fingerprint and the date that produced it, so a number from another fingerprint can
never be mistaken for a comparison. And a microbenchmark that could not run is reported with its
reason; the parameter stays absent instead of becoming a zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, ledger, read_request, yamlite  # noqa: E402
from rsi import signature  # noqa: E402

THRESHOLD_KEYS = tuple(signature.DEFAULT_THRESHOLDS)


def build_command(request: Dict[str, Any]) -> List[str]:
    command = [sys.executable, "-m", "evaluator.cli", "calibrate",
               "--suite", str(request.get("suite") or "core")]
    for flag in ("samples", "seed", "backend"):
        if request.get(flag) is not None:
            command += [f"--{flag}", str(request[flag])]
    if request.get("quick"):
        command.append("--quick")
    return command


def measured_path(hw_id: str) -> Path:
    return ROOT / "kb" / "hardware" / "measured" / f"{hw_id}.yaml"


def load_previous(request: Dict[str, Any], hw_id: str) -> Dict[str, Any]:
    named = request.get("previous_file")
    path = Path(str(named)) if named else measured_path(hw_id)
    if not path.is_absolute() and not path.is_file():
        path = ROOT / path
    if not path.is_file():
        return {}
    data = ledger.load_document(path)
    return data if isinstance(data, dict) else {}


def flat(value: Any) -> Optional[float]:
    """The number behind a parameter, whether it is a scalar or a small record."""
    if isinstance(value, dict):
        for key in ("threshold", "overhead_us"):
            if isinstance(value.get(key), (int, float)):
                return float(value[key])
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def reported_moved(merged: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The parameters the calibration workflow itself recorded as having moved.

    The comparison thresholds are part of the calibration protocol and live in Layer 0, so this
    skill reads the decision the workflow already made (``history[-1].moved``) instead of
    re-deciding it with numbers of its own. That keeps one definition of "moved" in the system.
    """
    history = merged.get("history") or []
    for entry in reversed(history):
        if isinstance(entry, dict) and entry.get("moved") is not None:
            return [item for item in entry["moved"] if isinstance(item, dict)]
    return []


def derive_thresholds(measured: Dict[str, Any]) -> Dict[str, float]:
    """Signature thresholds for this device, from its own measured curves.

    Values are only filled in when the measurement supports them; everything else keeps the
    device-neutral default, which is what lets a brand-new device inherit the whole system.
    """
    table = signature.threshold_table(measured)
    thresholds = {key: float(table[key]) for key in THRESHOLD_KEYS}
    noise = measured.get("noise_floor") or {}
    if isinstance(noise.get("threshold"), (int, float)):
        thresholds["min_reportable_speedup"] = round(float(noise["threshold"]), 4)
    coalescing = measured.get("coalescing")
    if isinstance(coalescing, dict) and coalescing:
        values = [value for value in coalescing.values() if isinstance(value, (int, float))]
        ideal = coalescing.get("1")
        worst = max(values) if values else None
        if isinstance(ideal, (int, float)) and worst and ideal:
            ratio = float(worst) / float(ideal)
            if ratio > signature.DEFAULT_THRESHOLDS["uncoalesced_sectors"]:
                thresholds["uncoalesced_sectors"] = round(float(ratio), 4)
    return thresholds


def run_live(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    command = build_command(request)
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                               timeout=int(request.get("timeout_s") or 7200), check=False)
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return None


def calibrate(request: Dict[str, Any]) -> Dict[str, Any]:
    empty = {"status": "unavailable", "hw_id": None, "measured": None, "moved": [],
             "thresholds": {}, "skipped": [], "notes": []}
    supplied = request.get("measured_file")
    if supplied:
        path = Path(str(supplied))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        if not path.is_file():
            return dict(empty, problems=[f"unavailable: no calibration report at {supplied}"])
        merged = ledger.load_document(path)
        if not isinstance(merged, dict):
            return dict(empty, problems=["schema: the stored calibration is not a mapping"])
    else:
        merged = run_live(request)
        if merged is None:
            return dict(empty, problems=["unavailable: the calibration suite did not produce a report"])

    if request.get("hw_id") and not merged.get("hw_id"):
        merged["hw_id"] = str(request["hw_id"])
    hw_id = str(merged.get("hw_id") or request.get("hw_id") or "")
    if not hw_id:
        return dict(empty, problems=["unavailable: the calibration report names no hardware id"])

    previous = load_previous(request, hw_id)
    moved = reported_moved(merged)
    if moved:
        merged["under_review"] = True
        merged["under_review_reason"] = (
            "parameters moved beyond their thresholds: rules that depend on them go under review")
    thresholds = derive_thresholds(merged)
    merged["thresholds"] = thresholds
    notes: List[str] = []
    skipped = [{"name": str(entry.get("name")), "reason": str(entry.get("reason") or "no reason given")}
               for entry in (merged.get("skipped") or []) if isinstance(entry, dict)]
    if skipped:
        notes.append(f"{len(skipped)} microbenchmark(s) did not run; their parameters stay unknown")
    if moved:
        notes.append(f"{len(moved)} parameter(s) moved beyond threshold; dependent rules go under review")
    if previous.get("env_fingerprint") and previous.get("env_fingerprint") != merged.get("env_fingerprint"):
        notes.append("the environment fingerprint changed since the previous run; this is a new "
                     "measurement, not a comparison")
    elif not previous:
        notes.append("no previous measurement for this hardware id; this run is the baseline")

    if request.get("dry_run"):
        return {"status": "dry_run", "hw_id": hw_id, "measured": merged, "moved": moved,
                "thresholds": thresholds, "skipped": skipped, "problems": [], "notes": notes}

    destination = measured_path(hw_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yamlite.safe_dump_json_style(merged), encoding="utf-8")
    return {"status": "ok", "hw_id": hw_id, "measured": merged, "moved": moved,
            "thresholds": thresholds, "skipped": skipped, "problems": [], "notes": notes,
            "report_ref": str(destination.relative_to(ROOT))}


def main(argv: List[str]) -> int:
    outcome = calibrate(read_request(argv))
    if outcome.get("measured") is not None:
        try:
            schema = jsonschema_lite.load_schema(
                str(ROOT / "skills" / "calibrate-hardware" / "contract" / "output.schema.json"))
            jsonschema_lite.check(outcome, schema, label="calibrate-hardware")
        except jsonschema_lite.ValidationError as error:
            outcome["problems"] = list(outcome.get("problems") or []) + \
                [f"schema: {line}" for line in error.errors]
            outcome["status"] = "schema_invalid"
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
