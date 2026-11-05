"""The evaluator's only entry point.

Three commands, and nothing else calls into Layer 0:

=================  ==================================================  ======================
``evaluate``       correctness plus timing                          signed ``result.json``
``profile``        profiler collection, canonical metric extraction  ``profile.json`` + raw
``calibrate``      microbenchmark suite                              ``kb/hardware/measured/``
=================  ==================================================  ======================

The evaluator is the system's only source of truth. If it is wrong, every piece of experience
built on it is noise, so it is built first and never modifies itself. Its source is hashed at
startup and the hash is recorded in every result: when the evaluator changes, past results are
visibly from another evaluator, and every baseline has to be re-run.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluator import backends  # noqa: E402
from evaluator.harness import timing as harness  # noqa: E402
from evaluator.oracle import correctness  # noqa: E402
from evaluator.profiler import adapters  # noqa: E402
from evaluator.tooling_path import (  # noqa: E402
    hardware, jsonschema_lite, ledger, paths, spec as spec_mod, yamlite,
)

EVALUATOR_VERSION = "1.0.0"
DEFAULT_SAMPLES = 30
DEFAULT_WARMUP = 3


# -- identity -------------------------------------------------------------

def evaluator_hash() -> str:
    """Hash of Layer 0's own source, so a changed evaluator is visible in every result."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _backend(args: argparse.Namespace) -> backends.BackendBase:
    config = hardware.discover_device()
    return backends.select(args.backend or config.get("backend"), config)


def _spec(op: str) -> spec_mod.Spec:
    return spec_mod.Spec.load(op)


def _candidate_path(op_spec: spec_mod.Spec, candidate: str) -> Path:
    """Resolve a candidate module.

    Accepted forms, tried in this order: an absolute path, a path relative to the working
    directory, a path relative to the RSI root, and a path relative to the operator directory.
    Anything else fails loudly, because a mistyped candidate that silently falls back to the
    baseline would look like a perfect measurement.
    """
    if not candidate:
        raise SystemExit("evaluate needs --candidate pointing at a kernel module")
    raw = Path(candidate)
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append((Path.cwd() / raw))
        candidates.append((paths.repo_root() / raw))
        candidates.append((op_spec.directory / raw))
    for option in candidates:
        for form in (option, option.with_suffix(".py")):
            if form.is_file():
                return form.resolve()
    tried = ", ".join(str(option) for option in candidates)
    raise SystemExit(f"no candidate module found; tried: {tried}")


# -- evaluate -------------------------------------------------------------

def cmd_evaluate(args: argparse.Namespace) -> int:
    op_spec = _spec(args.op)
    backend = _backend(args)
    candidate = _candidate_path(op_spec, args.candidate)
    device = hardware.discover_device()
    fingerprint = hardware.env_fingerprint(device)

    seed = _seed(args.seed)
    correctness_report = correctness.run_suite(
        op_spec, candidate, backend, seed=seed, max_cases=args.max_cases
    )
    result: Dict[str, Any] = {
        "schema_version": "1",
        "exp_kind": "evaluate",
        "op": op_spec.op,
        "candidate": paths.rel(candidate),
        "hw_id": str(device.get("hw_id", "")),
        "env_fingerprint": fingerprint,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_hash": evaluator_hash(),
        "created": _now(),
        "seed": seed,
        "candidate_hash": ledger.candidate_hash(candidate),
        "tolerance_hash": ledger.tolerances_hash(op_spec),
        "correctness": {
            "passed": correctness_report["passed"],
            "cases_run": correctness_report["cases_run"],
            "cases_failed": correctness_report["cases_failed"],
            "failures": correctness_report["failures"],
            "canary_tripped": correctness_report["canary_tripped"],
            "sentinel_untouched": correctness_report["sentinel_untouched"],
        },
        "timing": {
            "candidate_us": None,
            "baseline_us": None,
            "samples": 0,
            "setup_us": 0.0,
        },
        "guards": {},
    }

    if not correctness_report["passed"]:
        # A correctness failure stops here: a fast wrong kernel is not a candidate.
        signed = ledger.sign(result)
        _write(args.out, signed)
        _emit(signed)
        return 0

    # The second arm is the parent this experiment started from. `--parent` names it; without one
    # it is the spec baseline, which is the parent of the campaign's first experiment.
    parent = args.parent or op_spec.entry["baseline"]
    noise = _noise_threshold(device, root=None)

    # Timing runs in a separate interpreter. Correctness has already imported the candidate into
    # this process; a fresh child means a candidate that patched something during its correctness
    # run cannot reach the timing loop, and this process never holds candidate state while it signs.
    from evaluator import sandbox

    root = paths.repo_root()
    measured = sandbox.measure_in_child({
        "op": op_spec.op,
        "root": str(root),
        "candidate": str(Path(candidate)),
        "parent": str(parent),
        "case": args.case or None,
        "seed": seed,
        "samples": args.samples,
        "warmup": args.warmup,
        "backend": args.backend,
        "device": device,
        "verify_output": not args.no_output_check,
    }, root=root)
    if not measured.get("ok"):
        # The child refused the candidate, which is a finding, not a crash. Say what it found and
        # say which candidate: a refusal that arrives as a traceback reads like a broken tool.
        error = str(measured.get("error") or "")
        raise SystemExit(
            f"the timing child refused {paths.rel(candidate)}: {error}"
            if error else f"the timing child failed for {paths.rel(candidate)}")

    timing_block = harness.timing_report(
        measured["candidate"], measured["parent"],
        noise_threshold=noise, setup_us=float(measured.get("setup_us") or 0.0),
        setup_cap_us=op_spec.setup_cap_us,
        setup_cap_mode=getattr(op_spec, "setup_cap_mode", "relative"), seed=seed,
    )
    timing_block["measured_in"] = "separate-process"
    timing_block["setup_us"] = float(measured.get("setup_us") or 0.0)
    timing_block["process_launch_us"] = float(measured.get("process_launch_us") or 0.0)
    timing_block["interleaved"] = True
    timing_block["parent"] = paths.rel(parent) if isinstance(parent, (str, Path)) else str(parent)
    timing_block.pop("samples_candidate", None), timing_block.pop("samples_baseline", None)
    result["timing"] = timing_block

    speedup = timing_block.get("speedup")
    cap = float(op_spec.speedup_review_cap or 2.0)
    result["guards"] = {
        "implausible_speedup": bool(speedup and speedup >= cap),
        "files_modified": [],
        "tolerances_unchanged": True,
    }
    signed = ledger.sign(result)
    _write(args.out, signed)
    _emit(signed)
    if result["guards"]["implausible_speedup"]:
        print(
            f"paused: a single step of {speedup:.2f}x is at or above the {cap:g}x review cap; "
            "this experiment needs your review before it counts as evidence",
            file=sys.stderr,
        )
    return 0


def cmd_aa(args: argparse.Namespace) -> int:
    """A/A test: the same kernel measured as two candidates, to get the noise floor."""
    op_spec = _spec(args.op)
    backend = _backend(args)
    kernel = args.candidate or op_spec.entry["baseline"]
    arms = harness.build_arms(op_spec, kernel, kernel, backend)
    report = harness.aa_test(arms["parent"], backend, samples=args.samples,
                             warmup=args.warmup, seed=args.seed)
    device = hardware.discover_device()
    payload = {
        "op": op_spec.op,
        "hw_id": str(device.get("hw_id", "")),
        "env_fingerprint": hardware.env_fingerprint(device),
        "aa": report,
        "note": "a real candidate must beat this threshold, not 1.0",
    }
    if args.out:
        _write(args.out, payload)
    _emit(payload)
    return 0


# -- profile --------------------------------------------------------------

def cmd_profile(args: argparse.Namespace) -> int:
    op_spec = _spec(args.op)
    device = hardware.discover_device()
    # `device.profiler` names the adapter; the adapter's *settings* live in a separate block.
    # Passing the name where a settings mapping is expected made `profiler: msopprof` in
    # device.yaml crash `profile`, which is a documented configuration rather than an edge case.
    adapter_name = args.adapter or device.get("profiler")
    adapter_settings = device.get("profiler_settings") or {}
    if not isinstance(adapter_settings, dict):
        adapter_settings = {}
    adapter = adapters.select(adapter_name, adapter_settings)
    out_dir = Path(args.out_dir or (paths.artifacts_dir() / "profile"))
    out_dir.mkdir(parents=True, exist_ok=True)
    command = args.command or [sys.executable, "-c", "pass"]
    collected = adapter.profile(command, os.getcwd(), out_dir)
    profile = {
        "op": op_spec.op,
        "candidate": paths.rel(args.candidate or ""),
        "hw_id": str(device.get("hw_id", "")),
        "env_fingerprint": hardware.env_fingerprint(device),
        "created": _now(),
        "adapter": collected.get("adapter", adapter.name),
        "adapter_version": collected.get("adapter_version"),
        "transaction_granularity_bytes": collected.get("transaction_granularity_bytes"),
        "metrics": collected.get("metrics", {}),
        "unavailable": collected.get("unavailable", []),
        "raw_report": collected.get("raw_report", ""),
        "notes": collected.get("note", ""),
    }
    schema = jsonschema_lite.load_schema(str(paths.repo_root() / "schemas" / "profile.schema.json"))
    jsonschema_lite.check(profile, schema, label="profile")
    if args.out:
        _write(args.out, profile)
    _emit(profile)
    return 0


# -- calibrate ------------------------------------------------------------

def cmd_calibrate(args: argparse.Namespace) -> int:
    from evaluator.microbench import runner

    backend = _backend(args)
    device = hardware.discover_device()
    suite = args.suite or "core"
    report = runner.run_suite(suite, backend, device, samples=args.samples,
                              seed=args.seed, quick=args.quick)
    destination = hardware.hardware_file(str(device.get("hw_id", "")))
    merged = runner.merge_into_measured(destination, report, device)
    if args.out:
        _write(args.out, merged)
    _emit(merged)
    return 0


# -- red team -------------------------------------------------------------

def cmd_redteam(args: argparse.Namespace) -> int:
    op_spec = _spec(args.op)
    backend = _backend(args)
    cheats = _red_team_kernels(op_spec)
    if not cheats:
        raise SystemExit(
            f"no red-team kernels for {op_spec.op}: expected them under "
            f"{paths.op_dir(op_spec.op) / 'redteam'}"
        )
    report = correctness.red_team(op_spec, cheats, backend, seed=args.seed)
    if args.out:
        _write(args.out, report)
    _emit(report)
    return 0 if report["all_caught"] else 1


def _red_team_kernels(op_spec: spec_mod.Spec) -> List[Dict[str, str]]:
    directory = paths.op_dir(op_spec.op) / "redteam"
    if not directory.is_dir():
        return []
    manifest = directory / "manifest.yaml"
    if not manifest.is_file():
        return []
    data = yamlite.load(manifest) or {}
    cheats: List[Dict[str, str]] = []
    for entry in data.get("cheats", []):
        cheats.append({
            "name": str(entry.get("name")),
            "path": str(directory / str(entry.get("module", entry.get("name")))),
            "expect": str(entry.get("expect", "caught")),
            # Which detector is responsible for this cheat. Defaults to correctness, which is
            # right for a wrong-output cheat; a cheat aimed at the timing loop declares `timing`.
            "via": str(entry.get("via", "correctness")),
        })
    return cheats


# -- helpers --------------------------------------------------------------

def _noise_threshold(device: Dict[str, Any], root: Optional[Path] = None) -> Optional[float]:
    """The measured A/A threshold, if calibration has run; None means "not measured yet"."""
    try:
        measured = hardware.load_measured(str(device.get("hw_id", "")), root)
    except hardware.HardwareError:
        return None
    noise = measured.get("noise_floor") or {}
    value = noise.get("threshold")
    return float(value) if value is not None else None


def _write(path: Optional[str], payload: Dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _seed(requested: Optional[int]) -> int:
    """The run's seed: the caller's, or a fresh one.

    A fixed default would make "fresh random shapes" a claim about the code rather than about the
    run. The seed is always recorded in the result, so a run remains reproducible after the fact.
    """
    if requested is not None:
        return int(requested)
    return secrets.randbelow(2 ** 31 - 1)


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rsi evaluate",
        description="Layer 0: the frozen evaluator. Call it; do not read or edit it.",
    )
    parser.add_argument("--root", help="RSI repository root (default: autodetected)")
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("evaluate", help="correctness plus timing; emits a signed result")
    evaluate.add_argument("--op", required=True)
    evaluate.add_argument("--candidate", required=True, help="path to the candidate kernel module")
    evaluate.add_argument("--parent",
                          help="the kernel this experiment started from, usually best/. Defaults "
                               "to the spec baseline, which is the parent of the first experiment")
    evaluate.add_argument("--out", help="write the signed result here")
    evaluate.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    evaluate.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    evaluate.add_argument("--seed", type=int, default=None,
                          help="reproduce a previous run; omit for a fresh one, which is recorded")
    evaluate.add_argument("--max-cases", type=int, default=None)
    evaluate.add_argument("--case", help="fixed case as JSON, for the timing run")
    evaluate.add_argument("--no-output-check", action="store_true",
                          help="skip the repeat-call and input-tracking checks")
    evaluate.add_argument("--backend", help="execution backend name")
    evaluate.set_defaults(func=cmd_evaluate)

    aa = sub.add_parser("aa", help="A/A noise floor for this operator")
    aa.add_argument("--op", required=True)
    aa.add_argument("--candidate", default=None)
    aa.add_argument("--out")
    aa.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    aa.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    aa.add_argument("--seed", type=int, default=0)
    aa.add_argument("--backend")
    aa.set_defaults(func=cmd_aa)

    profile = sub.add_parser("profile", help="profiler collection, canonical metrics out")
    profile.add_argument("--op", required=True)
    profile.add_argument("--candidate", default=None)
    profile.add_argument("--command", nargs=argparse.REMAINDER, help="command to profile")
    profile.add_argument("--adapter", help="profiler adapter name")
    profile.add_argument("--out-dir")
    profile.add_argument("--out")
    profile.set_defaults(func=cmd_profile, backend=None)

    calibrate = sub.add_parser("calibrate", help="run the microbenchmark suite")
    calibrate.add_argument("--suite", default="core")
    calibrate.add_argument("--out")
    calibrate.add_argument("--samples", type=int, default=15)
    calibrate.add_argument("--seed", type=int, default=0)
    calibrate.add_argument("--quick", action="store_true", help="fast pass, for smoke tests")
    calibrate.add_argument("--backend")
    calibrate.set_defaults(func=cmd_calibrate)

    redteam = sub.add_parser("redteam", help="run the deliberately cheating kernels")
    redteam.add_argument("--op", required=True)
    redteam.add_argument("--out")
    redteam.add_argument("--seed", type=int, default=0)
    redteam.add_argument("--backend")
    redteam.set_defaults(func=cmd_redteam)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "root", None):
        os.environ["RSI_ROOT"] = str(Path(args.root).resolve())
    if not hasattr(args, "backend"):
        args.backend = None
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
