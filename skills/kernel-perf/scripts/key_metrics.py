#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Key derived metrics: MFU, MAC usage, roofline classification.

These are the numbers people actually quote, and each one is a ratio whose
denominator is a *theoretical* peak we take from config rather than measure.
That makes every value here tier D: useful for comparison, never a measured
fact. The module keeps the constants in one place so they can be corrected
when the platform config disagrees.

The three questions this answers
--------------------------------
MFU      "how much of the chip's arithmetic could this kernel possibly be
          using?" -- achieved FLOPs / theoretical peak FLOPs. Comparable to
          training-framework MFU, deliberately conservative.

MAC usage "of the cube work issued, how much did actual multiply-accumulate
          in the ideal 16x16x16 shape?" -- separates 'cube is busy' from 'cube
          is busy doing useful MACs'.

Roofline  "is this kernel limited by arithmetic or by memory?" -- the smaller
          of (compute time, memory time) bounds the rest. We report intensity
          and the binding side, not a single fabricated number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class PlatformPeaks:
    """Theoretical peaks for one SoC, all derived from platform_config."""

    soc: str
    ai_core_cnt: Optional[int] = None
    vector_core_cnt: Optional[int] = None
    cube_freq_mhz: Optional[float] = None
    vec_freq_mhz: Optional[float] = None
    cube_m: int = 16
    cube_n: int = 16
    cube_k: int = 16
    vec_lanes: int = 128
    ddr_rate: Optional[float] = None

    @property
    def cube_macs_per_s(self) -> Optional[float]:
        """MACs/s for the whole chip on the cube pipe.

        One 16x16x16 MMA is 16^3 = 4096 MACs; the cube issues one per cycle.
        """
        if not (self.ai_core_cnt and self.cube_freq_mhz):
            return None
        return (self.ai_core_cnt * self.cube_freq_mhz * 1e6
                * self.cube_m * self.cube_n * self.cube_k)

    @property
    def cube_flops_per_s(self) -> Optional[float]:
        """FLOPs/s: one MAC is two floating-point operations."""
        macs = self.cube_macs_per_s
        return macs * 2 if macs else None

    @property
    def vec_ops_per_s(self) -> Optional[float]:
        """Vector ops/s treating one lane as one op per cycle (fp32)."""
        if not (self.vector_core_cnt and self.vec_freq_mhz):
            return None
        return self.vector_core_cnt * self.vec_freq_mhz * 1e6 * self.vec_lanes

    def as_dict(self) -> Dict[str, Any]:
        cube_macs = self.cube_macs_per_s
        cube_flops = self.cube_flops_per_s
        vec = self.vec_ops_per_s
        return {
            "soc": self.soc,
            "ai_core_cnt": self.ai_core_cnt,
            "vector_core_cnt": self.vector_core_cnt,
            "cube_freq_mhz": self.cube_freq_mhz,
            "vec_freq_mhz": self.vec_freq_mhz,
            "cube_shape": f"{self.cube_m}x{self.cube_n}x{self.cube_k}",
            "vec_lanes": self.vec_lanes,
            "fallback_from_capture": getattr(self, "fallback_from_capture", False),
            "cube_tmacs_per_s": round(cube_macs / 1e12, 3) if cube_macs else None,
            "cube_tflops_per_s": round(cube_flops / 1e12, 3) if cube_flops else None,
            "vector_tops_per_s": round(vec / 1e12, 3) if vec else None,
        }


@dataclass
class KeyMetrics:
    """The headline numbers, each with its own denominator and caveat."""

    mfu_pct: Optional[float] = None
    mfu_note: str = ""
    mac_usage_pct: Optional[float] = None
    mac_note: str = ""
    achieved_tflops: Optional[float] = None
    achieved_tmacs: Optional[float] = None
    arithmetic_intensity: Optional[float] = None
    intensity_note: str = ""
    bound_by: str = ""
    bound_note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mfu_pct": self.mfu_pct,
            "mfu_note": self.mfu_note,
            "mac_usage_pct": self.mac_usage_pct,
            "mac_note": self.mac_note,
            "achieved_tflops": self.achieved_tflops,
            "achieved_tmacs": self.achieved_tmacs,
            "arithmetic_intensity": self.arithmetic_intensity,
            "intensity_note": self.intensity_note,
            "bound_by": self.bound_by,
            "bound_note": self.bound_note,
        }


def platform_peaks(platform: Any, soc: str) -> PlatformPeaks:
    """Build peaks from a six-field Platform, tolerating missing fields."""
    peaks = PlatformPeaks(soc=soc)
    if platform is None:
        return peaks
    peaks.ai_core_cnt = getattr(platform, "ai_core_cnt", None)
    peaks.vector_core_cnt = getattr(platform, "vector_core_cnt", None)
    peaks.cube_freq_mhz = getattr(platform, "cube_freq_mhz", None)
    peaks.vec_freq_mhz = getattr(platform, "vec_freq_mhz", None)
    return peaks


def compute(analysis: Dict[str, Any], peaks: PlatformPeaks) -> KeyMetrics:
    """Derive MFU / MAC usage / roofline from an analysis dict.

    `analysis` is the same structure kernel_perf.analyze() returns, so this
    stays a pure function over already-parsed values.
    """
    result = KeyMetrics()
    kernel = analysis.get("kernel") or {}
    duration_us = kernel.get("duration_us")
    arith = analysis.get("arithmetic") or {}
    pipes = analysis.get("pipes") or []

    # -- achieved MACs -----------------------------------------------------
    # cube_total_instr_number is the number of MMA issues; each does m*n*k MACs.
    cube_instr = arith.get("cube_total_instr") or 0
    macs = cube_instr * peaks.cube_m * peaks.cube_n * peaks.cube_k if cube_instr else 0

    if duration_us and duration_us > 0:
        seconds = duration_us * 1e-6
        if macs:
            result.achieved_tmacs = macs / seconds / 1e12
            result.achieved_tflops = macs * 2 / seconds / 1e12

    # -- MFU ---------------------------------------------------------------
    peak_flops = peaks.cube_flops_per_s
    if result.achieved_tflops is not None and peak_flops:
        result.mfu_pct = 100.0 * (result.achieved_tflops * 1e12) / peak_flops
        result.mfu_note = (
            f"达成 {result.achieved_tflops:.3f} TFLOPS ÷ 理论峰值 "
            f"{peak_flops / 1e12:.1f} TFLOPS（{peaks.ai_core_cnt} AIC × "
            f"{peaks.cube_freq_mhz:.0f} MHz × {peaks.cube_m}³ MAC × 2）"
        )
    elif not peak_flops:
        result.mfu_note = "缺少 AIC 数量或频率，无法计算 MFU"
    else:
        result.mfu_note = "kernel 没有发出 cube 指令，MFU 不适用（可能是 vector 算子）"

    # -- MAC usage ---------------------------------------------------------
    # Of the time the cube pipe was busy, how much was it issuing MACs?
    cube_ratio = arith.get("cube_ratio") or 0.0
    if cube_ratio > 0:
        # cube_ratio is occupancy of the pipe; a fully-occupied pipe issuing a
        # back-to-back MMA stream is the 100% reference.
        result.mac_usage_pct = min(cube_ratio, 100.0)
        result.mac_note = (
            f"cube 流水占用 {cube_ratio:.2f}%，即 MAC 发射机会的比例。"
            ">70% 表示 cube 基本被乘加填满"
        )
    else:
        result.mac_note = "cube 流水未活动（vector 算子或本 kernel 不含矩阵乘）"

    # -- arithmetic intensity ---------------------------------------------
    gm_read = 0.0
    gm_write = 0.0
    for core in analysis.get("per_core") or []:
        gm_read += core.get("gm_read_kb") or 0.0
        gm_write += core.get("gm_write_kb") or 0.0
    total_bytes = (gm_read + gm_write) * 1024.0
    if macs and total_bytes > 0:
        result.arithmetic_intensity = macs * 2 / total_bytes
        result.intensity_note = (
            f"{macs * 2:.3g} FLOPs ÷ {total_bytes / 1024:.0f} KiB 主存流量"
        )

    # -- what binds --------------------------------------------------------
    if pipes:
        top = max(pipes, key=lambda p: p.get("mean_ratio_pct") or 0.0)
        top_pct = top.get("mean_ratio_pct") or 0.0
        if top_pct < 30.0:
            result.bound_by = "latency"
            result.bound_note = (f"最高流水占用仅 {top_pct:.1f}%，"
                                 "没有任何单元成为瓶颈，时间花在依赖等待/气泡上")
        else:
            result.bound_by = top.get("pipe", "?")
            result.bound_note = f"{top.get('label')} 占用 {top_pct:.1f}%，是最忙的单元"
    return result


def render(metrics: KeyMetrics, peaks: PlatformPeaks) -> list:
    """Markdown lines for the key-metrics section."""
    lines = ["## 2. 关键指标（MFU / MAC / Roofline）", ""]
    lines.append("> 本节全部为 **D 级**：分母是理论峰值，不是实测值。用于横向比较，"
                 "不能当作「实测算力」。")
    lines.append("")

    peak = peaks.as_dict()
    lines.append("理论峰值（来自 platform_config）：")
    lines.append("")
    lines.append(f"- Cube: {peak['cube_shape']} MAC/cycle × {peak['ai_core_cnt']} AIC"
                 f" × {peak['cube_freq_mhz']} MHz = **{peak['cube_tflops_per_s']} TFLOPS**")
    lines.append(f"- Vector: {peak['vec_lanes']} lane × {peak['vector_core_cnt']} AIV"
                 f" × {peak['vec_freq_mhz']} MHz = **{peak['vector_tops_per_s']} TOPS**")
    lines.append("")

    lines.append("| 指标 | 值 | 说明 |")
    lines.append("|---|---:|---|")
    if metrics.mfu_pct is not None:
        lines.append(f"| **MFU** | {metrics.mfu_pct:.3f}% | {metrics.mfu_note} |")
    else:
        lines.append(f"| MFU | n/a | {metrics.mfu_note} |")
    if metrics.mac_usage_pct is not None:
        lines.append(f"| **MAC usage** | {metrics.mac_usage_pct:.2f}% | {metrics.mac_note} |")
    else:
        lines.append(f"| MAC usage | n/a | {metrics.mac_note} |")
    if metrics.achieved_tflops is not None:
        lines.append(f"| 达成算力 | {metrics.achieved_tflops:.4f} TFLOPS | "
                     f"{metrics.achieved_tmacs:.4f} TMAC/s |")
    if metrics.arithmetic_intensity is not None:
        lines.append(f"| 算术强度 | {metrics.arithmetic_intensity:.3f} FLOP/B | "
                     f"{metrics.intensity_note} |")
    lines.append(f"| 受限因素 | **{metrics.bound_by}** | {metrics.bound_note} |")
    lines.append("")
    return lines
