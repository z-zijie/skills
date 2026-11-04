#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instruction-level analysis: which instructions actually cost the time.

This is the deepest layer the tool reaches, and it needs three artefacts that
only appear when the profiler is asked for instruction instrumentation:

1. ``dump/dfx_region_map.txt`` -- maps each probed program counter to the pipe
   it runs on and the actual instruction opcode. Reverse engineered format,
   one directive per line::

       region_id=1536,pipe=mte2,bb_off=0x454,inst_off=0x18,pc=0x0000046c,opcode=GET_BUF

   ``pc`` is relative to the kernel start; ``bb_off`` is the basic block. The
   same ``region_id`` recurs across basic blocks and pipes, so region_id alone
   is NOT a unique key -- (pipe, bb_off, pc) is.

2. ``trace.json`` -- Chrome-tracing events carrying ``args.pc_addr`` (absolute)
   and ``dur`` in microseconds. Subtracting ``dump/pc_start_addr.txt`` from an
   absolute pc yields the kernel-relative offset that matches dfx_region_map.

3. ``dump/pc_start_addr.txt`` -- the absolute base address.

Joining (1) and (2) produces what NVIDIA NCU reports as "instruction-level
sampling": per-opcode time, per-pipe time, and the hottest PC in the kernel.
Without the join, trace.json gives durations against bare addresses and
dfx_region_map gives opcodes against bare pcs -- neither is readable.

Honesty about precision
-----------------------
These are *sampled* durations (tier C). A single launch yields tens of events,
not millions, so percentages for rare instructions are noisy. The tool reports
the covered fraction explicitly: if the trace accounts for only a small part of
the kernel's runtime, the breakdown describes the instrumented region, not the
whole kernel.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REGION_RE = re.compile(
    r"region_id=(?P<region>\d+),"
    r"pipe=(?P<pipe>[\w]+),"
    r"bb_off=(?P<bb>0x[0-9a-fA-F]+),"
    r"inst_off=(?P<inst>0x[0-9a-fA-F]+),"
    r"pc=(?P<pc>0x[0-9a-fA-F]+),"
    r"opcode=(?P<opcode>[\w]+)"
)


@dataclass
class Region:
    """One probed instruction site."""

    region_id: int
    pipe: str
    bb_off: int
    inst_off: int
    pc: int
    opcode: str


@dataclass
class InstrInsight:
    """Aggregated instruction-level result."""

    available: bool = False
    reason: str = ""
    base_pc: Optional[int] = None
    regions: int = 0
    events: int = 0
    covered_us: float = 0.0
    kernel_us: Optional[float] = None
    covered_pct: Optional[float] = None
    by_opcode: List[Dict[str, Any]] = field(default_factory=list)
    by_pipe: List[Dict[str, Any]] = field(default_factory=list)
    hottest: List[Dict[str, Any]] = field(default_factory=list)
    unmatched_events: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "base_pc": hex(self.base_pc) if self.base_pc is not None else None,
            "regions": self.regions,
            "events": self.events,
            "covered_us": round(self.covered_us, 4),
            "kernel_us": self.kernel_us,
            "covered_pct": round(self.covered_pct, 2) if self.covered_pct is not None else None,
            "by_opcode": self.by_opcode,
            "by_pipe": self.by_pipe,
            "hottest": self.hottest,
            "unmatched_events": self.unmatched_events,
        }


def parse_region_map(path: Path) -> List[Region]:
    """Parse dfx_region_map.txt. Tolerates a header line and blank lines.

    The file begins with a two-line header (``InstrumentDfxRegion`` and
    ``function=...``) that carries the kernel name; we skip anything that does
    not match the directive pattern rather than assuming a fixed line offset.
    """
    if not path.is_file():
        return []
    regions: List[Region] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        match = REGION_RE.search(line)
        if not match:
            continue
        try:
            regions.append(Region(
                region_id=int(match.group("region")),
                pipe=match.group("pipe"),
                bb_off=int(match.group("bb"), 16),
                inst_off=int(match.group("inst"), 16),
                pc=int(match.group("pc"), 16),
                opcode=match.group("opcode"),
            ))
        except ValueError:
            continue
    return regions


def parse_base_pc(path: Path) -> Optional[int]:
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip(), 16)
    except (OSError, ValueError):
        return None


def find_dump_dir(capture_root: Path) -> Optional[Path]:
    """Locate the per-kernel dump directory.

    msopprof nests it as ``device0/<mangled kernel>/<n>/dump`` for
    InstrTimeline but writes ``dump/`` directly for the simpler metric sets.
    """
    # An archived copy (see kernel_perf.preserve_probe_artifacts) is preferred
    # because msopprof deletes these files from their original location.
    probe = capture_root / "probe"
    if (probe / "dfx_region_map.txt").is_file() or (probe / "pc_start_addr.txt").is_file():
        return probe
    direct = capture_root / "dump"
    if (direct / "dfx_region_map.txt").is_file() or (direct / "pc_start_addr.txt").is_file():
        return direct
    for candidate in sorted(capture_root.glob("device*/*/*/dump")):
        if (candidate / "dfx_region_map.txt").is_file():
            return candidate
    for candidate in sorted(capture_root.glob("device*/**/dump")):
        if (candidate / "pc_start_addr.txt").is_file():
            return candidate
    return None


def analyze(capture_root: Path, kernel_us: Optional[float] = None) -> InstrInsight:
    """Join region map and trace into per-opcode cost."""
    dump = find_dump_dir(capture_root)
    if dump is None:
        return InstrInsight(reason="没有 dump 目录（需要 InstrTimeline 采集）")

    regions = parse_region_map(dump / "dfx_region_map.txt")
    if not regions:
        return InstrInsight(reason="dfx_region_map.txt 缺失或为空")

    trace_path = capture_root / "trace.json"
    if not trace_path.is_file():
        return InstrInsight(
            reason="trace.json 缺失（InstrTimeline 未产出）",
            regions=len(regions),
        )

    try:
        trace = json.loads(trace_path.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        return InstrInsight(reason=f"trace.json 无法解析: {exc!r}", regions=len(regions))

    base_pc = parse_base_pc(dump / "pc_start_addr.txt")
    events = [e for e in trace.get("traceEvents", []) if e.get("ph") == "X"]

    # pc -> region. Multiple regions can share a pc across pipes, so key on
    # (pipe, pc) and fall back to pc alone.
    by_pipe_pc: Dict[Tuple[str, int], Region] = {}
    by_pc: Dict[int, Region] = {}
    for region in regions:
        by_pipe_pc[(region.pipe, region.pc)] = region
        by_pc.setdefault(region.pc, region)

    opcode_time: Dict[str, Dict[str, Any]] = {}
    pipe_time: Dict[str, Dict[str, Any]] = {}
    pc_time: Dict[int, Dict[str, Any]] = {}
    covered = 0.0
    unmatched = 0

    for event in events:
        args = event.get("args") or {}
        raw_pc = args.get("pc_addr")
        duration = event.get("dur")
        if not raw_pc or duration is None:
            continue
        try:
            absolute = int(str(raw_pc), 16)
        except ValueError:
            unmatched += 1
            continue
        relative = absolute - base_pc if base_pc is not None else absolute
        pipe = str(event.get("tid", "?"))
        region = by_pipe_pc.get((pipe, relative)) or by_pc.get(relative)
        opcode = region.opcode if region else str(event.get("name", "?"))
        pipe_name = region.pipe if region else pipe
        covered += duration

        entry = opcode_time.setdefault(opcode, {"opcode": opcode, "time_us": 0.0, "count": 0})
        entry["time_us"] += duration
        entry["count"] += 1
        pipe_entry = pipe_time.setdefault(pipe_name, {"pipe": pipe_name, "time_us": 0.0, "count": 0})
        pipe_entry["time_us"] += duration
        pipe_entry["count"] += 1
        pc_entry = pc_time.setdefault(relative, {
            "pc_offset": relative, "pc": hex(relative), "opcode": opcode,
            "pipe": pipe_name, "time_us": 0.0, "count": 0,
        })
        pc_entry["time_us"] += duration
        pc_entry["count"] += 1

    insight = InstrInsight(
        available=covered > 0,
        base_pc=base_pc,
        regions=len(regions),
        events=len(events),
        covered_us=covered,
        kernel_us=kernel_us,
        unmatched_events=unmatched,
        reason="" if covered > 0 else "trace 中没有任何可归属的事件",
    )
    if kernel_us:
        insight.covered_pct = 100.0 * covered / kernel_us

    insight.by_opcode = sorted(
        ({**v, "time_us": round(v["time_us"], 4),
          "pct": round(100.0 * v["time_us"] / covered, 2) if covered else 0.0}
         for v in opcode_time.values()),
        key=lambda e: -e["time_us"],
    )
    insight.by_pipe = sorted(
        ({**v, "time_us": round(v["time_us"], 4),
          "pct": round(100.0 * v["time_us"] / covered, 2) if covered else 0.0}
         for v in pipe_time.values()),
        key=lambda e: -e["time_us"],
    )
    insight.hottest = sorted(
        ({**v, "time_us": round(v["time_us"], 4),
          "pct": round(100.0 * v["time_us"] / covered, 2) if covered else 0.0}
         for v in pc_time.values()),
        key=lambda e: -e["time_us"],
    )[:20]
    return insight


def render(insight: InstrInsight) -> List[str]:
    """Markdown lines for the instruction-level section."""
    lines: List[str] = ["## 12. 逐指令耗时（InstrTimeline）", ""]
    if not insight.available:
        lines.append(f"不可用：{insight.reason}")
        lines.append("")
        lines.append("> 需要 `--aic-metrics=InstrTimeline --instr-timeline-pipe=vector|mte2|mte3`。"
                     "该集合与 `PipeTimeline` 互斥，且必须与被采集 kernel 的流水一致——"
                     "vector 算子给 `scalar` 会被直接拒绝。")
        lines.append("")
        return lines

    lines.append(f"- 探针指令点 **{insight.regions}** 个，trace 事件 **{insight.events}** 条")
    lines.append(f"- 已归属耗时 **{insight.covered_us:.4f} us**"
                 + (f"（占 kernel {insight.covered_pct:.1f}%）"
                    if insight.covered_pct is not None else ""))
    if insight.covered_pct is not None and insight.covered_pct < 50:
        lines.append("")
        lines.append(f"> **覆盖率仅 {insight.covered_pct:.1f}%**：下面的分布描述的是被插桩的区间，"
                     "不是整个 kernel。不要把占比当作 kernel 的完整画像。")
    if insight.unmatched_events:
        lines.append(f"- 未能归属的事件：{insight.unmatched_events}")
    lines.append("")

    if insight.by_opcode:
        lines.append("### 按指令")
        lines.append("")
        lines.append("| 指令 | 累计耗时 us | 占比 | 次数 |")
        lines.append("|---|---:|---:|---:|")
        for entry in insight.by_opcode:
            lines.append(f"| `{entry['opcode']}` | {entry['time_us']:.4f} | "
                         f"{entry['pct']:.1f}% | {entry['count']} |")
        lines.append("")

    if insight.by_pipe:
        lines.append("### 按流水")
        lines.append("")
        lines.append("| 流水 | 累计耗时 us | 占比 | 次数 |")
        lines.append("|---|---:|---:|---:|")
        for entry in insight.by_pipe:
            lines.append(f"| {entry['pipe']} | {entry['time_us']:.4f} | "
                         f"{entry['pct']:.1f}% | {entry['count']} |")
        lines.append("")

    if insight.hottest:
        lines.append("### 最热指令点")
        lines.append("")
        lines.append("| PC 偏移 | 指令 | 流水 | 耗时 us | 占比 | 次数 |")
        lines.append("|---|---|---|---:|---:|---:|")
        for entry in insight.hottest[:12]:
            lines.append(f"| `{entry['pc']}` | `{entry['opcode']}` | {entry['pipe']} | "
                         f"{entry['time_us']:.4f} | {entry['pct']:.1f}% | {entry['count']} |")
        lines.append("")
    return lines
