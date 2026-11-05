"""Run a microbenchmark suite and merge its results into the measured layer.

Results expire, so every run stamps the environment fingerprint and the date, and compares
itself with the previous run. A parameter that moved beyond its threshold marks the rules that
depend on it ``under review`` — which is how old rules stop quietly working after a runtime
upgrade.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..tooling_path import hardware, paths, yamlite
from . import kernels, suite as suite_mod

#: How far a parameter may move between calibrations before dependent rules are flagged.
CHANGE_THRESHOLDS = {
    "peak_bandwidth_gbps": 0.10,
    "peak_flops": 0.15,
    "noise_floor": 0.25,
    "launch": 0.30,
}


def run_suite(name: str = "core", backend: Any = None, device: Optional[Dict[str, Any]] = None,
              samples: int = 15, seed: int = 0, quick: bool = False) -> Dict[str, Any]:
    """Run every applicable microbenchmark and collect its parameter."""
    factory = suite_mod.SUITES.get(name)
    if factory is None:
        raise SystemExit(f"unknown suite {name!r}; available: {', '.join(sorted(suite_mod.SUITES))}")
    bench_list = factory()
    device = device or hardware.discover_device()
    results: Dict[str, Any] = {}
    skipped: List[Dict[str, str]] = []
    for bench in bench_list:
        if not bench.applicable(backend):
            skipped.append({"name": bench.name, "reason": f"backend lacks {bench.requires}"})
            continue
        function = kernels.IMPLEMENTATIONS.get(bench.name)
        if function is None:
            skipped.append({"name": bench.name, "reason": "no implementation"})
            continue
        started = time.time()
        try:
            outcome = function(backend, samples=samples, seed=seed)
        except Exception as error:  # a broken microbenchmark must not poison the whole suite
            skipped.append({"name": bench.name, "reason": f"{type(error).__name__}: {error}"})
            continue
        if outcome.get("value") is None:
            skipped.append({"name": bench.name, "reason": outcome.get("note", "no measurable value")})
            continue
        results[bench.name] = {
            "parameter": bench.parameter,
            "value": outcome["value"],
            "curve": outcome.get("curve", {}),
            "unit": outcome.get("unit", ""),
            "note": outcome.get("note", ""),
            "seconds": round(time.time() - started, 3),
        }
    return {
        "suite": name,
        "hw_id": str(device.get("hw_id", "")),
        "env_fingerprint": hardware.env_fingerprint(device),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": getattr(backend, "name", "unknown"),
        "backend_description": backend.device_description() if hasattr(backend, "device_description") else {},
        "measured": _what_was_measured(backend, device),
        "quick": quick,
        "results": results,
        "skipped": skipped,
    }


def _what_was_measured(backend: Any, device: Dict[str, Any]) -> Dict[str, Any]:
    """Say plainly whether these numbers describe the accelerator or the host running them.

    A device description that claims an accelerator while the backend drives a host array library
    produces numbers about the host. Recording that here is the difference between a calibration
    that is wrong and one that is honestly misconfigured: the roofline built on host numbers will
    show every kernel at a few percent attainment, and the campaign will chase a bound that is
    twenty times too high.
    """
    declared = str(device.get("hw_class") or "other")
    description = backend.device_description() if hasattr(backend, "device_description") else {}
    module = str(description.get("array_module") or "")
    backend_name = getattr(backend, "name", "unknown")
    on_accelerator = True
    note = ""
    if declared in {"gpu", "npu"}:
        if backend_name == "cpu":
            on_accelerator = False
            note = ("the device describes an accelerator but the reference backend ran: these "
                    "numbers describe the host, not the device")
        elif module in {"numpy"} and declared in {"gpu", "npu"}:
            on_accelerator = False
            note = (f"the device describes an accelerator but the array module is {module!r}: these "
                    "numbers describe the host. Point device.yaml at the device runtime "
                    "(array_module) and re-measure before trusting any roofline")
    return {
        "on_declared_accelerator": on_accelerator,
        "declared_hw_class": declared,
        "backend": backend_name,
        "array_module": module or None,
        "note": note,
    }


def merge_into_measured(path: Path, report: Dict[str, Any], device: Dict[str, Any]) -> Dict[str, Any]:
    """Fold a calibration report into ``kb/hardware/measured/<hw_id>.yaml``.

    The previous file is kept as a ``history`` entry so a review can see what moved, and the
    comparison decides whether dependent rules must go ``under review``.
    """
    previous = yamlite.load(path) if path.is_file() else {}
    previous = previous if isinstance(previous, dict) else {}
    merged = dict(previous)
    results = report.get("results", {})
    moved: List[Dict[str, Any]] = []

    def put(key: str, value: Any, threshold_key: str) -> None:
        if value is None:
            return
        before = _flat_get(previous, key)
        merged[key] = value
        if isinstance(before, (int, float)) and isinstance(value, (int, float)) and before:
            change = abs(value - before) / abs(before)
            limit = CHANGE_THRESHOLDS.get(threshold_key, 0.2)
            if change > limit:
                moved.append({"parameter": key, "before": before, "after": value,
                              "relative_change": round(change, 3), "threshold": limit})

    measured_where = report.get("measured") or {}
    if "memory_bandwidth" in results:
        put("peak_bandwidth_gbps", _number(results["memory_bandwidth"]["value"]), "peak_bandwidth_gbps")
    if measured_where:
        merged["measured_on_declared_accelerator"] = bool(measured_where.get("on_declared_accelerator"))
        merged["measurement_note"] = measured_where.get("note", "")
        merged["measurement_backend"] = measured_where
    if "peak_compute" in results:
        put("peak_flops", results["peak_compute"]["value"], "peak_flops")
    if "noise_floor" in results:
        put("noise_floor", {"threshold": _number(results["noise_floor"]["value"]),
                            "relative_spread": results["noise_floor"]["curve"].get("relative_spread"),
                            "measured_at": report["measured_at"]}, "noise_floor")
    if "launch_overhead" in results:
        put("launch", {"overhead_us": _number(results["launch_overhead"]["value"])}, "launch")
    if "drift" in results:
        put("drift", results["drift"]["curve"], "noise_floor")
    if "coalescing_penalty" in results:
        put("coalescing", results["coalescing_penalty"]["value"], "peak_bandwidth_gbps")
    if "cache_hierarchy" in results:
        put("cache", results["cache_hierarchy"]["value"], "peak_bandwidth_gbps")
    if "sync_cost" in results:
        put("sync", results["sync_cost"]["value"], "launch")
    if "atomics" in results:
        put("atomics", results["atomics"]["value"], "launch")

    merged["hw_id"] = report.get("hw_id") or device.get("hw_id")
    merged.setdefault("hw_class", device.get("hw_class", "other"))
    merged.setdefault("model", device.get("model"))
    merged["env_fingerprint"] = report.get("env_fingerprint")
    merged["measured_at"] = report.get("measured_at")
    merged["calibration_suite"] = report.get("suite")

    history = list(merged.get("history") or [])
    history.append({
        "at": report.get("measured_at"),
        "suite": report.get("suite"),
        "fingerprint": report.get("env_fingerprint"),
        "moved": moved,
        "skipped": [entry["name"] for entry in report.get("skipped", [])],
    })
    merged["history"] = history[-20:]
    if measured_where and not measured_where.get("on_declared_accelerator", True):
        # The peaks are real numbers about the wrong machine. They are kept so the mistake is
        # visible, and marked so nothing downstream treats them as the device's ceiling.
        merged["trustworthy_for_roofline"] = False
        merged["under_review"] = True
        merged["under_review_reason"] = (
            measured_where.get("note")
            or "these numbers were measured on the host rather than the declared accelerator"
        )
    else:
        merged["trustworthy_for_roofline"] = True
    merged["under_review"] = bool(merged.get("under_review") or moved)
    if moved:
        merged["under_review_reason"] = (
            "parameters moved beyond their thresholds: rules that depend on them go under review"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yamlite.safe_dump_json_style(merged), encoding="utf-8")
    return merged


def _flat_get(document: Dict[str, Any], key: str) -> Any:
    value = document.get(key)
    if isinstance(value, dict):
        for candidate in ("threshold", "overhead_us"):
            if candidate in value:
                return value[candidate]
        return None
    return value


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def propose_thresholds(report: Dict[str, Any], existing: Dict[str, Any]) -> Dict[str, Any]:
    """Derive signature thresholds from measured curves rather than from taste.

    This is what lets a new device inherit the whole system: the tags stop being tuned by hand
    and start being tuned by the numbers the calibration suite produced.
    """
    thresholds = dict((existing or {}).get("thresholds") or {})
    noise = (existing or {}).get("noise_floor") or {}
    if noise.get("threshold"):
        # A device with a wide noise floor needs a bigger margin before a tag is called.
        thresholds.setdefault("min_reportable_speedup", round(float(noise["threshold"]), 3))
    coalescing = (existing or {}).get("coalescing") or {}
    if isinstance(coalescing, dict) and coalescing:
        first = coalescing.get("1")
        worst = max((value for value in coalescing.values() if isinstance(value, (int, float))),
                    default=None)
        if first and worst and worst / first > 1.5:
            thresholds.setdefault("uncoalesced_sectors", 1.5)
    return thresholds
