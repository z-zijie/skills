"""DataFlow runtime estimator.

Execution model
---------------
Per core, nodes execute on pipes. Different pipes can overlap; the same pipe is
serial. On top of that, explicit `deps` create ordering. So a node starts at:

    start(n) = max( max over deps d of end(d),
                    end of previous node on n's pipe )

and the kernel time is max over nodes of end(n), converted from cycles to time.

This is the classic pipe/stream model used for Ascend-style kernels (MTE2 load,
VEC compute, MTE3 store), which is exactly the shape the goal asks to model.

Reported outputs
----------------
  cycles          critical-path cycles
  us              same in microseconds
  per_pipe_us     time each pipe is busy (shows the bottleneck)
  bottleneck      the pipe with the largest busy time
  calibrated      which pipe models are backed by measurement
  warnings        e.g. a flow that uses an uncalibrated pipe
"""
import json, sys
from dataclasses import dataclass
from typing import Dict, List

sys.path.insert(0, ".")
from ir import Flow, Node, parse, topological
from costs import CostModel


@dataclass
class NodeTiming:
    id: str
    pipe: str
    start_cyc: float
    end_cyc: float
    dur_cyc: float


@dataclass
class Result:
    cycles: float
    us: float
    per_pipe_cycles: Dict[str, float]
    timings: List[NodeTiming]
    bottleneck: str
    calibrated: Dict[str, dict]
    warnings: List[str]

    def to_dict(self):
        return dict(
            cycles=self.cycles, us=self.us,
            per_pipe_us={k: v for k, v in self._per_pipe_us().items()},
            bottleneck=self.bottleneck,
            calibrated=self.calibrated,
            warnings=self.warnings,
            nodes=[dict(id=t.id, pipe=t.pipe, start_us=t.start_cyc/1650.0,
                        dur_us=t.dur_cyc/1650.0) for t in self.timings],
        )

    def _per_pipe_us(self):
        g = self.calibrated["MTE2"].get("ghz", 1.65)
        return {k: v/(g*1000.0) for k, v in self.per_pipe_cycles.items()}


def estimate(flow: Flow, model: CostModel = None, verbose: bool = False) -> Result:
    model = model or CostModel()
    order = topological(flow)
    cores = flow.cores

    warnings = []
    cal = model.calibration_report()
    for n in flow.nodes:
        if n.kind == "barrier":
            continue
        c = cal.get(n.pipe)
        if c and not c["calibrated"]:
            warnings.append(f"node {n.id} uses uncalibrated pipe {n.pipe}: {c['source']}")

    end: Dict[str, float] = {}
    last_on_pipe: Dict[str, float] = {}
    timings: List[NodeTiming] = []
    per_pipe: Dict[str, float] = {}

    for n in order:
        dur = model.node_cycles(n, cores)
        dep_end = max((end[d] for d in n.deps), default=0.0)
        pipe_prev = last_on_pipe.get(n.pipe, 0.0) if n.kind != "barrier" else 0.0
        start = max(dep_end, pipe_prev)
        finish = start + dur
        end[n.id] = finish
        if n.kind != "barrier":
            last_on_pipe[n.pipe] = finish
            per_pipe[n.pipe] = per_pipe.get(n.pipe, 0.0) + dur
        timings.append(NodeTiming(n.id, n.pipe, start, finish, dur))

    total = max(end.values()) if end else 0.0
    bottleneck = max(per_pipe, key=per_pipe.get) if per_pipe else "none"
    return Result(
        cycles=total,
        us=model.clock.to_us(total),
        per_pipe_cycles=per_pipe,
        timings=timings,
        bottleneck=bottleneck,
        calibrated=cal,
        warnings=warnings,
    )


if __name__ == "__main__":
    import pathlib
    if len(sys.argv) > 1:
        obj = json.loads(pathlib.Path(sys.argv[1]).read_text())
    else:
        obj = {
            "name": "two_mte2_100k",
            "cores": 56,
            "nodes": [
                {"id": "c1", "kind": "copy", "pipe": "MTE2", "bytes": 102400},
                {"id": "c2", "kind": "copy", "pipe": "MTE2", "bytes": 102400, "deps": ["c1"]},
            ],
        }
    r = estimate(parse(obj))
    print(json.dumps(r.to_dict(), indent=1, ensure_ascii=False))
