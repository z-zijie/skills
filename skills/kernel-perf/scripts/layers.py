#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layered metric classification: what part of the machine each number describes.

A flat list of 51 metrics is hard to act on. Optimisation work always proceeds
level by level -- first "is the chip's memory system the limit?", then "which
pipe inside the core?", then "which block started late and straggled?". This
module gives every metric a place in that hierarchy so the report reads in the
order a person debugs.

The three layers, and why they are separate
-------------------------------------------
soc        Machine-level: data movement across the bus, L2, HBM/DDR, and the
           per-block scheduling timeline. Numbers here are shared by all cores;
           reading them tells you whether the kernel's shape is wrong.

core       Inside one AI Core: MTE2/MTE3/Cube/Vector/Scalar/FixPipe occupancy,
           issue stalls, bank conflicts, UB/L0/L1 bandwidth. Numbers here are
           per-core and answer "which pipe is the bottleneck".

schedule   When each block (a core's AIC subcore, or one of its two AIV
           subcores) starts and ends. This is what reveals launch skew and
           tail effects -- a kernel can have perfectly balanced per-core work
           and still be slow because block 55 started 2us after block 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Layer ids, ordered as a reader should walk them.
LAYERS = ["soc", "core", "schedule"]

LAYER_LABELS = {
    "soc": "SoC layer (data movement / interconnect / L2)",
    "core": "Core layer (pipes / conflicts / on-chip caches)",
    "schedule": "Schedule layer (block start and end / multi-core synchronisation)",
}

LAYER_DESCRIPTIONS = {
    "soc": "Resources shared across cores. Problems here cannot be fixed by editing kernel code; they need a change of algorithmic shape or total tiling.",
    "core": "Contention among pipes inside one core. This layer answers which unit is the bottleneck.",
    "schedule": "Each block's timeline. This layer answers whether the cores started together and whether any straggled.",
}

# metric key -> layer
METRIC_LAYER: Dict[str, str] = {
    # ---- SoC: data movement and caches shared across cores -------------
    "duration_us": "soc",
    "block_dim": "soc",
    "freq_mhz": "soc",
    "gm_to_ub_gbps": "soc",
    "gm_to_ub_pct": "soc",
    "ub_to_gm_gbps": "soc",
    "main_mem_read_gbps": "soc",
    "main_mem_write_gbps": "soc",
    "l2_read_hit": "soc",
    "l2_write_hit": "soc",
    "l2_read_miss": "soc",
    "l2_hit_viz": "soc",
    "memory_paths": "soc",
    # ---- core: per-core pipes and on-chip buffers ----------------------
    "cycles": "core",
    "vec_ratio": "core",
    "scalar_ratio": "core",
    "mte2_ratio": "core",
    "mte3_ratio": "core",
    "cube_ratio": "core",
    "mte1_ratio": "core",
    "fixpipe_ratio": "core",
    "aic_scalar_ratio": "core",
    "vec_wait_ratio": "core",
    "mte3_wait_ratio": "core",
    "mte2_wait_ratio": "core",
    "wait_cycles": "core",
    "pipe_wait_table": "core",
    "bank_stu_cflt": "core",
    "bank_ldu_cflt": "core",
    "sfu_cflt": "core",
    "ub_read_vec_gbps": "core",
    "ub_write_vec_gbps": "core",
    "l1_read_gbps": "core",
    "l1_write_gbps": "core",
    "l0a_read_gbps": "core",
    "l0b_read_gbps": "core",
    "l0c_write_gbps": "core",
    "icache_hit": "core",
    "dcache": "core",
    "cube_instr": "core",
    "cube_fp_instr": "core",
    "mac_usage": "core",
    "mfu": "core",
    "vec_lane_usage": "core",
    "instr_timeline": "core",
    "instr_region_map": "core",
    "source_attribution": "core",
    "pipe_timeline": "core",
    # ---- schedule: per-block timeline ----------------------------------
    "imbalance": "schedule",
    "block_timeline": "schedule",
    "launch_skew": "schedule",
    "tail_effect": "schedule",
    "vf_ratio": "core",
    "simt_vf_ratio": "core",
    "vf_internal": "core",
    "load_store_ratio": "core",
    "pc_cycle": "core",
    "sector_efficiency": "soc",
    "cross_die_bw": "soc",
    "cross_die_pct": "soc",
    "die_count": "soc",
    "advice": "soc",
}


def layer_of(key: str) -> str:
    return METRIC_LAYER.get(key, "core")


def metrics_in_layer(catalogue: List[object], layer: str) -> List[object]:
    return [m for m in catalogue if layer_of(getattr(m, "key", "")) == layer]


@dataclass
class BlockTime:
    """Start/end/duration for one block, in kernel-relative microseconds."""

    block_id: str
    core_type: str
    start_us: Optional[float] = None
    end_us: Optional[float] = None
    duration_us: Optional[float] = None
    source: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "block_id": self.block_id,
            "core_type": self.core_type,
            "start_us": self.start_us,
            "end_us": self.end_us,
            "duration_us": self.duration_us,
            "source": self.source,
        }


@dataclass
class ScheduleInsight:
    """What the block timeline says about synchronisation and tail."""

    available: bool = False
    reason: str = ""
    blocks: int = 0
    source: str = ""
    first_start_us: Optional[float] = None
    last_start_us: Optional[float] = None
    last_end_us: Optional[float] = None
    launch_skew_us: Optional[float] = None
    launch_skew_pct: Optional[float] = None
    tail_us: Optional[float] = None
    tail_pct: Optional[float] = None
    min_duration_us: Optional[float] = None
    max_duration_us: Optional[float] = None
    duration_spread_pct: Optional[float] = None
    slowest_blocks: List[Dict[str, object]] = field(default_factory=list)
    latest_blocks: List[Dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "available": self.available,
            "reason": self.reason,
            "blocks": self.blocks,
            "source": self.source,
            "first_start_us": self.first_start_us,
            "last_start_us": self.last_start_us,
            "last_end_us": self.last_end_us,
            "launch_skew_us": self.launch_skew_us,
            "launch_skew_pct": self.launch_skew_pct,
            "tail_us": self.tail_us,
            "tail_pct": self.tail_pct,
            "min_duration_us": self.min_duration_us,
            "max_duration_us": self.max_duration_us,
            "duration_spread_pct": self.duration_spread_pct,
            "slowest_blocks": self.slowest_blocks,
            "latest_blocks": self.latest_blocks,
        }


def analyse_schedule(blocks: List[BlockTime],
                     kernel_us: Optional[float]) -> ScheduleInsight:
    """Derive launch skew and tail effect from per-block timing.

    Definitions, stated so the report can be argued with:

    launch_skew  last_start - first_start. How long the kernel takes to get
                 every block running. A kernel with 56 blocks and 2us of skew
                 spends 2us with only part of the chip working.

    tail         last_end - first_end: the gap between the earliest block to
                 finish and the latest. Large tail means some blocks do much
                 more work than others, or start much later.

    Both are reported as absolute us and as a percentage of kernel duration,
    because 1us of skew means very different things in a 5us and a 500us kernel.
    """
    insight = ScheduleInsight()
    usable = [b for b in blocks if b.start_us is not None and b.end_us is not None]
    if not usable:
        insight.reason = "no block-level timestamps (this capture provides no start/end)"
        return insight

    insight.available = True
    insight.blocks = len(usable)
    insight.source = usable[0].source

    starts = [b.start_us for b in usable if b.start_us is not None]
    ends = [b.end_us for b in usable if b.end_us is not None]
    durations = [b.duration_us for b in usable if b.duration_us is not None]
    if not starts or not ends:
        insight.available = False
        insight.reason = "block records lack start or end"
        return insight

    insight.first_start_us = round(min(starts), 4)
    insight.last_start_us = round(max(starts), 4)
    insight.last_end_us = round(max(ends), 4)
    insight.launch_skew_us = round(max(starts) - min(starts), 4)
    insight.tail_us = round(max(ends) - min(ends), 4)
    if kernel_us:
        insight.launch_skew_pct = round(100.0 * insight.launch_skew_us / kernel_us, 2)
        insight.tail_pct = round(100.0 * insight.tail_us / kernel_us, 2)
    if durations:
        insight.min_duration_us = round(min(durations), 4)
        insight.max_duration_us = round(max(durations), 4)
        mean = sum(durations) / len(durations)
        if mean:
            insight.duration_spread_pct = round(
                100.0 * (max(durations) - min(durations)) / mean, 2)

    insight.slowest_blocks = [
        b.as_dict() for b in sorted(
            usable, key=lambda x: -(x.duration_us or 0))[:5]
    ]
    insight.latest_blocks = [
        b.as_dict() for b in sorted(usable, key=lambda x: -(x.start_us or 0))[:5]
    ]
    return insight


def blocks_from_trace(trace: Dict[str, object],
                      kernel_us: Optional[float] = None) -> List[BlockTime]:
    """Derive per-block start/end from a PipeTimeline trace.

    PipeTimeline writes one Chrome-tracing event stream per sub-core, with the
    participant encoded in ``pid`` as ``core<N>.veccore<M>``. Taking min(ts) and
    max(ts+dur) over a pid's events gives that sub-core's first activity and
    last completion, which is exactly the block start/end needed for skew and
    tail analysis.

    Honesty about coverage: PipeTimeline instruments only a subset of cores.
    On dav-3510 a 56-block kernel yielded 12 sub-cores here, so the analysis
    covers those blocks and says so -- skew measured on 12 blocks is a lower
    bound, not the chip-wide truth.
    """
    events = [e for e in (trace or {}).get("traceEvents", [])
              if isinstance(e, dict) and e.get("ph") == "X"]
    if not events:
        return []
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("pid", "?")), []).append(event)

    blocks: List[BlockTime] = []
    origin: Optional[float] = None
    raw: List[tuple] = []
    for pid, evs in grouped.items():
        starts = [float(e.get("ts", 0.0)) for e in evs]
        ends = [float(e.get("ts", 0.0)) + float(e.get("dur", 0.0) or 0.0) for e in evs]
        raw.append((pid, min(starts), max(ends)))
    if not raw:
        return []
    origin = min(r[1] for r in raw)

    for pid, start, end in sorted(raw):
        core_type = "vector" if "veccore" in pid else (
            "cube" if "cubecore" in pid else "?")
        blocks.append(BlockTime(
            block_id=pid,
            core_type=core_type,
            start_us=round(start - origin, 4),
            end_us=round(end - origin, 4),
            duration_us=round(end - start, 4),
            source="trace.json (PipeTimeline)",
        ))
    return blocks


def render_schedule(insight: ScheduleInsight) -> List[str]:
    """Markdown for the scheduling layer."""
    lines: List[str] = []
    if not insight.available:
        lines.append(f"Unavailable: {insight.reason}")
        lines.append("")
        lines.append("> Block-level timestamps require `--aic-metrics=PipeTimeline` (a separate pass). "
                     "It instruments only a subset of cores, so covered blocks may be fewer than Block Dim.")
        lines.append("")
        return lines

    lines.append(f"- Block timelines collected for **{insight.blocks}** blocks (source `{insight.source}`)")
    lines.append(f"- First block started at {insight.first_start_us:.3f} us, "
                 f"last at {insight.last_start_us:.3f} us")
    lines.append("")
    lines.append("| Metric | Value | Meaning |")
    lines.append("|---|---:|---|")
    lines.append(f"| **Launch skew** | {insight.launch_skew_us:.3f} us"
                 + (f" ({insight.launch_skew_pct:.1f}%)" if insight.launch_skew_pct else "")
                 + " | Only part of the chip works during this interval |")
    lines.append(f"| **Tail** | {insight.tail_us:.3f} us"
                 + (f" ({insight.tail_pct:.1f}%)" if insight.tail_pct else "")
                 + " | Gap between the earliest and the latest block to finish |")
    lines.append(f"| Block duration range | {insight.min_duration_us:.3f}-{insight.max_duration_us:.3f} us | "
                 f"spread {insight.duration_spread_pct:.1f}% |")
    lines.append("")

    if insight.launch_skew_pct is not None and insight.launch_skew_pct > 10:
        lines.append(f"> **Asynchronous launch**: skew is {insight.launch_skew_pct:.0f}% of kernel duration. "
                     "Chip utilisation drops proportionally during that interval and pipeline tuning cannot help; "
                     "first check whether tiling makes every core depend on the same prerequisite step.")
        lines.append("")
    if insight.tail_pct is not None and insight.tail_pct > 20:
        lines.append(f"> **Pronounced tail**: the slowest core finishes {insight.tail_us:.2f} us after the fastest "
                     f"({insight.tail_pct:.0f}%). Usually an uneven final tile or a preempted core.")
        lines.append("")

    if insight.slowest_blocks:
        lines.append("Slowest blocks:")
        lines.append("")
        lines.append("| Block | Type | Start us | End us | Duration us |")
        lines.append("|---|---|---:|---:|---:|")
        for b in insight.slowest_blocks:
            lines.append(f"| {b['block_id']} | {b['core_type']} | {b['start_us']:.3f} | "
                         f"{b['end_us']:.3f} | {b['duration_us']:.3f} |")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# SoC memory paths (incl. cross-die)
# ---------------------------------------------------------------------------
# msopprof publishes eight-plus "memory_path" ids per core inside
# visualize_data.bin's core_memory_map. The ids are not documented anywhere in
# CANN, so the names below are inferred, and the inference is stated rather
# than presented as fact:
#
#   0,1          observed on a vector kernel as the dominant read path, bandwidth
#                equal to GM->UB -- these are the local-die GM read/write pair
#   12..15       byte-for-byte equal to the UB/vector four-direction numbers in
#                the per-block 'UB' table, so these are the UB datapath, not GM
#   39,41        always 0 on every capture seen -- reserved or unused on this SKU
#   40           tracks GM read on the cube kernel; likely the cube-side GM path
#   2,6,7,8,37,38 appear ONLY on the cube kernel and carry non-zero traffic.
#                On a two-die part (io_die_num=2) with interleaved L2, the only
#                plausible source of extra GM-side paths is the remote die.
#
# Because the mapping is inferred, values are reported with the raw id and the
# confidence is C, not A. A reader who knows the SKU can correct the names
# without touching the parser.
MEMORY_PATH_NAMES = {
    0: "GM read (local die)",
    1: "GM write (local die)",
    2: "Cross-die path 2 (inferred)",
    6: "Cross-die path 6 (inferred)",
    7: "Cross-die path 7 (inferred)",
    8: "Cross-die path 8 (inferred)",
    12: "UB<-GM (MTE2 into UB)",
    13: "UB→GM（MTE3）",
    14: "UB->L2 (vector read)",
    15: "L2->UB (vector write)",
    37: "Cross-die path 37 (inferred)",
    38: "Cross-die path 38 (inferred)",
    39: "Reserved (always 0)",
    40: "GM read (cube side, inferred)",
    41: "Reserved (always 0)",
    48: "L0A/B path",
    50: "Reserved (always 0)",
    52: "L0C path",
    54: "FixPipe path",
}

# Paths that carry traffic off the local die. Everything else is on-die.
CROSS_DIE_PATHS = {2, 6, 7, 8, 37, 38}


@dataclass
class SocPaths:
    """Cross-die vs on-die traffic split, and what it implies."""

    available: bool = False
    reason: str = ""
    die_count: Optional[int] = None
    paths: List[Dict[str, object]] = field(default_factory=list)
    cross_die_gbps: float = 0.0
    on_die_gbps: float = 0.0
    cross_die_pct: Optional[float] = None
    cross_die_requests: int = 0

    def as_dict(self) -> Dict[str, object]:
        return {
            "available": self.available,
            "reason": self.reason,
            "die_count": self.die_count,
            "paths": self.paths,
            "cross_die_gbps": round(self.cross_die_gbps, 4),
            "on_die_gbps": round(self.on_die_gbps, 4),
            "cross_die_pct": self.cross_die_pct,
            "cross_die_requests": self.cross_die_requests,
        }


def analyse_paths(viz_cores: List[Dict[str, object]],
                  die_num: Optional[int] = None) -> SocPaths:
    """Split measured per-core bandwidth into on-die vs cross-die."""
    result = SocPaths()
    result.die_count = die_num
    if not viz_cores:
        result.reason = "no core_memory_map (visualize_data.bin 0x08)"
        return result

    units = (viz_cores[0] or {}).get("memory_unit") or []
    if not units:
        result.reason = "core 0 has no memory_unit"
        return result
    result.available = True

    seen = set()
    for unit in units:
        try:
            path_id = int(unit.get("memory_path"))
        except (TypeError, ValueError):
            continue
        if path_id in seen:
            continue
        seen.add(path_id)
        bandwidth = _as_float(unit.get("bandwidth")) or 0.0
        requests = _as_float(unit.get("request")) or 0
        is_cross = path_id in CROSS_DIE_PATHS
        result.paths.append({
            "path_id": path_id,
            "name": MEMORY_PATH_NAMES.get(path_id, f"path {path_id} (unnamed)"),
            "bandwidth_gbps": round(bandwidth, 4),
            "requests": int(requests),
            "cross_die": is_cross,
            "inferred": path_id in (2, 6, 7, 8, 37, 38, 40),
        })
        if is_cross:
            result.cross_die_gbps += bandwidth
            result.cross_die_requests += int(requests)
        else:
            result.on_die_gbps += bandwidth

    total = result.cross_die_gbps + result.on_die_gbps
    if total > 0:
        result.cross_die_pct = round(100.0 * result.cross_die_gbps / total, 2)
    result.paths.sort(key=lambda e: -float(e["bandwidth_gbps"]))
    return result


def _as_float(value: object) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def render_paths(paths: SocPaths) -> List[str]:
    """Markdown for the memory-path / cross-die section."""
    lines: List[str] = []
    if not paths.available:
        lines.append(f"Unavailable: {paths.reason}")
        lines.append("")
        return lines

    die_note = (f"this part declares io_die_num={paths.die_count} (dual-die)"
                if paths.die_count else "die count unknown")
    lines.append(f"- {die_note}")
    lines.append(f"- Cross-die traffic {paths.cross_die_gbps:.3f} GB/s, "
                 f"on-die traffic {paths.on_die_gbps:.3f} GB/s"
                 + (f", **cross-die {paths.cross_die_pct:.1f}%**"
                    if paths.cross_die_pct is not None else ""))
    lines.append("")
    lines.append("| Path | Name | Bandwidth GB/s | Requests | Cross-die |")
    lines.append("|---:|---|---:|---:|:--:|")
    for entry in paths.paths:
        lines.append(
            f"| {entry['path_id']} | {entry['name']} | {entry['bandwidth_gbps']:.3f} | "
            f"{entry['requests']} | {'yes' if entry['cross_die'] else ''} |")
    lines.append("")
    lines.append("> Path names are **inferred** (CANN does not document the ids): 0/1 match GM read/write, "
                 "12-15 match the UB table byte for byte, and 2/6/7/8/37/38 appear only on the cube kernel with real traffic. "
                 "Their confidence reflects the inference, not a measurement.")
    lines.append("")
    if paths.cross_die_pct is not None and paths.cross_die_pct > 20:
        lines.append(f"> **Cross-die traffic is {paths.cross_die_pct:.0f}%**: on a dual-die part, reaching the remote "
                     "die's L2 traverses SIO at materially higher latency. Available levers: map GM addresses to the L2 "
                     "interleave so each core writes only local-die cachelines, reduce the number of cores writing the same cacheline, "
                     "or reduce same-address write participation.")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# PC-vs-cycle: icache-miss detection (the primary technique in scalar_story)
# ---------------------------------------------------------------------------
@dataclass
class PcStall:
    """A period where the PC did not advance while the pipe was busy.

    scalar_story's diagnostic: plot PC against cycle; a long horizontal segment
    means the core is waiting on an icache fetch, because the same PC is being
    re-executed/stalled rather than advancing. We reproduce that numerically --
    a horizontal run is simply an interval where consecutive events share a PC
    and the gap between them is large relative to the event durations.
    """

    pc: str
    opcode: str
    pipe: str
    start_us: float
    gap_us: float
    events: int

    def as_dict(self) -> Dict[str, object]:
        return {
            "pc": self.pc, "opcode": self.opcode, "pipe": self.pipe,
            "start_us": self.start_us, "gap_us": self.gap_us,
            "events": self.events,
        }


@dataclass
class PcCycleInsight:
    available: bool = False
    reason: str = ""
    events: int = 0
    distinct_pcs: int = 0
    span_us: Optional[float] = None
    stalls: List[Dict[str, object]] = field(default_factory=list)
    total_stall_us: float = 0.0
    stall_pct: Optional[float] = None
    threshold_us: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "available": self.available, "reason": self.reason,
            "events": self.events, "distinct_pcs": self.distinct_pcs,
            "span_us": self.span_us, "stalls": self.stalls,
            "total_stall_us": round(self.total_stall_us, 4),
            "stall_pct": self.stall_pct,
            "threshold_us": round(self.threshold_us, 4),
        }


def analyse_pc_cycle(trace: Dict[str, object],
                     kernel_us: Optional[float] = None,
                     min_gap_us: float = 0.05) -> PcCycleInsight:
    """Find intervals where the PC stayed put long enough to look like a stall.

    Only InstrTimeline events carry both a timestamp and a PC, so this needs
    --with-instructions. The threshold is absolute microseconds rather than a
    multiple of event duration: instrumented events are sub-nanosecond, so a
    ratio test would flag every pair.
    """
    insight = PcCycleInsight(threshold_us=min_gap_us)
    events = []
    for event in (trace or {}).get("traceEvents", []):
        if not isinstance(event, dict) or event.get("ph") != "X":
            continue
        args = event.get("args") or {}
        pc = args.get("pc_addr")
        ts = event.get("ts")
        if pc is None or ts is None:
            continue
        try:
            events.append((float(ts), str(pc), str(event.get("name", "?")),
                           str(event.get("tid", "?")),
                           float(event.get("dur", 0.0) or 0.0)))
        except (TypeError, ValueError):
            continue
    if not events:
        insight.reason = ("no events carry both pc_addr and ts "
                          "(requires --with-instructions)")
        return insight

    events.sort(key=lambda e: e[0])
    insight.available = True
    insight.events = len(events)
    insight.distinct_pcs = len({e[1] for e in events})
    insight.span_us = round(events[-1][0] - events[0][0], 4)

    # Group consecutive events sharing a PC; a large internal gap is the signal.
    origin = events[0][0]
    stalls: List[PcStall] = []
    index = 0
    while index < len(events):
        run_start = index
        pc = events[index][1]
        while index + 1 < len(events) and events[index + 1][1] == pc:
            index += 1
        run_end = index
        if run_end > run_start:
            gap = events[run_end][0] - events[run_start][0]
            if gap >= min_gap_us:
                stalls.append(PcStall(
                    pc=pc,
                    opcode=events[run_start][2],
                    pipe=events[run_start][3],
                    start_us=round(events[run_start][0] - origin, 4),
                    gap_us=round(gap, 4),
                    events=run_end - run_start + 1,
                ))
        index += 1

    stalls.sort(key=lambda s: -s.gap_us)
    insight.stalls = [s.as_dict() for s in stalls[:10]]
    insight.total_stall_us = sum(s.gap_us for s in stalls)
    if insight.span_us:
        insight.stall_pct = round(
            100.0 * insight.total_stall_us / insight.span_us, 2)
    return insight


def render_pc_cycle(insight: PcCycleInsight) -> List[str]:
    lines: List[str] = []
    if not insight.available:
        lines.append(f"Unavailable: {insight.reason}")
        lines.append("")
        return lines
    lines.append(f"- {insight.events} events carry a PC, {insight.distinct_pcs} distinct PCs, "
                 f"span {insight.span_us:.3f} us")
    lines.append(f"- Intervals where one PC resided for at least {insight.threshold_us:.2f} us: "
                 f"**{len(insight.stalls)} sites**, totalling {insight.total_stall_us:.3f} us"
                 + (f"({insight.stall_pct:.1f}% of span)"
                    if insight.stall_pct is not None else ""))
    lines.append("")
    if insight.stalls:
        lines.append("| PC | Opcode | Pipe | Start us | Residence us | Events |")
        lines.append("|---|---|---|---:|---:|---:|")
        for entry in insight.stalls:
            lines.append(f"| `{entry['pc']}` | `{entry['opcode']}` | {entry['pipe']} | "
                         f"{entry['start_us']:.3f} | {entry['gap_us']:.3f} | "
                         f"{entry['events']} |")
        lines.append("")
        lines.append("> A PC that does not advance for a long interval is waiting on instruction fetch. scalar_story localises icache misses "
                     "by the long horizontal segments of a PC-vs-cycle plot; this is the numeric equivalent. On a hit, "
                     "insert an icache prefetch one or two basic blocks before the loop entry or call.")
        lines.append("")
    return lines


def render_layers(sections: Dict[str, List[str]]) -> List[str]:
    """Assemble the layered view given already-rendered per-layer lines."""
    lines: List[str] = []
    for layer in LAYERS:
        body = sections.get(layer)
        if not body:
            continue
        lines.append(f"### {LAYER_LABELS[layer]}")
        lines.append("")
        lines.append(f"> {LAYER_DESCRIPTIONS[layer]}")
        lines.append("")
        lines.extend(body)
        lines.append("")
    return lines
