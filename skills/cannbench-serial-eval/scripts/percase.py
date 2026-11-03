#!/usr/bin/env python3
"""Evaluate whole cases of one operator through the official harness code.

Why this exists
---------------
`scripts/run_evaluation.sh` is the full benchmark entry. It fans every case of
the requested task directory out to several worker processes at once. On this
single-card Ascend950PR box that fan-out is what wedges the machine: concurrent
operator development plus multi-process profiling exhausts the container and
the box stops responding. So the full script is off-limits for day-to-day work.

The harness already carries a single-process, single-case entry point:
`python -m kernel_eval.cli eval-child`, which takes one cases JSON and runs
those cases with the official accuracy checker, the official profiler timing and
the official Eq.3 per-case score. This script drives exactly that, one process
per case, so the numbers stay directly comparable with the leaderboard instead
of being re-derived by a lookalike metric.

Usage:
  percase.py --op <op_id> --case 1 [--case 3] --out-dir DIR
  percase.py --op <op_id> --all --out-dir DIR

  --op      op_id from tasks/suite_manifest.json (e.g. swi_glu)
  --case    case number, repeatable (default: 1)
  --all     every case of the operator, still one process per case
  --out-dir directory for logs and results

Prints one JSON line per case, then one aggregate line, and writes
results.json / projection.json into --out-dir.
Exit status: 0 when every requested case ran and passed accuracy, 1 otherwise.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BENCH = Path("/workspace/cann-bench")
TASKS = BENCH / "tasks"
PYBIN = "/workspace/cannbench/evalenv/bin/python"
CNB_ENV = "/workspace/cannbench/bin/cnb-env"


def op_rel_path(op_id):
    """Map an op_id from the suite manifest to its task rel_path."""
    manifest = json.loads((TASKS / "suite_manifest.json").read_text())
    for op in manifest["operators"]:
        if op["id"] == op_id:
            return op["path"].split("tasks/", 1)[-1]
    return None


def load_cases(op_id, cases):
    """Return the official case specs for *op_id*, filtered to *cases*.

    Goes through the harness loader so shapes, dtypes, attrs, tolerances and the
    baseline/T_HW anchors are exactly the ones the full run would use for the
    same case numbers. *cases* None means every case of the operator.
    """
    sys.path.insert(0, str(BENCH / "src"))
    # Importing the bench package is what registers the loaders; the harness CLI
    # does the same before it touches the registry.
    from kernel_eval.benches import cann as _cann_bench  # noqa: F401
    from kernel_eval.registry import get_case_loader

    rel_path = op_rel_path(op_id)
    if rel_path is None:
        raise SystemExit(f"unknown op {op_id!r} in suite_manifest.json")

    wanted = None if cases is None else {int(c) for c in cases}
    loader = get_case_loader("cann", tasks_root=str(TASKS))
    return [spec for spec in loader.scan_all()
            if spec.rel_path == rel_path
            and (wanted is None or spec.case_num in wanted)]


def child_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BENCH / "src") + (
        ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["ASCEND_SLOG_PRINT_TO_STDOUT"] = "0"
    env["ASCEND_GLOBAL_LOG_LEVEL"] = "3"
    return env


def run_child(spec, out_dir, warmup, repeat, timeout):
    """Run the official eval-child for one case; return its case result dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_file = out_dir / "cases.json"
    result_file = out_dir / "child.json"
    cases_file.write_text(json.dumps([spec.to_dict()], ensure_ascii=False))

    cmd = [
        PYBIN, "-u", "-m", "kernel_eval.cli", "eval-child",
        "--bench-name", "cann",
        "--device-id", "0",
        "--cases-file", str(cases_file),
        "--output", str(result_file),
        "--warmup", str(warmup),
        "--repeat", str(repeat),
        "--reports-dir", str(out_dir / "reports"),
        "--task-dir", str(TASKS),
        # One case per process: merged batch profiling needs the multi-case
        # fan-out that this path exists to avoid, and separate processes keep
        # host memory from accumulating across a whole operator.
        "--no-perf-batch-cases",
        "--perf-batch-case-memory-limit-mb", "32768",
        "--profiler-level", "Level1",
    ]

    log = out_dir / "child.log"
    with open(log, "w") as fh:
        try:
            subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                           timeout=timeout, env=child_env(), check=False)
        except subprocess.TimeoutExpired:
            return {"success": False,
                    "error_msg": f"eval-child timed out after {timeout}s"}

    if not result_file.is_file():
        tail = ""
        try:
            tail = log.read_text(errors="replace")[-1000:]
        except OSError:
            pass
        return {"success": False, "error_msg": "eval-child produced no result",
                "log_tail": tail}

    rows = json.loads(result_file.read_text()).get("case_results") or []
    return rows[0] if rows else {"success": False, "error_msg": "no case result"}


def summarize(spec, row):
    """Flatten one official case result into the fields the loop compares."""
    perf = row.get("perf") or {}
    acc = row.get("accuracy") or {}
    return {
        "operator": spec.operator,
        "rel_path": spec.rel_path,
        "case_num": spec.case_num,
        "ok": bool(row.get("success")),
        "accuracy_passed": bool(acc.get("passed")) if acc else None,
        "elapsed_us": perf.get("elapsed_us"),
        "baseline_perf_us": row.get("baseline_perf_us", spec.baseline_perf_us),
        "t_hw_us": row.get("t_hw_us", spec.t_hw_us),
        "perf_score": perf.get("perf_score"),
        "speedup": perf.get("speedup"),
        "error_msg": row.get("error_msg"),
        "failure_type": row.get("failure_type"),
    }


def project(results, full=False):
    """Score over the cases that were run, using the harness's own Eq.4 weights.

    With every case present this is the operator's official score: compile 5,
    accuracy 20 scaled by the passed fraction, performance 70 times the mean
    Eq.3 score, plus the 5-point all-pass bonus. With only a sample it is the
    same expression minus the bonus, which is the accept/reject signal for
    one-change-one-measure iteration -- not a leaderboard number.
    """
    perf_scores = [r["perf_score"] for r in results if r["perf_score"] is not None]
    n = len(results)
    passed = sum(1 for r in results if r["ok"])
    mean_perf = (sum(perf_scores) / len(perf_scores)) if perf_scores else None
    all_passed = passed == n
    bonus = 5.0 if (full and all_passed) else 0.0
    return {
        "op": results[0]["op"] if results else None,
        "cases": [r["case_num"] for r in results],
        "n_cases": n,
        "all_passed": all_passed,
        "mean_perf_score": mean_perf,
        "projected_operator_score": (
            None if mean_perf is None
            else 5.0 + 20.0 * (passed / n) + 70.0 * mean_perf + bonus),
        "full_operator": bool(full),
        "note": ("official operator score (all cases)" if full
                 else "projection over sampled cases; the all-case bonus is excluded"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", required=True)
    ap.add_argument("--case", type=int, action="append", dest="cases",
                    help="case number, repeatable; omit with --all for every case")
    ap.add_argument("--all", action="store_true",
                    help="run every case of the operator, one process per case")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        specs = load_cases(args.op, None)
    else:
        specs = load_cases(args.op, args.cases or [1])
    if not specs:
        print(json.dumps({"op": args.op, "ok": False, "error": "no matching cases"}))
        return 2

    wanted = {int(c) for c in (args.cases or [])}
    if not args.all:
        missing = sorted(wanted - {s.case_num for s in specs})
        if missing:
            print(json.dumps({"op": args.op, "ok": False,
                              "error": f"cases not found: {missing}"}))
            return 2

    results = []
    for spec in sorted(specs, key=lambda s: s.case_num):
        row = run_child(spec, out_dir / f"case_{spec.case_num}",
                        args.warmup, args.repeat, args.timeout)
        rec = summarize(spec, row)
        rec["op"] = args.op
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False))
        sys.stdout.flush()

    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1))
    proj = project(results, full=args.all)
    print(json.dumps(proj, ensure_ascii=False))
    (out_dir / "projection.json").write_text(
        json.dumps(proj, ensure_ascii=False, indent=1))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
