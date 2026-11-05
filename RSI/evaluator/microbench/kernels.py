"""The fourteen microbenchmarks, each one producing a parameter the system consumes.

Every function returns ``{"value": ..., "curve": ..., "unit": ..., "note": ...}``. A value is
never invented: when a measurement is impossible on the selected backend, the entry says so
and the parameter is left out of the measured layer, which makes downstream skills treat it as
unknown rather than as zero.

All work is device work through ``backends.ops``: a loop over the reference backend on a host,
a real vector kernel on a GPU or an NPU. Nothing here names a vendor.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List

from .. import backends as backends_mod
from ..backends import ops as ops_mod
from ..harness import timing as protocol
from ..tooling_path import timing

#: Fixed element counts so runs are comparable between invocations.
SMALL = 1 << 12
MEDIUM = 1 << 16
LARGE = 1 << 18
DTYPES = ("fp32",)


def _time(fn, backend: Any, samples: int) -> List[float]:
    return protocol.measure(fn, backend, samples=samples, warmup=2)


def _ops(backend: Any) -> ops_mod.OpsBase:
    return ops_mod.for_backend(backend)


def noise_floor(backend: Any, samples: int = 15, **_: Any) -> Dict[str, Any]:
    """A/A: the same kernel as two arms. The fake difference is the noise floor."""
    ops = _ops(backend)
    source = backend.from_list([1.0] * MEDIUM)
    destination = backend.zeros(MEDIUM)
    arm = lambda: ops.copy(source, destination)
    report = protocol.aa_test(arm, backend, samples=samples, warmup=2)
    return {
        "value": report["threshold"],
        "curve": {"relative_spread": report["relative_spread"], "arm_a_us": report["arm_a_us"],
                  "arm_b_us": report["arm_b_us"]},
        "unit": "speedup ratio a real candidate must beat",
        "note": "measured here, never assumed; re-measure after a runtime upgrade",
    }


def drift(backend: Any, samples: int = 15, **_: Any) -> Dict[str, Any]:
    """How long warm-up must be, and how far throughput moves once it settles."""
    ops = _ops(backend)
    source = backend.from_list([1.0] * MEDIUM)
    destination = backend.zeros(MEDIUM)
    report = protocol.drift_test(lambda: ops.copy(source, destination), backend,
                               duration_samples=max(20, samples * 6))
    return {
        "value": report["drift_fraction"],
        "curve": report,
        "unit": "fractional throughput change from start to plateau",
        "note": f"warm-up of {int(report['warmup_iterations'])} iterations reached a plateau",
    }


def memory_bandwidth(backend: Any, samples: int = 15, **_: Any) -> Dict[str, Any]:
    """Peak device-memory bandwidth: the bandwidth term of the roofline."""
    ops = _ops(backend)
    curve: Dict[str, float] = {}
    best = 0.0
    for size in (SMALL, MEDIUM, LARGE):
        source = backend.from_list([1.0] * size)
        destination = backend.zeros(size)
        elapsed = timing.median(_time(lambda: ops.copy(source, destination), backend, samples))
        moved = size * 4 * 2  # read plus write, fp32
        gbps = moved / elapsed / 1e3 if elapsed else 0.0
        curve[str(size)] = gbps
        best = max(best, gbps)
    return {
        "value": best,
        "curve": curve,
        "unit": "GB/s",
        "note": "copy of a decreasing fraction of the working set; the best size sets the peak",
    }


def peak_compute(backend: Any, samples: int = 15, **_: Any) -> Dict[str, Any]:
    """Peak compute from a fused-multiply-add loop, per dtype."""
    ops = _ops(backend)
    size = MEDIUM
    a = backend.from_list([1.5] * size)
    b = backend.from_list([1.0000001] * size)
    c = backend.zeros(size)
    out = backend.zeros(size)
    elapsed = timing.median(_time(lambda: ops.mul_add(a, b, c, out), backend, samples))
    flops = size * 2  # one multiply and one add per element
    value = flops / elapsed * 1e6 / 1e9 if elapsed else 0.0
    return {
        "value": {"fp32": value},
        "curve": {f"elements_{size}": elapsed},
        "unit": "GFLOP/s per dtype",
        "note": "device peak is a ceiling, not an achievement: compare attainment, not raw GFLOP/s",
    }


def cache_hierarchy(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Where latency steps up as the working set outgrows each cache level."""
    ops = _ops(backend)
    curve: Dict[str, float] = {}
    sizes = [1 << power for power in range(8, 21, 2)]
    for size in sizes:
        source = backend.from_list([1.0] * size)
        destination = backend.zeros(size)
        curve[str(size)] = timing.median(_time(lambda: ops.copy(source, destination), backend, samples))
    steps: List[Dict[str, Any]] = []
    previous = None
    for size in sizes:
        current = curve[str(size)]
        if previous is not None and previous > 0:
            ratio = current / previous
            if ratio > 1.6:
                steps.append({"at_elements": size, "step_ratio": ratio})
        previous = current
    return {
        "value": {"steps": steps, "curve": curve},
        "curve": curve,
        "unit": "microseconds per copy, by working-set size",
        "note": "a step up in per-element cost marks a capacity boundary",
    }


def coalescing_penalty(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Throughput versus access stride: what uncoalesced access costs on this device."""
    ops = _ops(backend)
    size = MEDIUM
    source = backend.from_list([1.0] * size)
    destination = backend.zeros(size // 8)
    curve: Dict[str, float] = {}
    baseline = None
    for stride in (1, 2, 4, 8):
        elapsed = timing.median(_time(lambda: ops.strided_read(source, stride, destination),
                                      backend, samples))
        curve[str(stride)] = elapsed
        if stride == 1:
            baseline = elapsed
    slowdown = {stride: (value / baseline if baseline else 0.0) for stride, value in curve.items()}
    return {
        "value": slowdown,
        "curve": curve,
        "unit": "slowdown relative to stride 1",
        "note": "estimates the gain from making access contiguous",
    }


def load_width(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Throughput ratio of wide versus narrow loads.

    The backend decides what a "wide load" is: an array module vectorises, the reference
    backend does not, and the report says which happened instead of pretending.
    """
    ops = _ops(backend)
    size = MEDIUM
    source = backend.from_list([1.0] * size)
    destination = backend.zeros(size)
    elapsed = timing.median(_time(lambda: ops.copy(source, destination), backend, samples))
    return {
        "value": {"vectorised": ops.kind != "reference-loops", "copy_us": elapsed},
        "curve": {"copy_us": elapsed},
        "unit": "microseconds per copy",
        "note": ("the runtime vectorises this copy, so wide loads are already in use"
                 if ops.kind != "reference-loops"
                 else "the reference backend does not vectorise; no width ratio is measurable"),
    }


def bank_conflicts(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Scratchpad conflict cost: the profiling-only metric on most devices.

    An array runtime gives no scratchpad with selectable banking, so this returns an
    unmeasured entry rather than a fabricated slowdown. A device that exposes scratchpad
    banking (a vendor ops implementation) replaces this with a real sweep.
    """
    return {
        "value": None,
        "curve": {},
        "unit": "slowdown versus conflict degree",
        "note": "not measurable through the portable backend; a vendor ops implementation supplies it",
    }


def occupancy_vs_registers(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Whether more parallelism keeps paying once the device is already saturated."""
    ops = _ops(backend)
    curve: Dict[str, float] = {}
    for size in (SMALL, MEDIUM, LARGE):
        source = backend.from_list([1.0] * size)
        destination = backend.zeros(size)
        elapsed = timing.median(_time(lambda: ops.copy(source, destination), backend, samples))
        elements = size
        curve[str(size)] = elements / elapsed / 1e3 if elapsed else 0.0
    values = list(curve.values())
    saturated = max(values) > 0 and min(values) / max(values) > 0.8
    return {
        "value": {"throughput_by_size": curve, "flat": saturated},
        "curve": curve,
        "unit": "elements per microsecond",
        "note": ("throughput is flat across sizes: the device is already saturated"
                 if saturated else "throughput still rises with size: more parallelism pays"),
    }


def branch_divergence(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Cost of divergent control flow within a work group.

    Measured as the cost of a masked loop against a full loop, which is the portable form of
    the same question: how much does doing less work per lane still cost.
    """
    ops = _ops(backend)
    size = MEDIUM
    source = backend.from_list([1.0] * size)
    destination = backend.zeros(size)
    full = timing.median(_time(lambda: ops.add(source, source, destination), backend, samples))
    zero = backend.zeros(size)
    masked = timing.median(_time(lambda: ops.add(source, zero, destination), backend, samples))
    return {
        "value": {"full_us": full, "masked_us": masked,
                  "ratio": (masked / full) if full else 0.0},
        "curve": {"full_us": full, "masked_us": masked},
        "unit": "slowdown ratio",
        "note": "a ratio near 1 means the device hides divergence; above 1 means it pays",
    }


def instruction_cost(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Relative cost of the expensive scalar operations the patterns mention."""
    ops = _ops(backend)
    size = MEDIUM
    a = backend.from_list([1.5] * size)
    b = backend.from_list([1.0000001] * size)
    out = backend.zeros(size)
    baseline = timing.median(_time(lambda: ops.mul_add(a, b, out, out), backend, samples))
    return {
        "value": {"mul_add_us": baseline},
        "curve": {"mul_add_us": baseline},
        "unit": "microseconds; divide, sqrt and exp are added by a vendor ops implementation",
        "note": ("the portable operation set exposes multiply-add only, so no relative cost "
                 "table is claimed for the other operations"),
    }


def launch_overhead(backend: Any, samples: int = 15, **_: Any) -> Dict[str, Any]:
    """Fixed per-launch cost: the floor for small shapes and the case for merging kernels."""
    ops = _ops(backend)
    elapsed = timing.median(_time(ops.launch_empty, backend, samples))
    return {
        "value": elapsed,
        "curve": {"empty_launch_us": elapsed},
        "unit": "microseconds",
        "note": "an empty call through the backend: the smallest unit of work a launch can cost",
    }


def sync_cost(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """What one work-group barrier costs, which prices every scratchpad scheme."""
    ops = _ops(backend)
    buffers = [backend.zeros(1024) for _ in range(4)]
    curve: Dict[str, float] = {}
    for barrier_every in (1024, 64, 8):
        elapsed = timing.median(_time(lambda: ops.barrier_loop(buffers, barrier_every),
                                      backend, samples))
        curve[str(barrier_every)] = elapsed
    return {
        "value": curve,
        "curve": curve,
        "unit": "microseconds for a fixed amount of work at decreasing barrier intervals",
        "note": "the growth from the widest interval to the narrowest is the barrier price",
    }


def atomics(backend: Any, samples: int = 8, **_: Any) -> Dict[str, Any]:
    """Cost of a scatter-add as contention rises."""
    ops = _ops(backend)
    size = MEDIUM
    source = backend.from_list([1.0] * size)
    curve: Dict[str, float] = {}
    for buckets in (1024, 64, 1):
        accumulators = backend.zeros(buckets)
        elapsed = timing.median(_time(lambda: ops.atomic_add(accumulators, source, buckets),
                                      backend, samples))
        curve[str(buckets)] = elapsed
    return {
        "value": curve,
        "curve": curve,
        "unit": "microseconds for a fixed scatter at decreasing bucket count",
        "note": "the growth as buckets shrink is the contention penalty",
    }


#: Name -> function, matching ``suite.core_suite()``.
IMPLEMENTATIONS = {
    "noise_floor": noise_floor,
    "drift": drift,
    "memory_bandwidth": memory_bandwidth,
    "peak_compute": peak_compute,
    "cache_hierarchy": cache_hierarchy,
    "coalescing_penalty": coalescing_penalty,
    "load_width": load_width,
    "bank_conflicts": bank_conflicts,
    "occupancy_vs_registers": occupancy_vs_registers,
    "branch_divergence": branch_divergence,
    "instruction_cost": instruction_cost,
    "launch_overhead": launch_overhead,
    "sync_cost": sync_cost,
    "atomics": atomics,
}
