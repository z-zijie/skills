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
    "soc": "SoC 层（数据搬运 / 总线 / L2）",
    "core": "核内层（流水线 / 冲突 / 片上缓存）",
    "schedule": "调度层（block 启动与结束 / 多核同步）",
}

LAYER_DESCRIPTIONS = {
    "soc": "跨核共享的资源。这一层出的问题靠改 kernel 代码解决不了，要改算法规格或 tiling 总量。",
    "core": "单核内部各流水的竞争。这一层回答「瓶颈是哪个单元」。",
    "schedule": "每个 block 的时间轴。这一层回答「核有没有一起开工、有没有拖尾」。",
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
        insight.reason = "没有 block 级时间戳（当前采集未提供 start/end）"
        return insight

    insight.available = True
    insight.blocks = len(usable)
    insight.source = usable[0].source

    starts = [b.start_us for b in usable if b.start_us is not None]
    ends = [b.end_us for b in usable if b.end_us is not None]
    durations = [b.duration_us for b in usable if b.duration_us is not None]
    if not starts or not ends:
        insight.available = False
        insight.reason = "block 记录缺少 start 或 end"
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
        lines.append(f"不可用：{insight.reason}")
        lines.append("")
        lines.append("> block 级时间戳需要 `--aic-metrics=PipeTimeline`（单独一趟采集）。"
                     "它只插桩部分核，因此覆盖的 block 数可能小于 Block Dim。")
        lines.append("")
        return lines

    lines.append(f"- 采集到 **{insight.blocks}** 个 block 的时间轴（来源 `{insight.source}`）")
    lines.append(f"- 首个 block 启动 {insight.first_start_us:.3f} us，"
                 f"最后一个启动 {insight.last_start_us:.3f} us")
    lines.append("")
    lines.append("| 指标 | 值 | 含义 |")
    lines.append("|---|---:|---|")
    lines.append(f"| **启动偏移 (launch skew)** | {insight.launch_skew_us:.3f} us"
                 + (f" ({insight.launch_skew_pct:.1f}%)" if insight.launch_skew_pct else "")
                 + " | 这段时间内芯片只有部分核在工作 |")
    lines.append(f"| **拖尾 (tail)** | {insight.tail_us:.3f} us"
                 + (f" ({insight.tail_pct:.1f}%)" if insight.tail_pct else "")
                 + " | 最先完成的核到最慢核完成的间隔 |")
    lines.append(f"| block 耗时范围 | {insight.min_duration_us:.3f}–{insight.max_duration_us:.3f} us | "
                 f"离散度 {insight.duration_spread_pct:.1f}% |")
    lines.append("")

    if insight.launch_skew_pct is not None and insight.launch_skew_pct > 10:
        lines.append(f"> **启动不同步**：偏移占 kernel 时长的 {insight.launch_skew_pct:.0f}%。"
                     "这部分时间芯片利用率按比例下降，与流水线优化无关，"
                     "应先查 tiling 是否让各核依赖同一个前置步骤。")
        lines.append("")
    if insight.tail_pct is not None and insight.tail_pct > 20:
        lines.append(f"> **拖尾明显**：最慢核比最快核晚 {insight.tail_us:.2f} us 收工"
                     f"（{insight.tail_pct:.0f}%）。典型原因是尾块数据量不均或某个核被抢占。")
        lines.append("")

    if insight.slowest_blocks:
        lines.append("最慢的 block：")
        lines.append("")
        lines.append("| block | 类型 | 启动 us | 结束 us | 耗时 us |")
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
    0: "GM 读（本 die）",
    1: "GM 写（本 die）",
    2: "跨 die 通路 2（推断）",
    6: "跨 die 通路 6（推断）",
    7: "跨 die 通路 7（推断）",
    8: "跨 die 通路 8（推断）",
    12: "UB←GM（MTE2 落 UB）",
    13: "UB→GM（MTE3）",
    14: "UB→L2（vector 读）",
    15: "L2→UB（vector 写）",
    37: "跨 die 通路 37（推断）",
    38: "跨 die 通路 38（推断）",
    39: "保留（恒 0）",
    40: "GM 读（cube 侧，推断）",
    41: "保留（恒 0）",
    48: "L0A/B 通路",
    50: "保留（恒 0）",
    52: "L0C 通路",
    54: "FixPipe 通路",
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
        result.reason = "没有 core_memory_map（visualize_data.bin 0x08）"
        return result

    units = (viz_cores[0] or {}).get("memory_unit") or []
    if not units:
        result.reason = "core 0 没有 memory_unit"
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
            "name": MEMORY_PATH_NAMES.get(path_id, f"path {path_id}（未命名）"),
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
        lines.append(f"不可用：{paths.reason}")
        lines.append("")
        return lines

    die_note = (f"本芯片 io_die_num={paths.die_count}（双 die）"
                if paths.die_count else "die 数量未知")
    lines.append(f"- {die_note}")
    lines.append(f"- 跨 die 流量 {paths.cross_die_gbps:.3f} GB/s，"
                 f"片内流量 {paths.on_die_gbps:.3f} GB/s"
                 + (f"，**跨 die 占 {paths.cross_die_pct:.1f}%**"
                    if paths.cross_die_pct is not None else ""))
    lines.append("")
    lines.append("| path | 名称 | 带宽 GB/s | 请求数 | 跨 die |")
    lines.append("|---:|---|---:|---:|:--:|")
    for entry in paths.paths:
        lines.append(
            f"| {entry['path_id']} | {entry['name']} | {entry['bandwidth_gbps']:.3f} | "
            f"{entry['requests']} | {'是' if entry['cross_die'] else ''} |")
    lines.append("")
    lines.append("> path 名称是**推断**的（CANN 未公开 id 含义）：0/1 与 GM 读写吻合，"
                 "12–15 与 UB 表逐字节相同，2/6/7/8/37/38 只在 cube kernel 上出现且有流量。"
                 "置信度按推断给，不按实测给。")
    lines.append("")
    if paths.cross_die_pct is not None and paths.cross_die_pct > 20:
        lines.append(f"> **跨 die 流量占比 {paths.cross_die_pct:.0f}%**：双 die 上核访问到对端 "
                     "die 的 L2 要走 SIO，延迟显著高于本 die。可行方向：按 GM 地址的 L2 交织"
                     "关系让每个核只写本 die 的 cacheline、减少多核写同一 cacheline、"
                     "或减少参与写同一地址的核数。")
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
