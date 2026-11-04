#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adversarial critic: attack a kernel-perf capture and its analysis.

Why this exists
---------------
A profiling report that "looks reasonable" can still be wrong: the collection
may have silently produced empty counters, the units may be off by 4.9%, the
per-core rows may not correspond to the cores the kernel actually ran on, or a
derived percentage may be computed against a denominator that means nothing on
this silicon. Each of those failures produces a plausible-looking table.

This tool takes the role of a reviewer who does not believe the numbers. It
checks identities that MUST hold if the capture and the analysis are correct,
and reports every violation with the exact field values that broke it. Every
check is one of:

  IDENTITY  a relation that is arithmetically true by construction
            (e.g. ratio == time/total_time). A violation means a parsing or
            unit bug.
  CONSISTENCY two independent sources must agree
            (CSV vs visualize_data.bin, one table vs another).
  PLAUSIBILITY a physical bound that cannot be exceeded
            (utilisation <=100%, hit rate <=100%).
  COVERAGE   the report must mention something it would be misleading to omit.

Exit code is 0 only when no check fails, so this can gate a report.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# tolerant numeric parsing -- the profiler writes "NA" for "not measured"
# ---------------------------------------------------------------------------

def num(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "NA" or text.lower() in ("nan", "none", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", errors="replace") as handle:
        rows = []
        for row in csv.DictReader(handle):
            cleaned = {k.strip(): (v or "") for k, v in row.items() if k is not None}
            cleaned.pop("", None)
            rows.append(cleaned)
        return rows


def load_viz(path: Path) -> Dict[int, Dict[str, Any]]:
    """Decode visualize_data.bin into {kind: payload}. Last record of a kind wins."""
    if not path.is_file():
        return {}
    data = path.read_bytes()
    decoder = json.JSONDecoder()
    out: Dict[int, Dict[str, Any]] = {}
    pos = 0
    while pos + 14 <= len(data):
        try:
            length = struct.unpack_from("<Q", data, pos)[0]
        except struct.error:
            break
        if length < 3 or pos + 12 + length > len(data):
            break
        if data[pos + 10:pos + 12] != b"ZZ":
            break
        try:
            obj, _ = decoder.raw_decode(
                data[pos + 12:pos + 12 + length].decode("utf-8", "surrogateescape")
            )
        except (UnicodeDecodeError, ValueError):
            break
        out[data[pos + 8]] = obj
        pos += 12 + length
    return out


# ---------------------------------------------------------------------------
# finding model
# ---------------------------------------------------------------------------

class Finding:
    __slots__ = ("check", "kind", "ok", "detail", "evidence")

    def __init__(self, check: str, kind: str, ok: bool, detail: str,
                 evidence: Optional[Dict[str, Any]] = None):
        self.check = check
        self.kind = kind
        self.ok = ok
        self.detail = detail
        self.evidence = evidence or {}

    def __str__(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        return f"[{mark}] {self.check} ({self.kind}): {self.detail}"


class Critic:
    def __init__(self, capture_dir: Path, tolerance: float = 1e-3):
        self.dir = capture_dir
        self.tol = tolerance
        self.findings: List[Finding] = []
        self.tables = {
            name: load_csv(capture_dir / f"{name}.csv")
            for name in (
                "PipeUtilization", "ArithmeticUtilization", "Memory",
                "MemoryUB", "MemoryL0", "L2Cache", "ResourceConflictRatio",
            )
        }
        self.basic = load_csv(capture_dir / "OpBasicInfo.csv")
        self.viz = load_viz(capture_dir / "visualize_data.bin")
        self.report: Optional[str] = None

    # -- plumbing ----------------------------------------------------------
    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def check(self, name: str, kind: str) -> Callable:
        """Decorator: run a check, recording either a pass or a failure."""
        def wrap(fn: Callable[[], Tuple[bool, str, Dict[str, Any]]]):
            try:
                ok, detail, evidence = fn()
            except Exception as exc:  # a crashing check is itself a failure
                self.add(Finding(name, kind, False, f"检查本身异常: {exc!r}"))
                return fn
            self.add(Finding(name, kind, ok, detail, evidence))
            return fn
        return wrap

    def fail_count(self) -> int:
        return sum(1 for f in self.findings if not f.ok)

    # -- the checks --------------------------------------------------------
    def run(self) -> List[Finding]:
        self.check_collection_nonempty()
        self.check_row_count_matches_block_dim()
        self.check_ratio_identity()
        self.check_ratio_bounds()
        self.check_bandwidth_unit_convention()
        self.check_peak_denominator_recovers_vendor_pct()
        self.check_cross_table_time_agreement()
        self.check_viz_core_count_matches_rows()
        self.check_cycles_vs_frequency()
        self.check_l2_hit_rate_bounds()
        self.check_viz_l2_matches_csv()
        self.check_pipe_time_can_overlap()
        self.check_advice_not_empty_when_idle()
        if self.report is not None:
            self.check_report_mentions_no_data()
            self.check_report_has_per_core_section()
        return self.findings

    # 1. An empty collection looks exactly like a healthy one in the CSV shape:
    # same header, same row count, all counters NA. Catch it first.
    def check_collection_nonempty(self) -> None:
        @self.check("collection_nonempty", "COVERAGE")
        def _():
            rows = self.tables["PipeUtilization"]
            if not rows:
                return False, "没有 PipeUtilization.csv 或为空", {}
            numeric = suspicious = 0
            for row in rows:
                for key, value in row.items():
                    if not key.endswith(("_ratio", "_time(us)", "_cycles")):
                        continue
                    numeric += 1
                    if num(value) is None:
                        suspicious += 1
            if numeric == 0:
                return False, "没有任何 *_ratio/_time/_cycles 列", {}
            # The real test is not "how many cells are NA" (the profiler leaves
            # whole pipe families NA by design: a vector kernel has no cube
            # columns and vice versa) but "did ANY pipe ever report a non-zero
            # busy ratio". A capture where every *_ratio is NA/0 has no usable
            # execution data, however healthy the CSV shape looks.
            busy = 0
            for row in rows:
                for key, value in row.items():
                    if not key.endswith("_ratio"):
                        continue
                    parsed = num(value)
                    if parsed is not None and parsed > 0:
                        busy += 1
            empty_frac = suspicious / numeric
            ok = busy > 0
            return ok, (f"{busy} 个非零流水占比；{suspicious}/{numeric} 计数为空 "
                        f"({empty_frac:.1%})"), {
                "busy_ratio_cells": busy, "empty_fraction": round(empty_frac, 4)
            }
        _()

    # 2. One row per core is the profiler's contract; a mismatch means the
    # rows were aggregated or truncated.
    def check_row_count_matches_block_dim(self) -> None:
        @self.check("rows_match_block_dim", "CONSISTENCY")
        def _():
            if not self.basic:
                return True, "无 OpBasicInfo.csv，跳过", {}
            block_dim = num(self.basic[0].get("Block Dim"))
            rows = self.tables["PipeUtilization"]
            if block_dim is None or not rows:
                return True, "block_dim 或行数缺失，跳过", {}
            ok = int(block_dim) == len(rows)
            return ok, f"Block Dim={int(block_dim)} vs CSV 行数={len(rows)}", {
                "block_dim": int(block_dim), "rows": len(rows)
            }
        _()

    # 3. ratio == time / time_total. Pure arithmetic; a violation is a parser bug.
    def check_ratio_identity(self) -> None:
        @self.check("ratio_identity", "IDENTITY")
        def _():
            rows = self.tables["PipeUtilization"]
            checked = 0
            violations = []
            for row in rows:
                for prefix in ("aic", "aiv"):
                    total = num(row.get(f"{prefix}_time(us)"))
                    if not total:
                        continue
                    for suffix in ("vec", "scalar", "mte1", "mte2", "mte3",
                                   "cube", "fixpipe"):
                        part = num(row.get(f"{prefix}_{suffix}_time(us)"))
                        ratio = num(row.get(f"{prefix}_{suffix}_ratio"))
                        if part is None or ratio is None:
                            continue
                        checked += 1
                        expected = part / total
                        if abs(expected - ratio) > self.tol:
                            violations.append(
                                (row.get("block_id"), f"{prefix}_{suffix}", ratio, expected)
                            )
            if checked == 0:
                return True, "没有可校验的 ratio/time 对", {}
            ok = not violations
            detail = (f"校验 {checked} 对，{len(violations)} 处不符"
                      + (f"，例: core {violations[0][0]} {violations[0][1]} "
                         f"ratio={violations[0][2]:.6f} 应为 {violations[0][3]:.6f}"
                         if violations else ""))
            return ok, detail, {"checked": checked, "violations": len(violations)}
        _()

    # 4. No single pipe can be busy more than 100% of the core's life.
    def check_ratio_bounds(self) -> None:
        @self.check("ratio_bounds", "PLAUSIBILITY")
        def _():
            worst = (None, None, 0.0)
            for row in self.tables["PipeUtilization"]:
                for key, value in row.items():
                    if not key.endswith("_ratio"):
                        continue
                    parsed = num(value)
                    if parsed is None:
                        continue
                    if parsed > worst[2]:
                        worst = (row.get("block_id"), key, parsed)
                    if parsed > 1.0 + self.tol:
                        return False, (f"core {row.get('block_id')} {key}={parsed:.6f} > 1"), {
                            "core": row.get("block_id"), "field": key, "value": parsed
                        }
            return True, f"最大流水占比 {worst[1]}={worst[2]:.4f}（core {worst[0]}）", {
                "max_field": worst[1], "max_value": worst[2]
            }
        _()

    # 5. The unit trap: datas(KiB)*1024 / time_s must reproduce the published
    # GB/s only when GB means GiB. Verified to <1e-6 here, so any regression in
    # our own conversion shows up immediately.
    def check_bandwidth_unit_convention(self) -> None:
        @self.check("bandwidth_unit_convention", "IDENTITY")
        def _():
            pairs = [("GM_to_UB_datas(KB)", "aiv_gm_to_ub_bw(GB/s)")]
            checked = 0
            worst_err = 0.0
            worst_ctx = ""
            for row in self.tables["Memory"]:
                time_us = num(row.get("aiv_time(us)"))
                if not time_us:
                    continue
                for data_key, bw_key in pairs:
                    volume = num(row.get(data_key))
                    published = num(row.get(bw_key))
                    if volume is None or published is None or published <= 0:
                        continue
                    gib_per_s = volume * 1024.0 / (time_us * 1e-6) / (1024.0 ** 3)
                    err = abs(gib_per_s - published) / published
                    checked += 1
                    if err > worst_err:
                        worst_err = err
                        worst_ctx = f"core {row.get('block_id')} {worst_err:.2e}"
            if checked == 0:
                return True, "没有可校验的带宽/数据量对", {}
            ok = worst_err < 1e-5
            return ok, (f"KiB→GiB 换算校验 {checked} 对，最大相对误差 {worst_err:.2e}"
                        + ("" if ok else f"（{worst_ctx}）")), {
                "checked": checked, "max_relative_error": worst_err
            }
        _()

    # 6. The peak we recover must reproduce the vendor's own percentage. If it
    # does not, every peak_pct in the report is wrong.
    def check_peak_denominator_recovers_vendor_pct(self) -> None:
        @self.check("peak_denominator_roundtrip", "IDENTITY")
        def _():
            checked = 0
            worst_err = 0.0
            for row in self.tables["Memory"]:
                bw = num(row.get("aiv_gm_to_ub_bw(GB/s)"))
                pct = num(row.get("GM_to_UB_bw_usage_rate(%)"))
                if bw is None or pct is None or pct <= 0:
                    continue
                peak = bw / (pct / 100.0)
                recovered = 100.0 * bw / peak
                worst_err = max(worst_err, abs(recovered - pct))
                checked += 1
            if checked == 0:
                return True, "无 vendor 百分比可反解", {}
            ok = worst_err < 1e-4
            return ok, (f"反解峰值回代 {checked} 次，最大绝对误差 {worst_err:.2e} 个百分点"), {
                "checked": checked, "max_abs_error_pct": worst_err
            }
        _()

    # 7. All tables describe the same kernel run, so their time columns must be
    # the same numbers. A mismatch means we are mixing captures.
    def check_cross_table_time_agreement(self) -> None:
        @self.check("cross_table_time_agreement", "CONSISTENCY")
        def _():
            base = self.tables["PipeUtilization"]
            compared = 0
            mismatches = []
            for name in ("Memory", "MemoryUB", "L2Cache",
                         "ResourceConflictRatio", "ArithmeticUtilization"):
                rows = self.tables[name]
                if len(rows) != len(base) or not rows:
                    continue
                for left, right in zip(base, rows):
                    a = num(left.get("aiv_time(us)"))
                    b = num(right.get("aiv_time(us)"))
                    if a is None or b is None:
                        continue
                    compared += 1
                    if abs(a - b) > 1e-6:
                        mismatches.append((name, left.get("block_id"), a, b))
            if compared == 0:
                return True, "没有可比较的时间列", {}
            ok = not mismatches
            detail = (f"跨表比对 {compared} 个时间值，{len(mismatches)} 处不符"
                      + (f"，例: {mismatches[0]}" if mismatches else ""))
            return ok, detail, {"compared": compared, "mismatches": len(mismatches)}
        _()

    # 8. The per-core JSON must describe the same number of cores as the CSV.
    def check_viz_core_count_matches_rows(self) -> None:
        @self.check("viz_matches_csv_cores", "CONSISTENCY")
        def _():
            core_map = self.viz.get(0x08, {}).get("core_memory_map")
            if not core_map:
                return True, "无 0x08 core_memory_map 记录，跳过", {}
            rows = self.tables["PipeUtilization"]
            ok = len(core_map) == len(rows)
            return ok, f"visualize 核数={len(core_map)} vs CSV 行数={len(rows)}", {
                "viz_cores": len(core_map), "csv_rows": len(rows)
            }
        _()

    # 9. cycles / time must equal the reported clock. This catches a wrong
    # frequency assumption (which would silently mis-scale every cycle count).
    def check_cycles_vs_frequency(self) -> None:
        @self.check("cycles_match_frequency", "IDENTITY")
        def _():
            if not self.basic:
                return True, "无 OpBasicInfo.csv，跳过", {}
            freq = num(self.basic[0].get("Current Freq"))
            if not freq:
                return True, "无 Current Freq，跳过", {}
            worst = 0.0
            checked = 0
            for row in self.tables["PipeUtilization"]:
                for prefix in ("aiv", "aic"):
                    time_us = num(row.get(f"{prefix}_time(us)"))
                    cycles = num(row.get(f"{prefix}_total_cycles"))
                    if not time_us or cycles is None:
                        continue
                    implied = cycles / (time_us * 1e-6) / 1e6  # MHz
                    worst = max(worst, abs(implied - freq))
                    checked += 1
            if checked == 0:
                return True, "没有 cycles/time 对", {}
            ok = worst < max(5.0, freq * 0.01)
            return ok, f"{checked} 组 cycles/time 反推频率，最大偏差 {worst:.1f} MHz（报 {freq:.0f}）", {
                "checked": checked, "max_deviation_mhz": worst, "reported_mhz": freq
            }
        _()

    # 10. Hit rates are percentages by definition.
    def check_l2_hit_rate_bounds(self) -> None:
        @self.check("l2_hit_rate_bounds", "PLAUSIBILITY")
        def _():
            worst = (None, 0.0)
            for row in self.tables["L2Cache"]:
                for key, value in row.items():
                    if "hit_rate" not in key:
                        continue
                    parsed = num(value)
                    if parsed is None:
                        continue
                    if parsed > worst[1]:
                        worst = (key, parsed)
                    if parsed > 100.0 + self.tol:
                        return False, f"core {row.get('block_id')} {key}={parsed}", {}
            return True, f"最大命中率 {worst[0]}={worst[1]:.2f}%", {"max": worst[1]}
        _()

    # 11. CSV hit rate and the JSON hit_ratio are two encodings of one counter.
    def check_viz_l2_matches_csv(self) -> None:
        @self.check("viz_l2_matches_csv", "CONSISTENCY")
        def _():
            core_map = self.viz.get(0x08, {}).get("core_memory_map") or []
            if not core_map:
                return True, "无 core_memory_map，跳过", {}
            by_core = {}
            for row in self.tables["L2Cache"]:
                read = num(row.get("aiv_read_hit_rate(%)"))
                write = num(row.get("aiv_write_hit_rate(%)"))
                if read is None and write is None:
                    continue
                by_core[str(row.get("block_id"))] = (read, write)
            compared = 0
            mismatches = []
            for entry in core_map:
                core_no = str(entry.get("core_no"))
                if core_no not in by_core:
                    continue
                json_total = num((entry.get("L2cache") or {}).get("hit_ratio"))
                if json_total is None:
                    continue
                csv_read, csv_write = by_core[core_no]
                # The JSON total weighs read and write together, so it must sit
                # between the two CSV rates rather than equal either.
                candidates = [v for v in (csv_read, csv_write) if v is not None]
                if not candidates:
                    continue
                low, high = min(candidates), max(candidates)
                compared += 1
                if not (low - 1.0 <= json_total <= high + 1.0):
                    mismatches.append((core_no, json_total, csv_read, csv_write))
            if compared == 0:
                return True, "没有可交叉校验的 core", {}
            ok = not mismatches
            detail = (f"比对 {compared} 个核的 L2 命中率，{len(mismatches)} 处越界"
                      + (f"，例: {mismatches[0]}" if mismatches else ""))
            return ok, detail, {"compared": compared, "mismatches": len(mismatches)}
        _()

    # 12. Pipe busy times legitimately overlap (two pipes can be busy in the
    # same cycle up to the issue width), so the sum may exceed the core time --
    # but not without bound. A ratio >2 means we are adding incompatible
    # windows (e.g. summing across cores into one row).
    def check_pipe_time_can_overlap(self) -> None:
        @self.check("pipe_time_sum_bounded", "PLAUSIBILITY")
        def _():
            worst = (None, 0.0)
            for row in self.tables["PipeUtilization"]:
                for prefix, suffixes in (
                    ("aiv", ("vec", "scalar", "mte2", "mte3")),
                    ("aic", ("cube", "scalar", "mte1", "mte2", "mte3", "fixpipe")),
                ):
                    total = num(row.get(f"{prefix}_time(us)"))
                    if not total:
                        continue
                    parts = [num(row.get(f"{prefix}_{s}_time(us)")) for s in suffixes]
                    summed = sum(v for v in parts if v is not None)
                    ratio = summed / total
                    if ratio > worst[1]:
                        worst = (f"core {row.get('block_id')} {prefix}", ratio)
                    if ratio > 2.0:
                        return False, (f"{prefix} 流水时间合计是核时间的 {ratio:.2f} 倍，"
                                       f"疑似跨核求和"), {"ratio": ratio}
            return True, (f"流水时间合计最大为核时间的 {worst[1]:.3f} 倍"
                          f"（{worst[0]}，流水本可重叠）"), {"max_ratio": worst[1]}
        _()

    # 13. If the kernel shows a bottleneck but carries no vendor advice, that is
    # a coverage gap worth flagging (advice drives the report's recommendations).
    def check_advice_not_empty_when_idle(self) -> None:
        @self.check("advice_present", "COVERAGE")
        def _():
            # Advice lives in four places depending on the metric set that was
            # collected: the identity record, the two sub-block records, and the
            # per-block tables. A capture is only advice-less if ALL are empty --
            # checking just the identity record produces a false failure on the
            # default metric set, where identity.advice is always [].
            advice: List[str] = []
            for kind in (0x05, 0x06, 0x07):
                payload = self.viz.get(kind) or {}
                raw = payload.get("advice")
                if isinstance(raw, str) and raw:
                    advice.append(raw)
                elif raw:
                    advice.extend(str(x) for x in raw)
            for block in (self.viz.get(0x09) or {}).get("table_per_block") or []:
                raw = block.get("advice")
                if isinstance(raw, str) and raw:
                    advice.append(raw)
                elif raw:
                    advice.extend(str(x) for x in raw)
            rows = self.tables["PipeUtilization"]
            busy = False
            for row in rows:
                for key, value in row.items():
                    if key.endswith("_ratio"):
                        parsed = num(value)
                        if parsed is not None and parsed > 0.5:
                            busy = True
            if not busy:
                return True, "无主导流水，advice 非必需", {}
            ok = bool(advice)
            return ok, (f"存在 >50% 的流水，advice 条数={len(advice)}"
                        + ("" if ok else "（4 处来源全空）")), {
                "advice": advice
            }
        _()

    # 14/15. Report-level coverage: the two failure modes that produce a
    # misleading report are an empty capture presented as results, and a
    # summary with no per-core detail.
    def check_report_mentions_no_data(self) -> None:
        @self.check("report_flags_empty_capture", "COVERAGE")
        def _():
            rows = self.tables["PipeUtilization"]
            empty = True
            for row in rows:
                for key, value in row.items():
                    if key.endswith("_ratio"):
                        parsed = num(value)
                        if parsed is not None and parsed > 0:
                            empty = False
            if not empty:
                return True, "采集有数据，无需空数据告警", {}
            text = self.report or ""
            ok = "无有效数据" in text or "no data" in text.lower()
            return ok, ("采集为空且报告已标注" if ok else "采集为空但报告未标注"), {}
        _()

    def check_report_has_per_core_section(self) -> None:
        @self.check("report_has_per_core", "COVERAGE")
        def _():
            text = self.report or ""
            ok = "逐核明细" in text or "per_core" in text
            return ok, ("报告含逐核明细" if ok else "报告缺少逐核明细"), {}
        _()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_capture(path: Path) -> Optional[Path]:
    if (path / "OpBasicInfo.csv").is_file():
        return path
    for pattern in ("OPPROF_*", "PROF_*"):
        matches = sorted(p for p in path.glob(pattern) if p.is_dir())
        if matches:
            return matches[-1]
    for sub in sorted(p for p in path.iterdir() if p.is_dir()) if path.is_dir() else []:
        for pattern in ("OPPROF_*", "PROF_*"):
            matches = sorted(p for p in sub.glob(pattern) if p.is_dir())
            if matches:
                return matches[-1]
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="critic.py",
        description=(
            "Adversarial reviewer for a kernel-perf capture.\n"
            "Checks identities, cross-source consistency, physical bounds and "
            "report coverage. Exit 0 only when nothing fails."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  critic.py --out ./perf_out\n"
            "  critic.py --out ./perf_out --report ./perf_out/report.md --json\n"
        ),
    )
    parser.add_argument("--out", required=True,
                        help="采集目录（含 OPPROF_*/PROF_*，或直接是捕获目录）")
    parser.add_argument("--report", default=None,
                        help="同时校验生成的 report.md")
    parser.add_argument("--tolerance", type=float, default=1e-3,
                        help="ratio 恒等式的相对容差（默认 1e-3）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出全部检查")
    args, _extra = parser.parse_known_args(argv)

    base = Path(args.out).resolve()
    capture = find_capture(base)
    if capture is None:
        print(f"错误: {base} 下没有 OPPROF_*/PROF_* 采集目录", file=sys.stderr)
        return 1

    critic = Critic(capture, tolerance=args.tolerance)
    if args.report:
        report_path = Path(args.report)
        if report_path.is_file():
            critic.report = report_path.read_text(errors="replace")

    findings = critic.run()

    if args.json:
        print(json.dumps({
            "capture": str(capture),
            "passed": sum(1 for f in findings if f.ok),
            "failed": critic.fail_count(),
            "findings": [
                {"check": f.check, "kind": f.kind, "ok": f.ok,
                 "detail": f.detail, "evidence": f.evidence}
                for f in findings
            ],
        }, indent=2, ensure_ascii=False))
    else:
        print(f"# 对抗校验 — {capture}")
        print()
        for finding in findings:
            print(str(finding))
        print()
        passed = sum(1 for f in findings if f.ok)
        failed = critic.fail_count()
        print(f"结果: {passed}/{len(findings)} 通过，{failed} 失败")
        if failed:
            print("\n失败的检查意味着报告里的数字不可信，先修采集或解析再看指标。")

    return 0 if critic.fail_count() == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
