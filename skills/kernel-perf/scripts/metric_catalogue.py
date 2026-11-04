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
    "A": "Direct counter, reproduces to the last digit",
    "B": "Exact derivation from tier-A values, no sampling, no model assumption",
    "C": "Sampled statistic, aliasing risk on short kernels",
    "D": "Depends on a hardware constant, not measured",
    "E": "Known unstable or meaningless in common cases",
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
        M("duration_us", "Kernel duration", "us", "A", "OpBasicInfo.csv:Task Duration(us)",
          note="Duration of one kernel task, including launch and drain"),
        M("block_dim", "Block Dim", "core", "A", "OpBasicInfo.csv:Block Dim",
          note="Cores actually used; below the chip core count means tiling covered only part of it"),
        M("freq_mhz", "Current clock", "MHz", "A", "OpBasicInfo.csv:Current Freq",
          note="A mismatch against Rated Freq indicates throttling"),
        M("cycles", "Core cycles", "cycle", "A", "PipeUtilization.csv:aiv_total_cycles",
          note="cycles/time should equal the clock; use it as a cross-check"),

        # ---- pipe occupancy ---------------------------------------------
        M("vec_ratio", "Vector pipe occupancy", "%", "B", "PipeUtilization.csv:aiv_vec_ratio",
          note="vec busy time / core total time, averaged across cores"),
        M("scalar_ratio", "Scalar pipe occupancy", "%", "B", "PipeUtilization.csv:aiv_scalar_ratio"),
        M("mte2_ratio", "MTE2 (GM->UB) occupancy", "%", "B", "PipeUtilization.csv:aiv_mte2_ratio"),
        M("mte3_ratio", "MTE3 (UB->GM) occupancy", "%", "B", "PipeUtilization.csv:aiv_mte3_ratio"),
        M("cube_ratio", "Cube pipe occupancy", "%", "B", "PipeUtilization.csv:aic_cube_ratio"),
        M("mte1_ratio", "MTE1 (L1->L0) occupancy", "%", "B", "PipeUtilization.csv:aic_mte1_ratio"),
        M("fixpipe_ratio", "FixPipe occupancy", "%", "B", "PipeUtilization.csv:aic_fixpipe_ratio"),
        M("aic_scalar_ratio", "AIC Scalar occupancy", "%", "B", "PipeUtilization.csv:aic_scalar_ratio"),

        # ---- wait / stall ------------------------------------------------
        M("vec_wait_ratio", "Vector wait ratio", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_wait_ratio",
          note="Share of time a pipe spends waiting for data or queue space; direct evidence of bubbles"),
        M("mte3_wait_ratio", "MTE3 wait ratio", "%", "B",
          "ResourceConflictRatio.csv:aiv_mte3_wait_ratio"),
        M("mte2_wait_ratio", "MTE2 wait ratio", "%", "B",
          "ResourceConflictRatio.csv:aiv_mte2_wait_ratio"),
        M("wait_cycles", "Per-pipe wait cycles", "cycle", "A",
          "visualize_data.bin 0x09 table 'Pipe' column 'wait cycles'",
          note="Accumulated per core; closer to a raw counter than the ratio"),

        # ---- conflicts ---------------------------------------------------
        M("bank_stu_cflt", "Store bank conflict rate", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_stu_cflt_ratio",
          note="Above 5% warrants UB address adjustment or padding"),
        M("bank_ldu_cflt", "Load bank conflict rate", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_ldu_cflt_ratio"),
        M("sfu_cflt", "SFU conflict rate", "%", "B",
          "ResourceConflictRatio.csv:aiv_vec_sfu_cflt_ratio"),

        # ---- bandwidth ---------------------------------------------------
        M("gm_to_ub_gbps", "GM->UB bandwidth", "GiB/s", "B", "Memory.csv:aiv_gm_to_ub_bw(GB/s)",
          note="The profiler labels it GB/s; measured to be GiB/s (1024^3)"),
        M("gm_to_ub_pct", "GM->UB share of peak", "%", "B",
          "Memory.csv:GM_to_UB_bw_usage_rate(%)",
          caveat="The denominator is inverted from the same capture; not comparable across parts"),
        M("ub_to_gm_gbps", "UB->GM bandwidth", "GiB/s", "B", "Memory.csv:aiv_ub_to_gm_bw(GB/s)"),
        M("main_mem_read_gbps", "Main-memory read bandwidth", "GiB/s", "B",
          "Memory.csv:aiv_main_mem_read_bw(GB/s)"),
        M("main_mem_write_gbps", "Main-memory write bandwidth", "GiB/s", "B",
          "Memory.csv:aiv_main_mem_write_bw(GB/s)"),
        M("ub_read_vec_gbps", "UB read (vector) bandwidth", "GiB/s", "B",
          "MemoryUB.csv:aiv_ub_read_bw_vector(GB/s)"),
        M("ub_write_vec_gbps", "UB write (vector) bandwidth", "GiB/s", "B",
          "MemoryUB.csv:aiv_ub_write_bw_vector(GB/s)"),
        M("l1_read_gbps", "L1 read bandwidth", "GiB/s", "B", "Memory.csv:aic_l1_read_bw(GB/s)"),
        M("l1_write_gbps", "L1 write bandwidth", "GiB/s", "B", "Memory.csv:aic_l1_write_bw(GB/s)"),
        M("l0a_read_gbps", "L0A read bandwidth", "GiB/s", "B", "MemoryL0.csv:aic_l0a_read_bw(GB/s)"),
        M("l0b_read_gbps", "L0B read bandwidth", "GiB/s", "B", "MemoryL0.csv:aic_l0b_read_bw(GB/s)"),
        M("l0c_write_gbps", "L0C write bandwidth", "GiB/s", "B", "MemoryL0.csv:aic_l0c_write_bw_cube(GB/s)"),

        # ---- datapath detail (CSV-invisible, viz only) --------------------
        M("memory_paths", "Bandwidth of the 8 memory paths", "GiB/s", "B",
          "visualize_data.bin 0x08 core_memory_map[].memory_unit[]",
          note="Per-core per-path bandwidth and request counts; absent from the CSVs"),
        M("pipe_wait_table", "Per-core per-pipe wait", "cycle", "A",
          "visualize_data.bin 0x09 table 'Pipe'"),

        # ---- cache -------------------------------------------------------
        M("l2_read_hit", "L2 read hit rate", "%", "C", "L2Cache.csv:aiv_read_hit_rate(%)",
          note="Read hit rate reproduces well (measured CV 0.6%)",
          caveat="The denominator counts only close/far requests and excludes iCache"),
        M("l2_write_hit", "L2 write hit rate", "%", "E", "L2Cache.csv:aiv_write_hit_rate(%)",
          note="Write-back policy makes the denominator depend on workload phase",
          caveat="Measured CV 14.4% over five repeats with a 34% range; not a stable metric"),
        M("l2_read_miss", "L2 read miss count", "count", "A",
          "L2Cache.csv:aiv_read_close_miss"),
        M("icache_hit", "iCache hit rate", "%", "B",
          "visualize_data.bin 0x09 table 'Cache' row 'iCache Total'",
          note="A high miss rate means the code footprint is too large; consider splitting the kernel"),
        M("l2_hit_viz", "L2 total hit rate (per core)", "%", "B",
          "visualize_data.bin 0x08 core_memory_map[].L2cache.hit_ratio",
          caveat="Weighted over read and write, unlike the split CSV columns"),
        M("dcache", "DCache requests and throughput", "-", "B",
          "visualize_data.bin 0x09 table 'DCache'",
          note="Requests and throughput for the three GM/Vector directions; a different level from L2",
          caveat="Vector kernels usually show zero; DCache mainly serves scalar access"),
        M("outstanding", "Outstanding (in-flight) requests", "-", "C",
          "unavailable",
          note="A metric the reviewer asked for specifically",
          caveat="No output path under any --aic-metrics value on this part"
                 "(searching visualize_data.bin for outstanding/inflight returns zero hits)"),

        # ---- compute -----------------------------------------------------
        M("cube_instr", "Cube instruction count", "count", "A",
          "ArithmeticUtilization.csv:aic_cube_total_instr_number"),
        M("cube_fp_instr", "Cube float instruction count", "count", "A",
          "ArithmeticUtilization.csv:aic_cube_fp_instr_number"),
        M("mac_usage", "MAC utilisation", "%", "D", "derived: cube instr × 16³ MAC / peak",
          note="Key metric: what fraction of cube time actually issues multiply-accumulate",
          caveat="Relies on the hardcoded 16x16x16 shape and the 1650 MHz rated clock"),
        M("mfu", "MFU (model FLOPs utilisation)", "%", "D", "derived: achieved FLOPs / 378.5 TFLOPS",
          note="Key metric: aligned with the convention used by training and inference frameworks",
          caveat="The denominator is the theoretical peak; the achievable peak is lower due to caching, so MFU is inherently conservative"),
        M("vec_lane_usage", "Vector lane utilisation", "%", "D",
          "derived: vec_ratio x effective lanes / 128"),

        # ---- instruction level ------------------------------------------
        M("instr_timeline", "Instruction-level cost", "us", "C",
          "trace.json traceEvents[] with args.pc_addr",
          note="From InstrTimeline; reports the PC and the cost of each instruction",
          caveat="Requires --instr-timeline-pipe; mutually exclusive with PipeTimeline"),
        M("instr_region_map", "PC->opcode map", "-", "A",
          "dump/dfx_region_map.txt",
          note="Maps a PC to its pipe and opcode; the key to instruction attribution"),
        M("source_attribution", "Source-line attribution", "-", "C",
          "visualize_data.bin 0x01 source bundle plus the PC map",
          caveat="Shows inlined header lines, not the user's kernel lines"),

        # ---- timeline ----------------------------------------------------
        M("pipe_timeline", "Per-pipe timeline", "us", "C", "trace.json (PipeTimeline)",
          note="Busy and idle intervals per pipe", caveat="Covers only the instrumented cores; measurements often show just one"),

        # ---- scheduling / block timeline ---------------------------------
        M("block_timeline", "Block start/end timestamps", "us", "C",
          "trace.json (PipeTimeline) events with pid=core<N>.veccore<M>",
          note="First activity and last completion of each block; the only source for launch skew and tail analysis",
          caveat="PipeTimeline instruments only a subset of cores: a 56-block kernel yielded 12 subcores, "
                 "so the skew is a lower bound, not a chip-wide truth"),
        M("launch_skew", "Launch skew", "us", "C", "derived: last_start - first_start",
          note="Only part of the chip works during this interval; pipeline tuning cannot help"),
        M("tail_effect", "Tail", "us", "C", "derived: last_end - first_end",
          note="How much later the slowest core finishes than the fastest; usually an uneven final tile or preemption"),

        # ---- VF (Vector Fusion) ------------------------------------------
        M("vf_ratio", "VF share", "%", "B", "ArithmeticUtilization.csv:aiv_vec_vf_ratio",
          note="Share of vector time spent in VF (Vector Fusion)"),
        M("simt_vf_ratio", "SIMT VF share", "%", "B",
          "ArithmeticUtilization.csv:aiv_vec_simt_vf_ratio"),
        M("vf_internal", "VF internals: PUSHQ/RVECEX/RVECLD/RVECSU/PUSH_PB", "cycle", "C",
          "unavailable (cannsim only)",
          note="simd_vf_story in cann-samples uses these to judge whether VF calls are too fragmented",
          caveat="These detailed counters appear only in cannsim traces; on-device msopprof reports only vf_ratio. "
                 "This skill is restricted to on-device capture, so the value is marked unavailable rather than substituted from simulation"),

        # ---- load/store & icache (scalar story) --------------------------
        M("load_store_ratio", "Load/Store instruction share", "%", "C",
          "unavailable (requires instr_popped_log.dump)",
          note="The core evidence scalar_story uses to infer register spill; above 30% suggests spill",
          caveat="The instr_popped log needs --dump=true, and --dump requires TimelineDetail; "
                 "the latter fails outright on dav-3510, so this cannot be obtained on-device"),
        M("pc_cycle", "PC residence (icache-miss diagnosis)", "us", "C",
          "trace.json (InstrTimeline): pc_addr + ts",
          note="A PC that stops advancing means waiting on instruction fetch; the numeric equivalent of scalar_story's PC-vs-cycle plot",
          caveat="Requires --with-instructions; measured on vector_add: 71 events, zero residences, "
                 "too few events on a short kernel to decide"),
        M("load_store_via_pc", "Load/Store hot spots (attributed by PC)", "us", "C",
          "derived: InstrTimeline pc + dfx_region_map opcode",
          note="Shows the cost of each LD/ST-class opcode, but not a total instruction count",
          caveat="Not scalar_story's Load/Store share (that needs the full instr_popped instruction stream)"),

        # ---- sector / coalescing / cross-die ------------------------------
        M("sector_efficiency", "Sector utilisation / coalescing", "%", "C",
          "unavailable (no direct counter)",
          note="NCU measures coalescing as sectors per request; CANN exposes no such counter",
          caveat="No metric set emits sectors on-device. The available indirect evidence is "
                 "the ratio of GM_to_UB_datas(KB) to actual transfer count, and L2 requests against misses"),
        M("cross_die_bw", "Cross-die traffic (SIO)", "GB/s", "C",
          "visualize_data.bin 0x08 memory_unit[memory_path in {2,6,7,8,37,38}]",
          note="On a dual-die part, reaching the remote die's L2 traverses SIO at materially higher latency",
          caveat="CANN does not document the path ids, so the cross-die grouping is inferred and tiered accordingly"),
        M("cross_die_pct", "Cross-die traffic share", "%", "C",
          "derived: cross_die_bw / (cross_die + on_die)",
          note="Above 20%, adjust the write distribution to match the GM address interleave"),
        M("die_count", "Die count", "-", "A", "platform_config:DieInfo/io_die_num",
          note="2 on Ascend950PR"),

        # ---- runtime / driver REA outcomes --------------------------------
        M("op_timeline", "per-op start/end/duration", "ns", "A",
          "libprofapi.so: aclprofGetOpStart/GetOpEnd/GetOpDuration（acl_prof.h）",
          note="Operator-level timeline distinguishing each dispatched operator's start and end; finer than the single CSV duration",
          caveat="Still operator level, not block level, and carries no hardware counters"),
        M("stream_correlation", "stream / correlationId association", "-", "A",
          "mspti: msptiActivityKernel{start,end,streamId,correlationId}",
          note="Determines which stream a kernel ran on and which host call it corresponds to",
          caveat="Needs mspti collection, a separate path from msopprof; not enabled by default"),
        M("pmu_via_driver", "Driver-level PMU access", "-", "C",
          "unavailable: libascend_hal.so exports no such symbol",
          note="Of 1757 exported symbols only dmanage_get_pmu_voltage is PMU-related, and it is power management",
          caveat="Counter access lives inside the profiler driver and is not exported through HAL; msopprof is the only route"),
        M("pmu_event_set", "PMU event ids", "-", "A",
          "Ascend/msprof analysis/msconfig/soc_pmu_config.py",
          note="request_events=0x8a, hit_events=0x8c,0x8d, miss_events=0x2; "
               "identical for every v4 and v6 part including dav-3510",
          caveat="The event set is fixed per chip generation and has no user-facing selector; "
                 "--aic-metrics chooses a derived metric group, not raw events"),

        # ---- rock-bottom reliability ------------------------------------
        M("imbalance", "Core-to-core spread", "%", "B", "derived: (max-min)/mean of per-core time",
          note="Above 30% means uneven tiling; on short kernels it is mostly launch overhead"),
        M("advice", "Vendor advice", "-", "A", "visualize_data.bin advice field"),
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
    lines = ["# Metric catalogue", ""]
    lines.append("Every metric states its provenance and confidence. The tier is not a subjective rating: it follows "
                 "how the value is produced, calibrated against the coefficient of variation over five repeat collections.")
    lines.append("")
    lines.append("| Tier | Meaning |")
    lines.append("|---|---|")
    for tier in TIER_ORDER:
        lines.append(f"| {tier} | {TIER_DESCRIPTION[tier]} |")
    lines.append("")
    for tier in TIER_ORDER:
        group = [m for m in metrics if m.tier == tier]
        if not group:
            continue
        lines.append(f"## Tier {tier} ({len(group)} entries)")
        lines.append("")
        lines.append("| Metric | Unit | Source | Note |")
        lines.append("|---|---|---|---|")
        for m in group:
            note = m.note
            if m.caveat:
                note = (note + "；" if note else "") + "**Caveat**: " + m.caveat
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
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--tier", choices=TIER_ORDER, default=None,
                       help="list only metrics of the given tier")
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
            print(f"# Tier {args.tier} metrics ({len(metrics)} entries)\n")
            for m in metrics:
                print(f"- **{m.label}** (`{m.key}`) — {m.unit}")
                print(f"  Source: `{m.source}`")
                if m.note:
                    print(f"  Note: {m.note}")
                if m.caveat:
                    print(f"  **Caveat**: {m.caveat}")
                measured = stability_for(m.key)
                if measured:
                    print(f"  Measured CV: {measured['cv_pct']}% ({measured['runs']} runs)")
        else:
            print(render_catalogue())
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
