"""Bottleneck signatures: canonical metrics discretized into a class and a tag set.

A signature is the retrieval key for everything the knowledge base holds, so it is built by
code from thresholds, never by a model. Thresholds come from
``kb/hardware/measured/thresholds.yaml``, which the calibration workflow tunes, so moving to
a new device means re-tuning one file rather than rewriting any skill.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import metrics as metrics_mod

#: Starting thresholds. They are deliberately device-neutral; the calibration workflow
#: replaces them per hardware id, and every tag records which threshold produced it.
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "mem_saturated_pct": 80.0,
    "compute_saturated_pct": 80.0,
    "memory_bound_mem_pct": 60.0,
    "memory_bound_compute_pct": 60.0,
    "compute_bound_compute_pct": 60.0,
    "uncoalesced_sectors": 1.5,
    "cache_hit_low_pct": 60.0,
    "occupancy_low_pct": 50.0,
    "memory_stall_high": 0.4,
    "scratchpad_conflict_ratio": 1.2,
    "divergence_high_pct": 20.0,
    "register_pressure_fraction": 0.75,
    "launch_dominated_fraction": 0.10,
    "plateau_rounds": 3,
    "target_attainment_pct": 85.0,
}

#: Tags that describe the operator rather than the machine state; they are carried through
#: retrieval but never derived from a counter.
STRUCTURAL_TAGS = ("plateau",)


def threshold_table(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """DEFAULT_THRESHOLDS with a hardware-specific ``thresholds`` block applied on top."""
    table = dict(DEFAULT_THRESHOLDS)
    for key, value in ((overrides or {}).get("thresholds") or {}).items():
        if key in table and isinstance(value, (int, float)):
            table[key] = float(value)
    return table


def _value(values: Dict[str, Any], name: str) -> Optional[float]:
    raw = values.get(name)
    if raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def classify(values: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None) -> str:
    """Pick the bottleneck class from the two utilisation numbers.

    Missing utilisation is treated as unknown and yields ``latency_bound`` only when the
    attainment is low; otherwise the class is decided by whichever utilisation is present, so
    a device that cannot report one counter still produces a usable signature.
    """
    table = thresholds or DEFAULT_THRESHOLDS
    mem = _value(values, "device_mem_throughput_pct")
    compute = _value(values, "compute_throughput_pct")
    attained = _value(values, "attained_pct")
    if mem is None and compute is None:
        return "latency_bound" if (attained is None or attained < table["memory_bound_mem_pct"]) else "mixed"
    if mem is not None and compute is not None:
        if mem >= table["memory_bound_mem_pct"] and compute < table["memory_bound_compute_pct"]:
            return "memory_bound"
        if compute >= table["compute_bound_compute_pct"] and mem < table["memory_bound_mem_pct"]:
            return "compute_bound"
        if mem >= table["memory_bound_mem_pct"] and compute >= table["compute_bound_compute_pct"]:
            return "mixed"
        return "latency_bound"
    if mem is not None:
        return "memory_bound" if mem >= table["memory_bound_mem_pct"] else "latency_bound"
    return "compute_bound" if compute >= table["compute_bound_compute_pct"] else "latency_bound"


def tags(values: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None,
         extra: Optional[Iterable[str]] = None) -> List[str]:
    """Every threshold decision that fires, plus caller-supplied structural tags."""
    table = thresholds or DEFAULT_THRESHOLDS
    out: List[str] = []

    mem = _value(values, "device_mem_throughput_pct")
    if mem is not None and mem >= table["mem_saturated_pct"]:
        out.append("mem_saturated")
    sectors = _value(values, "sectors_per_request")
    if sectors is not None and sectors >= table["uncoalesced_sectors"]:
        out.append("uncoalesced")
    cache_hit = _value(values, "cache_hit_pct")
    if cache_hit is not None and cache_hit < table["cache_hit_low_pct"]:
        out.append("cache_hit_low")
    compute = _value(values, "compute_throughput_pct")
    if compute is not None and compute >= table["compute_saturated_pct"]:
        out.append("compute_saturated")
    occupancy = _value(values, "occupancy_pct")
    if occupancy is not None and occupancy < table["occupancy_low_pct"]:
        out.append("occupancy_low")
    stall = _value(values, "stall_memory")
    if stall is not None and stall >= table["memory_stall_high"]:
        out.append("memory_stall_high")
    conflict = _value(values, "scratchpad_conflict_ratio")
    if conflict is not None and conflict >= table["scratchpad_conflict_ratio"]:
        out.append("scratchpad_conflict")
    divergence = _value(values, "branch_divergence_pct")
    if divergence is not None and divergence >= table["divergence_high_pct"]:
        out.append("divergence_high")
    registers = _value(values, "registers_per_lane")
    register_max = _value(values, "registers_per_lane_max")
    if registers is not None and register_max and registers >= table["register_pressure_fraction"] * register_max:
        out.append("register_pressure")
    spills = _value(values, "spill_bytes")
    if spills is not None and spills > 0:
        out.append("spills")
    launch = _value(values, "launch_overhead_us")
    measured = _value(values, "measured_us")
    if launch is not None and measured and launch >= table["launch_dominated_fraction"] * measured:
        out.append("launch_dominated")
    for tag in extra or []:
        if tag not in out:
            out.append(tag)
    return out


def build(op: str, hw_id: str, values: Dict[str, Any], roofline: Dict[str, Any],
          thresholds: Optional[Dict[str, float]] = None,
          extra_tags: Optional[Iterable[str]] = None,
          env_fingerprint: str = "") -> Dict[str, Any]:
    """Assemble a signature object that validates against signature.schema.json."""
    table = thresholds or DEFAULT_THRESHOLDS
    merged = dict(values)
    if "measured_us" not in merged and roofline.get("measured_us") is not None:
        merged["measured_us"] = roofline["measured_us"]
    if "attained_pct" not in merged and roofline.get("attained_pct") is not None:
        merged["attained_pct"] = roofline["attained_pct"]
    classe = classify(merged, table)
    tag_list = tags(merged, table, extra_tags)
    reported = {
        name: merged.get(name) for name in metrics_mod.METRICS
        if merged.get(name) is not None
    }
    signature = {
        "op": op,
        "hw_id": hw_id,
        "class": classe,
        "tags": tag_list,
        "metrics": metrics_mod.normalise(reported),
        "roofline": {
            "bound_us": float(roofline["bound_us"]),
            "measured_us": float(roofline["measured_us"]),
            "attained_pct": float(roofline["attained_pct"]),
        },
        "primary_bottleneck": primary_bottleneck(classe, tag_list),
        "notes": "",
    }
    if roofline.get("limiting_term"):
        signature["roofline"]["limiting_term"] = roofline["limiting_term"]
    if roofline.get("assumptions"):
        signature["roofline"]["assumptions"] = list(roofline["assumptions"])
    if env_fingerprint:
        signature["env_fingerprint"] = env_fingerprint
    return signature


#: Which playbook a class plus tag set should reach first. The playbook files declare the
#: signatures they apply to; this ordering breaks ties when several match.
_PLAYBOOK_PRIORITY: Sequence[tuple] = (
    ("plateau", "plateau"),
    ("scratchpad_conflict", "smem-conflict"),
    ("divergence_high", "divergence"),
    ("occupancy_low", "low-occupancy"),
    ("uncoalesced", "mem-cache-miss"),
    ("cache_hit_low", "mem-cache-miss"),
    ("mem_saturated", "mem-dram-saturated"),
    ("compute_saturated", "compute-bound"),
    ("launch_dominated", "latency-bound"),
)


def primary_bottleneck(classe: str, tag_list: Iterable[str]) -> str:
    """A single, named bottleneck: the first specific tag that fires, else the class."""
    tag_set = set(tag_list)
    for tag, playbook in _PLAYBOOK_PRIORITY:
        if tag in tag_set:
            return playbook
    return {
        "memory_bound": "mem-dram-saturated",
        "compute_bound": "compute-bound",
        "latency_bound": "latency-bound",
        "mixed": "mem-dram-saturated",
    }.get(classe, "plateau")


def tag_metric(tag: str) -> Optional[str]:
    return metrics_mod.TAG_METRICS.get(tag)


def match_score(signature: Dict[str, Any], entry_scope: Dict[str, Any]) -> int:
    """How well a stored entry's scope matches a signature.

    The retrieval rule is exact tag matching first, then a step-by-step relaxation: this
    score is what makes that ordering computable. Higher is better; 0 means no match at all.
    """
    score = 0
    hw = entry_scope.get("hw") or []
    if hw and signature.get("hw_id") not in hw:
        return 0
    if hw:
        score += 4
    tags_all = entry_scope.get("tags_all") or []
    membership = set(signature.get("tags") or [])
    if tags_all:
        if not set(tags_all) <= membership:
            return 0
        score += 3 * len(tags_all)
    tags_any = entry_scope.get("tags_any") or []
    if tags_any:
        hits = len(set(tags_any) & membership)
        if hits == 0:
            return 0
        score += hits
    classes = entry_scope.get("classes") or []
    if classes:
        if signature.get("class") not in classes:
            return 0
        score += 2
    return score
