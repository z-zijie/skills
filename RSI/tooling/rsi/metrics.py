"""The canonical metric registry.

One place decides what a metric means, what unit it carries, which direction is better, and
which legacy spellings map onto it. Adapters produce canonical names, signatures discretize
canonical names, rule cards cite canonical names, and validation rejects anything that is not
registered here. That is what keeps a rule readable after the device changes.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Canonical metric -> (unit, better direction, one-line meaning).
# Direction is "higher" when a larger value indicates a worse state, "lower" when a larger
# value indicates a better state, or None when the metric is descriptive.
METRICS: Dict[str, Dict[str, Optional[str]]] = {
    "device_mem_throughput_pct": {
        "unit": "% of measured peak",
        "worse_when": "higher",
        "meaning": "device-memory bandwidth utilisation",
    },
    "device_mem_bytes": {
        "unit": "byte",
        "worse_when": None,
        "meaning": "bytes moved to and from device memory in the profiled window",
    },
    "cache_hit_pct": {
        "unit": "%",
        "worse_when": "lower",
        "meaning": "hit rate of the outermost hardware-managed on-chip cache level",
    },
    "cache_throughput_pct": {
        "unit": "% of peak",
        "worse_when": "higher",
        "meaning": "pressure on the hardware-managed on-chip cache levels",
    },
    "compute_throughput_pct": {
        "unit": "% of peak",
        "worse_when": "higher",
        "meaning": "utilisation of the dominant compute pipe",
    },
    "occupancy_pct": {
        "unit": "%",
        "worse_when": "lower",
        "meaning": "resident work lanes over the device maximum",
    },
    "sectors_per_request": {
        "unit": "transactions / request",
        "worse_when": "higher",
        "meaning": "device-memory transactions per request; 1.0 is ideal contiguity",
    },
    "stall_memory": {
        "unit": "fraction of issue slots",
        "worse_when": "higher",
        "meaning": "issue slots idle waiting on device memory",
    },
    "stall_barrier": {
        "unit": "fraction of issue slots",
        "worse_when": "higher",
        "meaning": "issue slots idle waiting on a work-group barrier",
    },
    "scratchpad_throughput_pct": {
        "unit": "% of peak",
        "worse_when": "higher",
        "meaning": "pressure on scratchpad bandwidth",
    },
    "scratchpad_conflict_ratio": {
        "unit": "conflicts / access",
        "worse_when": "higher",
        "meaning": "scratchpad bank or vault conflicts per access",
    },
    "scratchpad_bytes_per_workgroup": {
        "unit": "byte",
        "worse_when": "higher",
        "meaning": "static scratchpad allocation per work-group",
    },
    "registers_per_lane": {
        "unit": "register",
        "worse_when": "higher",
        "meaning": "register pressure per work-lane",
    },
    "spill_bytes": {
        "unit": "byte",
        "worse_when": "higher",
        "meaning": "register spills to local memory",
    },
    "branch_divergence_pct": {
        "unit": "%",
        "worse_when": "higher",
        "meaning": "work lanes that take a different path than the rest of their group",
    },
    "launch_overhead_us": {
        "unit": "us",
        "worse_when": "higher",
        "meaning": "fixed launch cost measured by the calibration suite",
    },
    "instruction_count": {
        "unit": "instruction",
        "worse_when": "higher",
        "meaning": "instructions executed in the kernel",
    },
}

#: Legacy or vendor-flavoured spellings accepted on read and mapped to canonical names.
#: Acceptance keeps old cards and old ledgers readable; every writer emits canonical names.
ALIASES: Dict[str, str] = {
    "dram_throughput_pct": "device_mem_throughput_pct",
    "hbm_throughput_pct": "device_mem_throughput_pct",
    "gpu__dram_throughput_pct": "device_mem_throughput_pct",
    "hbm_bandwidth_pct": "device_mem_throughput_pct",
    "sm_throughput_pct": "compute_throughput_pct",
    "npu_compute_throughput_pct": "compute_throughput_pct",
    "achieved_occupancy_pct": "occupancy_pct",
    "l1tex_throughput_pct": "scratchpad_throughput_pct",
    "smem_throughput_pct": "scratchpad_throughput_pct",
    "smem_bank_conflicts": "scratchpad_conflict_ratio",
    "l2_hit_pct": "cache_hit_pct",
    "l2_throughput_pct": "cache_throughput_pct",
    "stall_long_scoreboard": "stall_memory",
    "registers_per_thread": "registers_per_lane",
    "v_ratio": "compute_throughput_pct",
}

#: Metrics whose hardware counters can be misleading on some devices. The verifier must
#: cross-check an alternative before it rests a verdict on one of these.
UNRELIABLE_BY_DESIGN: List[str] = ["scratchpad_conflict_ratio"]

#: Tag -> canonical metric the tag is derived from. The threshold itself lives per hardware
#: id in ``kb/hardware/measured/thresholds.yaml``.
TAG_METRICS: Dict[str, Optional[str]] = {
    "mem_saturated": "device_mem_throughput_pct",
    "uncoalesced": "sectors_per_request",
    "cache_hit_low": "cache_hit_pct",
    "compute_saturated": "compute_throughput_pct",
    "occupancy_low": "occupancy_pct",
    "memory_stall_high": "stall_memory",
    "scratchpad_conflict": "scratchpad_conflict_ratio",
    "divergence_high": "branch_divergence_pct",
    "register_pressure": "registers_per_lane",
    "spills": "spill_bytes",
    "launch_dominated": "launch_overhead_us",
    "plateau": None,
}

CLASSES = ("memory_bound", "compute_bound", "latency_bound", "mixed")


class UnknownMetric(KeyError):
    """Raised when a document names a metric that is not registered."""


def canonical(name: str) -> str:
    """Map a legacy spelling to its canonical name, or return the name unchanged."""
    return ALIASES.get(name, name)


def is_known(name: str) -> bool:
    return canonical(name) in METRICS


def require_known(name: str) -> str:
    resolved = canonical(name)
    if resolved not in METRICS:
        known = ", ".join(sorted(METRICS))
        raise UnknownMetric(f"{name!r} is not a canonical metric; register it in docs/vocabulary.md first (known: {known})")
    return resolved


def unit(name: str) -> Optional[str]:
    return METRICS[require_known(name)]["unit"]


def better_direction(name: str) -> Optional[str]:
    """'higher' when an increase means worse, 'lower' when an increase means better."""
    return METRICS[require_known(name)]["worse_when"]


def normalise(mapping: Dict[str, object]) -> Dict[str, object]:
    """Rewrite a metric->value mapping to canonical names, keeping unknown keys as they are."""
    out: Dict[str, object] = {}
    for key, value in (mapping or {}).items():
        out[canonical(str(key))] = value
    return out


def check_names(names: List[str]) -> List[str]:
    """Return the unknown names in ``names`` (empty when all are registered)."""
    return [name for name in names if canonical(name) not in METRICS]
