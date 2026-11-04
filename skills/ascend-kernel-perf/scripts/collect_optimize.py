#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adversarial configuration search: maximize how much true data we get.

The problem this solves
-----------------------
`msopprof` has no single invocation that collects everything. Some metric sets
are only valid alone, some silently produce an all-NA capture when combined,
`TimelineDetail` fails outright on dav-3510, and `--replay-mode` changes which
counters are even populated. Which combination actually yields the most
*correct* data is not documented and differs per kernel type.

So we search it. Each candidate configuration is a point in a discrete space
(metric set x replay mode x launch count x warm-up). We run it, then score the
resulting capture with an adversarial critic that refuses to reward volume:
a capture full of NA is worthless, and a capture that violates an identity is
worse than useless. The reward is therefore

    reward = (passed checks) - 3*(failed checks) + coverage - empty_penalty

where coverage counts how many distinct concrete fields we obtained. The weights
make a failed identity dominate any amount of extra coverage, so the search
cannot buy breadth with wrongness. This is the RL loop: the critic is the
reward model, the configuration space is the action space, and the search keeps
the highest-reward capture.

Usage
-----
    python3 collect_optimize.py --app ./bench --kernel my_kernel --out ./opt
    python3 collect_optimize.py --app ./bench --kernel k --out ./opt --quick
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kernel_perf as KP  # noqa: E402


# ---------------------------------------------------------------------------
# the action space
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """One candidate collection configuration."""

    name: str
    metrics: List[str]
    replay_mode: str = "kernel"
    launch_count: int = 1
    warmup: int = 3
    extra_flags: List[str] = field(default_factory=list)

    def describe(self) -> str:
        return (f"{self.name}: metrics={','.join(self.metrics)} "
                f"replay={self.replay_mode} launch={self.launch_count} "
                f"warmup={self.warmup}")


# The seven "core" groups are the ones the vendor HTML report itself is built
# from; they are known to coexist (see references/dav3510-notes.md).
CORE_SEVEN = [
    "PipeUtilization", "ArithmeticUtilization", "Memory", "MemoryUB",
    "MemoryL0", "L2Cache", "ResourceConflictRatio",
]


def candidate_configs(quick: bool = False) -> List[Config]:
    """Ordered by expected value: the likeliest winner is tried first.

    `quick` keeps the three most valuable candidates so a full search still
    fits in a few minutes of device time.
    """
    configs = [
        # The vendor's own default set. Baseline: if this loses, something is
        # wrong with the environment, not with the search.
        Config("core7", CORE_SEVEN, "kernel", 1, 3),
        # Application replay re-runs the whole program per pass. Slower, but it
        # is the documented workaround when kernel replay collects nothing.
        Config("core7-appreplay", CORE_SEVEN, "application", 1, 3),
        # Repeated launches average out DVFS jitter; the profiler reports the
        # mean, which is what "accurate" means for a steady-state kernel.
        Config("core7-launch5", CORE_SEVEN, "kernel", 5, 5),
        # Roofline adds the vendor's own verdict record (0x0D), which is the
        # only source for "latency bound / pipeline caused" style attribution.
        Config("core7+roofline", CORE_SEVEN + ["Roofline"], "kernel", 1, 3),
        # Occupancy adds per-core throughput detail (record 0x0C) that the CSV
        # does not carry.
        Config("core7+occupancy", CORE_SEVEN + ["Occupancy"], "kernel", 1, 3),
        # BasicInfo is cheap and gives a pipeline timeline (trace.json).
        Config("core7+timeline", CORE_SEVEN + ["BasicInfo"], "kernel", 1, 3),
    ]
    if quick:
        return configs[:3] + [configs[3]]
    return configs


# ---------------------------------------------------------------------------
# scoring -- the reward model
# ---------------------------------------------------------------------------

def coverage_fields(capture: KP.Capture) -> int:
    """How many distinct concrete measurements did we actually obtain?

    Counts non-NA numeric cells across every CSV plus the JSON-only fields, so a
    configuration that adds roofline/occupancy/advice is rewarded and one that
    merely repeats the same numbers is not.
    """
    count = 0
    for table in capture.tables.values():
        for row in table:
            for key, value in row.items():
                if key in ("block_id", "sub_block_id"):
                    continue
                if KP._num(value) is not None:
                    count += 1
    # JSON-only data the CSVs never contain.
    count += len(capture.viz_cores) * 3           # per-core L2 + cycles
    if capture.roofline_vendor:
        count += 10                               # vendor roofline verdict
    count += len(capture.advice)                  # actionable advice strings
    count += len(capture.viz_blocks) * 4          # per-block detail tables
    if capture.timeline:
        count += 20                               # per-pipe timeline
    return count


def score_capture(capture: KP.Capture, critic_script: Path,
                  report_path: Optional[Path]) -> Dict[str, Any]:
    """Run the adversarial critic and turn its verdict into a scalar reward."""
    cmd = [sys.executable, str(critic_script), "--out", str(capture.root), "--json"]
    if report_path is not None and report_path.is_file():
        cmd.extend(["--report", str(report_path)])
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
        payload = json.loads(proc.stdout.decode(errors="replace") or "{}")
    except (subprocess.SubprocessError, ValueError) as exc:
        return {"reward": float("-inf"), "error": f"critic failed: {exc!r}",
                "passed": 0, "failed": 99, "coverage": 0}

    passed = int(payload.get("passed", 0))
    failed = int(payload.get("failed", 0))
    coverage = coverage_fields(capture)
    # Scale coverage down so it is a tie-breaker, not a way to outvote a failure.
    reward = passed - 3.0 * failed + min(coverage / 1000.0, 5.0)
    if failed:
        # A capture that breaks an identity or is empty must never win, however
        # much raw data it holds.
        reward = min(reward, -1.0)
    return {
        "reward": reward,
        "passed": passed,
        "failed": failed,
        "coverage": coverage,
        "failed_checks": [
            f["check"] for f in payload.get("findings", []) if not f.get("ok")
        ],
    }


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def run_candidate(home: Path, config: Config, app: List[str],
                  out_root: Path, kernel: Optional[str],
                  timeout: int) -> Tuple[Optional[Path], str]:
    """Collect one candidate into its own directory. Returns (capture, log)."""
    target = out_root / config.name
    target.mkdir(parents=True, exist_ok=True)
    rc, log, capture = KP.run_msopprof(
        home=home, app=app, out_dir=target, metrics=config.metrics,
        warmup=config.warmup, launch_count=config.launch_count,
        kernel=kernel, replay_mode=config.replay_mode, timeout=timeout,
    )
    (target / "collect.log").write_text(log, errors="replace")
    return capture, log


def search(args: argparse.Namespace) -> int:
    home = KP.find_cann_home()
    if home is None:
        print("错误: 找不到 CANN 安装", file=sys.stderr)
        return 1

    out_root = Path(args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    app = [str(Path(args.app).resolve())] + list(args.app_args or [])

    ok, message = KP.check_device_idle(home)
    if not ok:
        print(f"[warn] {message}")

    critic_script = HERE / "critic.py"
    configs = candidate_configs(quick=args.quick)
    results: List[Dict[str, Any]] = []

    for index, config in enumerate(configs, 1):
        print(f"\n[{index}/{len(configs)}] {config.describe()}", file=sys.stderr)
        started = time.time()
        capture, log = run_candidate(
            home, config, app, out_root / "runs", args.kernel, args.timeout
        )
        elapsed = time.time() - started
        if capture is None:
            print(f"  → 采集为空（{elapsed:.0f}s）", file=sys.stderr)
            results.append({
                "config": config.name, "metrics": config.metrics,
                "replay_mode": config.replay_mode,
                "launch_count": config.launch_count, "warmup": config.warmup,
                "reward": float("-inf"), "passed": 0, "failed": 99,
                "coverage": 0, "error": "no capture produced",
                "elapsed_s": round(elapsed, 1),
            })
            continue

        loaded = KP.load_capture(capture, home)
        analysis = KP.analyze(loaded)
        report_path = capture.parent / "report.md"
        report_path.write_text(KP.render_markdown(analysis))
        (capture.parent / "metrics.json").write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False)
        )
        verdict = score_capture(loaded, critic_script, report_path)
        verdict.update({
            "config": config.name,
            "metrics": config.metrics,
            "replay_mode": config.replay_mode,
            "launch_count": config.launch_count,
            "warmup": config.warmup,
            "capture": str(capture),
            "elapsed_s": round(elapsed, 1),
        })
        results.append(verdict)
        print(f"  → reward={verdict['reward']:.2f} "
              f"pass={verdict['passed']} fail={verdict['failed']} "
              f"coverage={verdict['coverage']} ({elapsed:.0f}s)",
              file=sys.stderr)
        if verdict.get("failed_checks"):
            print(f"    失败检查: {', '.join(verdict['failed_checks'])}",
                  file=sys.stderr)

    # --- pick the winner ---------------------------------------------------
    usable = [r for r in results if r["reward"] > float("-inf")]
    if not usable:
        print("\n错误: 没有任何配置产出可用数据。", file=sys.stderr)
        (out_root / "search.json").write_text(
            json.dumps({"results": results}, indent=2, ensure_ascii=False)
        )
        return 1
    winner = max(usable, key=lambda r: (r["reward"], r["coverage"]))

    summary = {
        "app": " ".join(app),
        "kernel": args.kernel,
        "winner": winner["config"],
        "winner_capture": winner.get("capture"),
        "winner_reward": winner["reward"],
        "results": results,
    }
    (out_root / "search.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )

    print("\n" + "=" * 66)
    print("配置搜索结论")
    print("=" * 66)
    for entry in sorted(usable, key=lambda r: -r["reward"]):
        mark = "  ← 最优" if entry is winner else ""
        print(f"  {entry['config']:18} reward={entry['reward']:6.2f} "
              f"pass={entry['passed']:2d} fail={entry['failed']} "
              f"coverage={entry['coverage']:5d}{mark}")
    for entry in results:
        if entry["reward"] == float("-inf"):
            print(f"  {entry['config']:18} 不可用（采集为空）")

    # Materialise the winner as the canonical capture.
    final = out_root / "capture"
    if final.exists():
        shutil.rmtree(final)
    shutil.copytree(winner["capture"], final)
    loaded = KP.load_capture(final, home)
    analysis = KP.analyze(loaded)
    (out_root / "metrics.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False)
    )
    (out_root / "report.md").write_text(KP.render_markdown(analysis))
    print(f"\n最优采集已归档到 {final}")
    print(f"报告: {out_root / 'report.md'}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="collect_optimize.py",
        description=(
            "Adversarial search over msopprof collection configurations.\n"
            "The critic script is the reward model: a capture that breaks an "
            "identity or comes back empty can never win, however much data it "
            "appears to contain."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  collect_optimize.py --app ./bench --kernel my_kernel --out ./opt\n"
            "  collect_optimize.py --app './mm 1024 1024 1024' --out ./opt --quick\n"
        ),
    )
    parser.add_argument("--app", required=True, help="可执行文件或完整命令行")
    parser.add_argument("--kernel", default=None, help="kernel 名（子串匹配）")
    parser.add_argument("--out", default="./kernel_perf_opt", help="输出目录")
    parser.add_argument("--quick", action="store_true",
                        help="只试 4 个最有价值的配置")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="单次采集超时秒数")
    args, extra = parser.parse_known_args(argv)
    args.app_args = extra
    return search(args)


if __name__ == "__main__":
    sys.exit(main())
