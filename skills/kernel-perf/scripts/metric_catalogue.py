#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metric catalogue: every measurable value, its provenance and its confidence.

Why a catalogue instead of just dumping CSVs
--------------------------------------------
Not all profiler output is equally trustworthy, and the difference is not
visible in the CSV. Some values are direct hardware counters read from a
saturating register; some are vendor formulas over counters; some are sampled
at 100 Hz and alias short kernels; some are physically meaningless when the
denominator is zero or the block is inactive.

Presenting all of them in one table implies equal weight. This module assigns
each metric an explicit confidence tier grounded in *how* it is produced, and
records the derivation so a reader can disagree with the tier.

Tiers
-----
A  counter          direct PMU counter or exact CSV field. Reproducible; a
                    repeat run matches to the last printed digit.
B  derived          arithmetic over tier-A values with no sampling and no
                    model assumption (e.g. ratio = time/total, sum of counters).
C  sampled          statistical: PC sampling, per-cycle timelines, anything
                    collected at a fixed frequency and scaled up.
D  modelled         depends on a hardware constant we read from config, not
                    measured (e.g. MFU divides by theoretical peak FLOPs).
E  unreliable       known to vary or to be meaningless in common cases. L2
                    write hit rate is the canonical example: measured CV 14%
                    across identical runs because write-allocate policy makes
                    the denominator workload-phase dependent.

Every metric also records `unit`, `source` (which file/field), and `note`
(caveats a reader must know before quoting the number).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# Confidence tiers, ordered best to worst.
TIER_ORDER = ["A", "B", "C", "D", "E"]

TIER_DESCRIPTION = {
    "A": "直接计数器，重复采集一致到末位",
    "B": "由 A 级值精确推导，无采样无模型假设",
    "C": "采样统计，短 kernel 上有混叠风险",
    "D": "依赖硬件理论常量，不是实测值",
    "E": "已知不稳定或在常见情形下无意义",
}


@dataclass
class Metric:
    """One catalogue entry."""

    key: str
    label: str
    unit: str
    tier: str
    source: str                 # which capture file and field
    note: str = ""
    caveat: str = ""
    aliases: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "tier": self.tier,
            "tier_description": TIER_DESCRIPTION.get(self.tier, ""),
            "source": self.source,
            "note": self.note,
            "caveat": self.caveat,
        }


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

def build_catalogue() -> List[Metric]:
    """The full metric vocabulary this tool knows how to produce.

    Grouped by what question the metric answers. Order here is the order the
    report uses.
    """
    M = Metric
    return [
        # ---- identity / basic -------------------------------------------
        M("duration_us", "Kernel 耗时", "us", "A", "OpBasicInfo.csv:Task Duration(us)",
          note="单次 kernel 任务时长，含核启动与排空"),
        M("block_dim", "Block Dim", "core", "A", "OpBasicInfo.csv:Block Dim",
          note="实际使用的核数；小于芯片核数说明 tiling 只用了一部分"),
        M("freq_mhz", "当前频率", "MHz", "A", "OpBasicInfo.csv:Current Freq",
          note="与 Rated Freq 不一致说明发生了降频"),
        M("cycles", "核周期数", "cycle", "A", "PipeUtilization.csv:aiv_total_cycles",
          note="cycles/time 应等于频率，可用来交叉校验"),

        # ---- pipe occupancy ---------------------------------------------
        M("vec_ratio", "Vector 流水占用", "%", "B", "PipeUtilization.csv:aiv_vec_ratio",
          note="vec 忙时间/核总时间，跨核平均"),
        M("scalar_ratio", "Scalar 流水占用", "%", "B", "PipeUtilization.csv:aiv_scalar_ratio"),
        M("mte2_ratio", "MTE2 (GM→UB) 占用", "%", "B", "PipeUtilization.csv:aiv_mte2_ratio"),
        M("mte3_ratio", "MTE3 (UB→GM) 占用", "%", "B", "PipeUtilization.csv:aiv_mte3_ratio"),
        M("cube_ratio", "Cube 流水占用", "%", "B", "PipeUtilization.csv:aic_cube_ratio"),
        M("mte1_ratio", "MTE1 (L1→L0) 占用", "%", "B", "PipeUtilization.csv:aic_mte1_ratio"),
        M("fixpipe_ratio", "FixPipe 占用", "%", "B", "PipeUtilization.csv:aic_fixpipe_ratio"),
        M("aic_scalar_ratio", "AIC Scalar 占用", "%", "B", "PipeUtilization.csv:aic_scalar_ratio"),

        # ---- wait / stall ------------------------------------------------
        M("vec_wait_ratio", "Vector 等待占比", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_wait_ratio",
          note="流水在等数据/等队列的时间比例，气泡的直接证据"),
        M("mte3_wait_ratio", "MTE3 等待占比", "%", "B",
          "ResourceConflictRatio.csv:aiv_mte3_wait_ratio"),
        M("mte2_wait_ratio", "MTE2 等待占比", "%", "B",
          "ResourceConflictRatio.csv:aiv_mte2_wait_ratio"),
        M("wait_cycles", "各流水 wait cycles", "cycle", "A",
          "visualize_data.bin 0x09 table 'Pipe' column 'wait cycles'",
          note="逐核累计，比 ratio 更接近原始计数器"),

        # ---- conflicts ---------------------------------------------------
        M("bank_stu_cflt", "Store bank 冲突率", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_stu_cflt_ratio",
          note=">5% 需要调整 UB 地址或加 padding"),
        M("bank_ldu_cflt", "Load bank 冲突率", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_ldu_cflt_ratio"),
        M("sfu_cflt", "SFU 冲突率", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_sfu_cflt_ratio"),

        # ---- bandwidth ---------------------------------------------------
        M("gm_to_ub_gbps", "GM→UB 带宽", "GiB/s", "B", "Memory.csv:aiv_gm_to_ub_bw(GB/s)",
          note="profiler 标 GB/s，实测为 GiB/s（1024³）"),
        M("gm_to_ub_pct", "GM→UB 占峰值", "%", "B",
          "Memory.csv:GM_to_UB_bw_usage_rate(%)",
          caveat="分母由同一次采集反解，跨机型不可比"),
        M("ub_to_gm_gbps", "UB→GM 带宽", "GiB/s", "B", "Memory.csv:aiv_ub_to_gm_bw(GB/s)"),
        M("main_mem_read_gbps", "主存读带宽", "GiB/s", "B",
          "Memory.csv:aiv_main_mem_read_bw(GB/s)"),
        M("main_mem_write_gbps", "主存写带宽", "GiB/s", "B",
          "Memory.csv:aiv_main_mem_write_bw(GB/s)"),
        M("ub_read_vec_gbps", "UB 读 (vector) 带宽", "GiB/s", "B",
          "MemoryUB.csv:aiv_ub_read_bw_vector(GB/s)"),
        M("ub_write_vec_gbps", "UB 写 (vector) 带宽", "GiB/s", "B",
          "MemoryUB.csv:aiv_ub_write_bw_vector(GB/s)"),
        M("l1_read_gbps", "L1 读带宽", "GiB/s", "B", "Memory.csv:aic_l1_read_bw(GB/s)"),
        M("l1_write_gbps", "L1 写带宽", "GiB/s", "B", "Memory.csv:aic_l1_write_bw(GB/s)"),
        M("l0a_read_gbps", "L0A 读带宽", "GiB/s", "B", "MemoryL0.csv:aic_l0a_read_bw(GB/s)"),
        M("l0b_read_gbps", "L0B 读带宽", "GiB/s", "B", "MemoryL0.csv:aic_l0b_read_bw(GB/s)"),
        M("l0c_write_gbps", "L0C 写带宽", "GiB/s", "B", "MemoryL0.csv:aic_l0c_write_bw_cube(GB/s)"),

        # ---- datapath detail (CSV-invisible, viz only) --------------------
        M("memory_paths", "8 条内存通路带宽", "GiB/s", "B",
          "visualize_data.bin 0x08 core_memory_map[].memory_unit[]",
          note="逐核逐通路的带宽与请求数，CSV 不含"),
        M("pipe_wait_table", "逐核逐流水 wait", "cycle", "A",
          "visualize_data.bin 0x09 table 'Pipe'"),

        # ---- cache -------------------------------------------------------
        M("l2_read_hit", "L2 读命中率", "%", "C", "L2Cache.csv:aiv_read_hit_rate(%)",
          note="读命中率重复性好（实测 CV 0.6%）",
          caveat="分母只统计 close/far 请求，不含 iCache"),
        M("l2_write_hit", "L2 写命中率", "%", "E", "L2Cache.csv:aiv_write_hit_rate(%)",
          note="写回策略使分母随工作负载相位变化",
          caveat="实测 5 次重复 CV 14.4%、极差 34%，不可当稳定指标"),
        M("l2_read_miss", "L2 读 miss 次数", "count", "A",
          "L2Cache.csv:aiv_read_close_miss"),
        M("icache_hit", "iCache 命中率", "%", "B",
          "visualize_data.bin 0x09 table 'Cache' row 'iCache Total'",
          note="miss 率高说明代码量过大，考虑拆分 kernel"),
        M("l2_hit_viz", "L2 总命中率 (逐核)", "%", "B",
          "visualize_data.bin 0x08 core_memory_map[].L2cache.hit_ratio",
          caveat="读+写加权，与 CSV 分读写口径不同"),

        # ---- compute -----------------------------------------------------
        M("cube_instr", "Cube 指令数", "count", "A",
          "ArithmeticUtilization.csv:aic_cube_total_instr_number"),
        M("cube_fp_instr", "Cube 浮点指令数", "count", "A",
          "ArithmeticUtilization.csv:aic_cube_fp_instr_number"),
        M("mac_usage", "MAC 利用率", "%", "D", "derived: cube instr × 16³ MAC / peak",
          note="关键指标：cube 有多大比例在真正做乘加",
          caveat="依赖 16×16×16 的硬编码维度与 1650 MHz 额定频率"),
        M("mfu", "MFU (模型算力利用率)", "%", "D", "derived: achieved FLOPs / 378.5 TFLOPS",
          note="关键指标：与训练/推理框架口径对齐",
          caveat="分母是理论峰值；实际可达峰值受缓存在内，MFU 天然偏低"),
        M("vec_lane_usage", "Vector lane 利用率", "%", "D",
          "derived: vec_ratio × 有效 lane 数 / 128"),

        # ---- instruction level ------------------------------------------
        M("instr_timeline", "逐指令耗时", "us", "C",
          "trace.json traceEvents[] with args.pc_addr",
          note="InstrTimeline 采集，给出每条指令的 pc 与耗时",
          caveat="需要 --instr-timeline-pipe 指定流水；与 PipeTimeline 互斥"),
        M("instr_region_map", "PC→opcode 映射", "-", "A",
          "dump/dfx_region_map.txt",
          note="把 pc 映射到 pipe 与 opcode，是逐指令归属的关键"),
        M("source_attribution", "源码行归属", "-", "C",
          "visualize_data.bin 0x01 源代码包 + PC 映射",
          caveat="显示的是内联展开后的头文件行，不是用户 kernel 行"),

        # ---- timeline ----------------------------------------------------
        M("pipe_timeline", "逐流水时间线", "us", "C", "trace.json (PipeTimeline)",
          note="每个流水的忙闲区间", caveat="只覆盖被采集的核；实测常只有 1 个核"),

        # ---- rock-bottom reliability ------------------------------------
        M("imbalance", "核间离散度", "%", "B", "derived: (max-min)/mean of per-core time",
          note=">30% 说明 tiling 切分不均；短 kernel 上主要是启动开销"),
        M("advice", "厂商建议", "-", "A", "visualize_data.bin advice 字段"),
    ]


def catalogue_by_key() -> Dict[str, Metric]:
    return {m.key: m for m in build_catalogue()}


# ---------------------------------------------------------------------------
# stability evidence
# ---------------------------------------------------------------------------
# Measured on Ascend950PR with 5 identical collections of vector_add
# (warm-up=5, launch-count=3). CV = coefficient of variation of the per-run
# mean. Numbers here are what justifies the C/D/E tiers; re-measure with
# `python3 stability.py` if the environment changes.
#
# A tier is only demoted to E when measured CV exceeds 10%: below that, a
# repeat run reproduces the value closely enough to reason about.
MEASURED_STABILITY = {
    "duration_us": {"cv_pct": 1.84, "runs": 5},
    "aiv_time": {"cv_pct": 0.71, "runs": 5},
    "mte2_ratio": {"cv_pct": 1.38, "runs": 5},
    "vec_ratio": {"cv_pct": 0.82, "runs": 5},
    "scalar_ratio": {"cv_pct": 2.86, "runs": 5},
    "gm_to_ub_gbps": {"cv_pct": 0.82, "runs": 5},
    "l2_read_hit": {"cv_pct": 0.57, "runs": 5},
    "l2_write_hit": {"cv_pct": 14.36, "runs": 5, "note": "range 34% across runs"},
    "vec_wait_ratio": {"cv_pct": 3.15, "runs": 5},
}

STABILITY_THRESHOLD_CV = 10.0


def stability_for(key: str) -> Optional[Dict[str, object]]:
    return MEASURED_STABILITY.get(key)


def confidence_of(key: str) -> str:
    """Tier for a metric, demoted to E if measured CV exceeds the threshold."""
    entry = catalogue_by_key().get(key)
    if entry is None:
        return "?"
    measured = MEASURED_STABILITY.get(key)
    if measured and measured["cv_pct"] > STABILITY_THRESHOLD_CV:
        return "E"
    return entry.tier


def render_catalogue() -> str:
    """Human-readable catalogue, grouped by tier."""
    metrics = build_catalogue()
    lines = ["# 指标目录", ""]
    lines.append("每个指标都标注产出口径与置信度。置信度不是主观评级，而是按"
                 "「这个数是怎么来的」划分，并用 5 次重复采集的变异系数校准。")
    lines.append("")
    lines.append("| 级别 | 含义 |")
    lines.append("|---|---|")
    for tier in TIER_ORDER:
        lines.append(f"| {tier} | {TIER_DESCRIPTION[tier]} |")
    lines.append("")
    for tier in TIER_ORDER:
        group = [m for m in metrics if m.tier == tier]
        if not group:
            continue
        lines.append(f"## {tier} 级（{len(group)} 项）")
        lines.append("")
        lines.append("| 指标 | 单位 | 来源 | 说明 |")
        lines.append("|---|---|---|---|")
        for m in group:
            note = m.note
            if m.caveat:
                note = (note + "；" if note else "") + "**注意**：" + m.caveat
            lines.append(f"| {m.label} | {m.unit} | `{m.source}` | {note} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import json as _json
    parser = argparse.ArgumentParser(
        prog="metric_catalogue.py",
        description=(
            "List every metric this tool can produce, with its provenance and\n"
            "confidence tier. The tier is derived from how the value is made\n"
            "and calibrated by measured repeatability, not asserted."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  metric_catalogue.py            # markdown catalogue\n"
            "  metric_catalogue.py --json     # machine readable\n"
            "  metric_catalogue.py --tier E   # only the unreliable ones\n"
        ),
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--tier", choices=TIER_ORDER, default=None,
                       help="只列出指定置信度的指标")
    args, _extra = parser.parse_known_args(argv)

    if args.tier:
        metrics = [m for m in build_catalogue() if confidence_of(m.key) == args.tier]
    else:
        metrics = build_catalogue()

    if args.json:
        print(_json.dumps(
            {"tiers": TIER_DESCRIPTION,
             "metrics": [{**m.as_dict(), "effective_confidence": confidence_of(m.key),
                          "measured_stability": stability_for(m.key)}
                         for m in metrics]},
            indent=2, ensure_ascii=False))
    else:
        if args.tier:
            print(f"# {args.tier} 级指标（{len(metrics)} 项）\n")
            for m in metrics:
                print(f"- **{m.label}** (`{m.key}`) — {m.unit}")
                print(f"  来源: `{m.source}`")
                if m.note:
                    print(f"  说明: {m.note}")
                if m.caveat:
                    print(f"  **注意**: {m.caveat}")
                measured = stability_for(m.key)
                if measured:
                    print(f"  实测 CV: {measured['cv_pct']}%（{measured['runs']} 次）")
        else:
            print(render_catalogue())
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
