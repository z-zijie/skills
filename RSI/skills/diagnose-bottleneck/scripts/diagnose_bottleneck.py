#!/usr/bin/env python3
"""diagnose-bottleneck: discretize canonical metrics into a signature and compute attainment.

The judgement is almost entirely mechanical, so almost all of it lives in ``tooling/rsi``:
``signature.build`` decides the class and the tags against this device's thresholds,
``hardware.Roofline`` decides the bound and the attainment from the measured layer, and
``signature.primary_bottleneck`` picks the one playbook name the campaign opens.

What this script adds is the two things a threshold cannot do: refusing to compare across an
environment fingerprint, and stating which metrics the diagnosis rested on and which were absent.
A signature built from attainment alone is legitimate and is labelled ``provisional``; one built
from counters is ``ok``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, jsonschema_lite as _jsl, ledger, metrics, read_request  # noqa: E402
from rsi import hardware, signature as signature_mod, spec as spec_mod  # noqa: E402

CLASS_METRICS = ("device_mem_throughput_pct", "compute_throughput_pct")


def load_thresholds(request: Dict[str, Any], hw_id: str) -> Dict[str, Any]:
    """This device's threshold block: inline, from a file, from the measured snapshot, or from
    the calibration table. The first source that carries a ``thresholds`` block wins."""
    candidates: List[Any] = [request.get("thresholds")]
    for key in ("thresholds_file", "measured_file"):
        named = request.get(key)
        if not named:
            continue
        path = Path(str(named))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        if path.is_file():
            candidates.append(ledger.load_document(path))
    candidates.append(hardware.load_thresholds(ROOT))
    candidates.append(load_measured(hw_id, request))
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("thresholds"), dict):
            return candidate
    return {}


def load_measured(hw_id: str, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The measured layer for this device, or the snapshot a replay pinned."""
    named = (request or {}).get("measured_file")
    if named:
        path = Path(str(named))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        if path.is_file():
            data = ledger.load_document(path)
            return data if isinstance(data, dict) else {}
    try:
        return hardware.load_measured(hw_id, ROOT)
    except hardware.HardwareError:
        return {}


def roofline_for(request: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Bound and attainment from the spec's cost model and the measured layer.

    The roofline uses device-memory bytes only, because that is the one level whose bandwidth is
    a fixed device property. Without a measured peak the bound cannot be computed, and the skill
    says so rather than inventing one.
    """
    measured_us = request.get("measured_us")
    if measured_us is None:
        return {"ok": False, "reason": "no measured_us: the roofline needs the evaluator's timing"}

    measured_layer = load_measured(str(profile.get("hw_id") or request.get("hw_id") or ""), request)
    if not measured_layer or measured_layer.get("peak_bandwidth_gbps") is None:
        return {"ok": False, "reason": "the measured layer has no peak bandwidth for this hardware id"}

    source = request.get("spec")
    if not isinstance(source, dict):
        named = request.get("spec_file")
        if not named:
            op_spec = spec_mod.Spec.load(str(request["op"]))
            source = op_spec.data
        else:
            path = Path(str(named))
            if not path.is_absolute() and not path.is_file():
                path = ROOT / path
            source = ledger.load_document(path)
    model = source.get("cost_model") or {}
    case = request.get("shape") or (source.get("shapes") or {}).get("typical", [{}])[0]
    dtype = request.get("dtype") or (source.get("dtype") or ["fp32"])[0]

    try:
        if isinstance(request.get("spec"), dict):
            op_spec = spec_mod.Spec(dict(source))
        else:
            op_spec = spec_mod.Spec.load(str(request["op"]))
    except (spec_mod.SpecError, KeyError, OSError) as error:
        return {"ok": False, "reason": f"cannot load the operator spec: {error}"}
    costs = op_spec.model(case, dtype)
    roofline = hardware.Roofline.from_measured(measured_layer)
    try:
        report = roofline.attainment(costs["bytes"], costs["flops"], float(measured_us), dtype)
    except (hardware.HardwareError, ValueError) as error:
        return {"ok": False, "reason": str(error)}
    return {"ok": True, "roofline": report, "bytes": costs["bytes"], "flops": costs["flops"],
            "limiting_term": report.get("limiting_term")}


def diagnose(request: Dict[str, Any]) -> Dict[str, Any]:
    notes: List[str] = []
    problems: List[str] = []

    profile = request.get("profile")
    if not isinstance(profile, dict):
        named = request.get("profile_file")
        if not named:
            return {"status": "unavailable", "signature": None, "problems": ["unavailable: no profile"],
                    "notes": notes}
        path = Path(str(named))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        if not path.is_file():
            return {"status": "unavailable", "signature": None,
                    "problems": [f"unavailable: no profile at {named}"], "notes": notes}
        profile = ledger.load_document(path)

    hw_id = str(request.get("hw_id") or profile.get("hw_id") or "")
    if not hw_id:
        return {"status": "unavailable", "signature": None,
                "problems": ["unavailable: the profile names no hardware id"], "notes": notes}
    availability = [metrics.canonical(str(name)) for name in (profile.get("unavailable") or [])]
    values = {metrics.canonical(str(key)): value for key, value in (profile.get("metrics") or {}).items()}
    values = {key: value for key, value in values.items() if value is not None}
    metrics_missing = sorted(name for name in metrics.METRICS if name not in values)
    metrics_used = sorted(values)

    bound = roofline_for(request, profile)
    if not bound["ok"]:
        return {"status": "unavailable", "signature": None,
                "problems": [f"unavailable: {bound['reason']}"], "notes": notes}
    roofline = bound["roofline"]

    table = signature_mod.threshold_table(load_thresholds(request, hw_id))
    sig = signature_mod.build(
        str(request.get("op") or profile.get("op") or ""), hw_id, values, roofline,
        thresholds=table, extra_tags=request.get("extra_tags") or [],
        env_fingerprint=str(profile.get("env_fingerprint") or ""),
    )
    if request.get("notes"):
        sig["notes"] = str(request["notes"])

    utilization_present = [name for name in CLASS_METRICS if name in values]
    status = "ok" if utilization_present else "provisional"
    if not utilization_present:
        notes.append("no utilization counter was available: the class rests on attainment and "
                     "structural tags, so a hypothesis built on it predicts no measurable counter")
        notes.append(f"primary_bottleneck {sig.get('primary_bottleneck')!r} is the class default for "
                     f"{sig.get('class')!r}, not a measured attribution")
    unavailable_used = [name for name in metrics_used if name in availability]
    if unavailable_used:
        problems.append(f"schema: {unavailable_used} are listed as unavailable yet carry a value")

    try:
        jsonschema_lite.check(sig, _jsl.load_schema(str(ROOT / "schemas" / "signature.schema.json")),
                             label="signature")
    except jsonschema_lite.ValidationError as error:
        return {"status": "schema_invalid", "signature": sig,
                "problems": list(problems) + [f"schema: {line}" for line in error.errors],
                "notes": notes}

    gap = hardware.Roofline.from_measured(load_measured(hw_id, request)).gap(
        float(roofline["attained_pct"]), float(table["target_attainment_pct"]))
    return {
        "status": status,
        "signature": sig,
        "problems": problems,
        "notes": notes,
        "based_on": {
            "metrics_used": metrics_used,
            "metrics_missing": metrics_missing,
            "thresholds_source": "measured" if load_thresholds(request, hw_id) else "default",
            "limiting_term": str(roofline.get("limiting_term") or ""),
        },
        "gap": gap,
    }


def main(argv: List[str]) -> int:
    outcome = diagnose(read_request(argv))
    if outcome.get("signature"):
        try:
            schema = jsonschema_lite.load_schema(
                str(ROOT / "skills" / "diagnose-bottleneck" / "contract" / "output.schema.json"))
            jsonschema_lite.check(outcome, schema, label="diagnose-bottleneck")
        except jsonschema_lite.ValidationError as error:
            outcome["problems"] = list(outcome.get("problems") or []) + \
                [f"schema: {line}" for line in error.errors]
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
