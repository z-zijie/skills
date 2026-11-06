"""Microbenchmark suite: turn this device's temperament into numbers and curves.

Microbenchmark code belongs to the evaluator. It is frozen like the rest of Layer 0, agents
read only the results, and a new microbenchmark is proposed by ``design-microbench`` and added
after human approval. That is why this package is inside ``evaluator/`` rather than in a
skill.

Two rules of use are enforced here rather than documented and forgotten:

* **Every measured parameter carries an environment fingerprint and a date**, because results
  expire. A re-measure after a runtime upgrade is compared with the previous run, and a
  parameter that moved beyond its threshold marks the rules that depend on it ``under review``.
* **Prediction error drives new measurements.** The suite reports which parameter a large
  prediction error implicates, which is what ``design-microbench`` consumes.

The suite is written against the backend contract, so it runs unchanged on any device that
provides one: a CPU reference, a GPU runtime, an NPU runtime. Each microbench applies only if
its required capability is present; the rest are reported as not measured, never as zero.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import kernels


class Microbench:
    """One microbenchmark: what it measures, what it needs, how it runs.

    ``run`` receives the backend and the sample count and returns a mapping of parameter name
    to value plus the curve that produced it. Nothing in a microbench may call a profiler: the
    point is to measure the machine directly.
    """

    def __init__(self, name: str, parameter: str, measures: str, used_by: str,
                 run: Callable[..., Dict[str, Any]], requires: tuple = ("timing",)) -> None:
        self.name = name
        self.parameter = parameter
        self.measures = measures
        self.used_by = used_by
        self.run = run
        self.requires = requires

    def applicable(self, backend: Any) -> bool:
        return set(self.requires) <= set(getattr(backend, "capabilities", ()))

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parameter": self.parameter,
            "measures": self.measures,
            "used_by": self.used_by,
            "requires": list(self.requires),
        }


def core_suite() -> List[Microbench]:
    """The starting suite, ordered by what it is used for."""
    return [
        Microbench("noise_floor", "noise_floor.threshold",
                   "repeat the same kernel as two arms (A/A)",
                   "the evaluator's win threshold",
                   kernels.noise_floor),
        Microbench("drift", "drift",
                   "run unbroken and watch throughput settle",
                   "the timing protocol's warm-up length",
                   kernels.drift),
        Microbench("memory_bandwidth", "peak_bandwidth_gbps",
                   "sequential read, write and copy across data sizes",
                   "the bandwidth term of the roofline",
                   kernels.memory_bandwidth),
        Microbench("peak_compute", "peak_flops",
                   "a fused-multiply-add-heavy loop per dtype",
                   "the compute term of the roofline",
                   kernels.peak_compute),
        Microbench("cache_hierarchy", "cache",
                   "sweep the working set from small to large",
                   "choosing tile sizes",
                   kernels.cache_hierarchy),
        Microbench("coalescing_penalty", "coalescing",
                   "access stride of 1, 2, 4 and up to random",
                   "estimating the gain from AoS to SoA",
                   kernels.coalescing_penalty),
        Microbench("load_width", "load_width",
                   "compare narrow, aligned and wide loads",
                   "estimating the gain from wide loads",
                   kernels.load_width),
        Microbench("bank_conflicts", "bank_conflicts",
                   "access scratchpad at different strides",
                   "whether to add padding",
                   kernels.bank_conflicts),
        Microbench("occupancy_vs_registers", "occupancy",
                   "add live registers on a bandwidth- and a compute-bound loop",
                   "whether cutting registers for occupancy pays off",
                   kernels.occupancy_vs_registers),
        Microbench("branch_divergence", "divergence",
                   "sweep the divergent fraction within a work group",
                   "estimating the gain from predication or sorting",
                   kernels.branch_divergence),
        Microbench("instruction_cost", "instruction_cost",
                   "divide, sqrt, exp and conversions against multiply-add",
                   "predicting division-to-reciprocal style changes",
                   kernels.instruction_cost),
        Microbench("launch_overhead", "launch.overhead_us",
                   "an empty kernel at different grid sizes",
                   "the floor for small shapes; whether to merge kernels",
                   kernels.launch_overhead),
        Microbench("sync_cost", "sync",
                   "vary how often a work group synchronises",
                   "the price of scratchpad schemes",
                   kernels.sync_cost),
        Microbench("atomics", "atomics",
                   "atomic add at different contention rates",
                   "reductions and histogram-style operators",
                   kernels.atomics),
    ]


#: Suites by name. ``core`` is the full starting set; ``quick`` is the two-item smoke test.
SUITES = {
    "core": core_suite,
    "quick": lambda: [microbench for microbench in core_suite()
                      if microbench.name in {"noise_floor", "memory_bandwidth"}],
}
