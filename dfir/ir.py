"""DataFlow IR: a small, explicit description of a kernel's data movement and
compute graph, plus a performance estimator driven by calibrated hardware
models.

Why an IR at all: the goal is "given an arbitrary DataFlow, predict its runtime
to within 5%". A free-form kernel cannot be measured up front, so the flow has to
be described in a form the estimator can reason about:

  * what memory moves happen (which pipe, how many bytes, where to/from)
  * what compute happens (which engine, how many elements)
  * how they are ordered and overlapped (streams / pipeline stages)
  * how work is partitioned over cores

The IR is deliberately small. It models what the measurements showed to matter:
bytes moved per pipe, transaction size, overlap, and core count.

IR (JSON)
---------
node: one operation.
  { "id": str,
    "kind": "copy" | "compute" | "barrier",
    "pipe": "MTE2" | "MTE3" | "VEC" | "SCALAR",   # copy/compute engine
    "bytes": int,        # copy only: bytes moved
    "elem": int,         # compute only: elements processed
    "dtype_bytes": int,  # compute only
    "tile": int,         # optional: split into `tile`-byte chunks
    "deps": [id, ...] }  # must finish before this node starts

flow: one kernel.
  { "name": str,
    "cores": int,                # CoreNum the flow runs on
    "nodes": [node, ...],
    "streams": {id: stream_id} } # optional: which serial queue each node is on

Execution model (what the estimator does)
-----------------------------------------
A node's duration comes from a calibrated cost model per pipe. Nodes on the same
stream run in issue order; nodes on different streams may overlap subject to
deps. The kernel time is the critical path through that dependency/stream graph.
Per-core time is modelled with the measured CoreNum law, then the kernel time is
the slowest core.

This file holds the IR parser and the scheduler. Cost models live in costs.py so
they can be recalibrated without touching the IR.
"""
import json, math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PIPES = ("MTE2", "MTE3", "VEC", "SCALAR")


@dataclass
class Node:
    id: str
    kind: str                       # copy | compute | barrier
    pipe: str = "MTE2"
    bytes: int = 0
    elem: int = 0
    dtype_bytes: int = 4
    tile: int = 0                   # 0 = no tiling
    copies: int = 1                 # identical copies on this node (K)
    deps: List[str] = field(default_factory=list)
    stream: int = 0

    @property
    def transactions(self) -> int:
        """Bus transactions, using the measured 256-byte MTE2 packet size."""
        return math.ceil(self.bytes / 256.0) if self.bytes else 0


@dataclass
class Flow:
    name: str
    cores: int
    nodes: List[Node]

    def by_id(self) -> Dict[str, Node]:
        return {n.id: n for n in self.nodes}


def parse(obj: Dict) -> Flow:
    """Parse the JSON form. Rejects unknown pipes/kinds loudly rather than
    silently estimating nonsense."""
    if "nodes" not in obj or not obj["nodes"]:
        raise ValueError("flow has no nodes")
    streams = obj.get("streams") or {}
    nodes = []
    ids = set()
    for raw in obj["nodes"]:
        nid = raw.get("id")
        if not nid:
            raise ValueError("node without id")
        if nid in ids:
            raise ValueError(f"duplicate node id {nid}")
        ids.add(nid)
        kind = raw.get("kind")
        if kind not in ("copy", "compute", "barrier"):
            raise ValueError(f"{nid}: bad kind {kind!r}")
        pipe = raw.get("pipe", "MTE2")
        if kind != "barrier" and pipe not in PIPES:
            raise ValueError(f"{nid}: bad pipe {pipe!r}")
        nodes.append(Node(
            id=nid, kind=kind, pipe=pipe,
            bytes=int(raw.get("bytes", 0)),
            elem=int(raw.get("elem", 0)),
            dtype_bytes=int(raw.get("dtype_bytes", 4)),
            tile=int(raw.get("tile", 0)),
            copies=int(raw.get("copies", 1)),
            deps=list(raw.get("deps", [])),
            stream=int(streams.get(nid, raw.get("stream", 0))),
        ))
    for n in nodes:
        for d in n.deps:
            if d not in ids:
                raise ValueError(f"{n.id}: dep on unknown node {d!r}")
    cores = int(obj.get("cores", 1))
    if cores < 1:
        raise ValueError("cores must be >= 1")
    return Flow(name=obj.get("name", "flow"), cores=cores, nodes=nodes)


def topological(flow: Flow) -> List[Node]:
    """Kahn's algorithm; raises on cycles instead of looping forever."""
    indeg = {n.id: 0 for n in flow.nodes}
    out: Dict[str, List[str]] = {n.id: [] for n in flow.nodes}
    for n in flow.nodes:
        for d in n.deps:
            indeg[n.id] += 1
            out[d].append(n.id)
    ready = [nid for nid, d in indeg.items() if d == 0]
    order = []
    byid = flow.by_id()
    while ready:
        ready.sort()
        nid = ready.pop(0)
        order.append(byid[nid])
        for m in out[nid]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    if len(order) != len(flow.nodes):
        raise ValueError("dependency cycle in flow")
    return order


if __name__ == "__main__":
    demo = {
        "name": "two_mte2_copies",
        "cores": 56,
        "nodes": [
            {"id": "c1", "kind": "copy", "pipe": "MTE2", "bytes": 102400},
            {"id": "c2", "kind": "copy", "pipe": "MTE2", "bytes": 102400, "deps": ["c1"]},
        ],
    }
    f = parse(demo)
    print("parsed:", f.name, "cores:", f.cores)
    for n in topological(f):
        print(f"  {n.id} {n.kind}/{n.pipe} bytes={n.bytes} txn={n.transactions}")
