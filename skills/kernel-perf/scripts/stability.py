#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repeatability harness: how much does each metric move between identical runs?

Why this is a first-class tool and not a test
---------------------------------------------
"Accurate" is not a property of a single capture. A number is only usable if
repeating the measurement reproduces it, and the amount of movement differs by
orders of magnitude between metrics: pipe occupancy repeats to ~1% while the L2
write hit rate swings by 34%.

The catalogue assigns confidence tiers partly from these measurements, so this
script is what keeps the tiers honest rather than asserted. Re-run it after a
CANN upgrade, a firmware change or a different SoC, and update
``metric_catalogue.MEASURED_STABILITY`` with the result.

Output: a table of mean / stdev / CV / range per metric, plus JSON for machine
consumption, plus the stability section of the report.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kernel_perf as KP  # noqa: E402

# Metrics worth tracking: one representative per behaviour class (identity,
# pipe, bandwidth, cache, stall). Keyed by (table, field) and labelled.
TRACKED = [
    ("OpBasicInfo", "Task Duration(us)", "duration_us", "耗时"),
    ("PipeUtilization", "aiv_time(us)", "aiv_time", "核忙时间"),
    ("PipeUtilization", "aiv_mte2_ratio", "mte2_ratio", "MTE2 占用"),
    ("PipeUtilization", "aiv_vec_ratio", "vec_ratio", "Vector 占用"),
    ("PipeUtilization", "aiv_scalar_ratio", "scalar_ratio", "Scalar 占用"),
    ("Memory", "aiv_gm_to_ub_bw(GB/s)", "gm_to_ub_gbps", "GM→UB 带宽"),
    ("L2Cache", "aiv_read_hit_rate(%)", "l2_read_hit", "L2 读命中"),
    ("L2Cache", "aiv_write_hit_rate(%)", "l2_write_hit", "L2 写命中"),
    ("ResourceConflictRatio", "aiv_vec_wait_ratio", "vec_wait_ratio", "Vector 等待"),
]


@dataclass
class MetricStability:
    key: str
    label: str
    runs: int = 0
    mean: Optional[float] = None
    stdev: Optional[float] = None
    cv_pct: Optional[float] = None
    range_pct: Optional[float] = None
    values: List[float] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.cv_pct is None:
            return "无数据"
        if self.cv_pct < 3:
            return "稳定"
        if self.cv_pct < 10:
            return "可用"
        return "不可单独引用"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "runs": self.runs,
            "mean": self.mean, "stdev": self.stdev, "cv_pct": self.cv_pct,
            "range_pct": self.range_pct, "verdict": self.verdict,
            "values": [round(v, 6) for v in self.values],
        }


def _column_mean(capture: KP.Capture, table: str, field_name: str) -> Optional[float]:
    """Mean of a field across cores, or the scalar identity value.

    OpBasicInfo is not stored in the per-core table dict -- load_capture parses
    it into dedicated Capture fields -- so it must be read from there, not via
    column(), which would silently return None.
    """
    if table == "OpBasicInfo":
        if field_name.startswith("Task Duration"):
            return capture.duration_us
        if field_name == "Current Freq":
            return capture.cur_freq
        return None
    values = [v for v in capture.column(table, field_name) if v is not None]
    return sum(values) / len(values) if values else None


def measure(captures: List[KP.Capture]) -> List[MetricStability]:
    """Compute stability statistics for every tracked metric."""
    out: List[MetricStability] = []
    for table, field_name, key, label in TRACKED:
        entry = MetricStability(key=key, label=label)
        for capture in captures:
            value = _column_mean(capture, table, field_name)
            if value is not None:
                entry.values.append(value)
        entry.runs = len(entry.values)
        if entry.runs >= 2:
            entry.mean = statistics.mean(entry.values)
            entry.stdev = statistics.stdev(entry.values)
            if entry.mean:
                entry.cv_pct = 100.0 * entry.stdev / entry.mean
                entry.range_pct = 100.0 * (max(entry.values) - min(entry.values)) / entry.mean
        elif entry.runs == 1:
            entry.mean = entry.values[0]
        out.append(entry)
    return out


def render(results: List[MetricStability], runs: int) -> str:
    lines = [f"# 稳定性测量（{runs} 次重复采集）", ""]
    lines.append("| 指标 | 均值 | 标准差 | CV% | 极差% | 判读 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for entry in results:
        if entry.cv_pct is None:
            lines.append(f"| {entry.label} | {entry.mean if entry.mean is not None else 'n/a'} "
                         f"| n/a | n/a | n/a | {entry.verdict} |")
            continue
        lines.append(
            f"| {entry.label} | {entry.mean:.4f} | {entry.stdev:.4f} | "
            f"{entry.cv_pct:.2f} | {entry.range_pct:.2f} | {entry.verdict} |"
        )
    lines.append("")
    unstable = [e for e in results if e.cv_pct is not None and e.cv_pct >= 10]
    if unstable:
        lines.append("**CV 超过 10% 的指标**（不应单独引用，只能看数量级）：")
        lines.append("")
        for entry in unstable:
            lines.append(f"- {entry.label}：CV {entry.cv_pct:.1f}%，"
                         f"极差 {entry.range_pct:.1f}%")
        lines.append("")
    return "\n".join(lines)


def run_stability(args: argparse.Namespace) -> int:
    home = KP.find_cann_home()
    if home is None:
        print("错误: 找不到 CANN 安装", file=sys.stderr)
        return 1

    out_root = Path(args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    app = [str(Path(args.app).resolve())] + list(args.app_args or [])

    captures: List[KP.Capture] = []
    for index in range(1, args.runs + 1):
        target = out_root / f"run{index}"
        print(f"[info] 第 {index}/{args.runs} 次采集…", file=sys.stderr)
        rc, log, root = KP.run_msopprof(
            home=home, app=app, out_dir=target, metrics=KP.DEFAULT_METRICS,
            warmup=args.warmup, launch_count=args.launch_count,
            kernel=args.kernel, replay_mode="kernel", timeout=args.timeout,
        )
        (target / "collect.log").write_text(log, errors="replace")
        if root is None:
            print(f"[warn] 第 {index} 次采集为空，计入样本外", file=sys.stderr)
            continue
        KP.preserve_probe_artifacts(root)
        captures.append(KP.load_capture(root, home))

    if len(captures) < 2:
        print(f"错误: 只有 {len(captures)} 次有效采集，无法计算抖动", file=sys.stderr)
        return 1

    results = measure(captures)
    report = render(results, len(captures))
    (out_root / "stability.md").write_text(report)
    payload = {"runs": len(captures), "metrics": [r.as_dict() for r in results]}
    (out_root / "stability.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False))

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(report)
    print(f"\n[info] 结果: {out_root / 'stability.md'}", file=sys.stderr)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stability.py",
        description=(
            "Repeat the same collection N times and report how much each metric\n"
            "moves. Use the result to decide which numbers can be compared\n"
            "between optimisations and which cannot."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  stability.py --app ./vector_add --kernel add_kernel --runs 5\n"
            "  stability.py --app './mm 1 1 1' --runs 3 --warmup 10\n"
        ),
    )
    parser.add_argument("--app", required=True, help="可执行文件或完整命令行")
    parser.add_argument("--kernel", default=None, help="kernel 名（子串匹配）")
    parser.add_argument("--runs", type=int, default=5, help="重复采集次数（默认 5）")
    parser.add_argument("--warmup", type=int, default=5, help="每次采集的预热次数")
    parser.add_argument("--launch-count", type=int, default=3,
                        help="每次采集内部的 launch 轮数")
    parser.add_argument("--out", default="./kernel_perf_stability", help="输出目录")
    parser.add_argument("--timeout", type=int, default=1800, help="单次采集超时")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args, extra = parser.parse_known_args(argv)
    args.app_args = extra
    return run_stability(args)


if __name__ == "__main__":
    sys.exit(main())
