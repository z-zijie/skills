"""Correctness: reference runs in the evaluator's process, candidate output is untrusted.

The reference implementation's output never sits in memory the candidate can reach, because
the reference is called first, its output is copied out, and only then is the candidate given
its own buffers. Every run fills the candidate's output with a sentinel, because "passed
without writing output" is the cheapest way to cheat: a kernel that returns immediately would
otherwise compare equal to a reference whose output happened to be zeros.

Three shape populations are used, and one of them is drawn fresh on every run, so a kernel
specialised for the shapes it has seen fails the shapes it has not.
"""

from __future__ import annotations

import importlib
import random
from typing import Any, Dict, List, Optional, Tuple

from ..tooling_path import load_kernel_module, spec as spec_mod
from .. import backends as backends_mod

SENTINEL = 1.0e38

#: A candidate that leaves this many output elements at the sentinel is treated as not having
#: written its output at all. One untouched element is already suspicious; this many is
#: unambiguously "did not run".
UNTOUCHED_TOLERANCE = 0


class ReferenceUnavailable(RuntimeError):
    """Raised when an operator has no usable reference implementation."""


class ModuleLoadError(RuntimeError):
    """Raised when a kernel module cannot be imported or lacks its entry function.

    This is an infrastructure failure, not a measurement: it says nothing about whether the
    evaluator would have caught the kernel's behaviour.
    """


def _call(module: Any, name: str, *args: Any) -> Any:
    function = getattr(module, name, None)
    if function is None:
        raise ReferenceUnavailable(f"module {module.__name__!r} has no callable {name!r}")
    return function(*args)


def run_case(op_spec: spec_mod.Spec, case: Dict[str, int], dtype: str, seed: int,
             candidate_path: Any, backend: backends_mod.BackendBase) -> Dict[str, Any]:
    """Run reference and candidate on one case and apply the spec's tolerance.

    Returns a dict with ``passed`` plus the evidence a failure report needs.
    """
    rng = random.Random(seed)
    numel = 1
    for value in case.values():
        numel *= int(value)
    # Inputs are drawn fresh from the seed the evaluator keeps, never cached, so a candidate
    # cannot memorise a result.
    n_inputs = int(op_spec.entry.get("inputs", 1) or 1)
    inputs = [[rng.uniform(-1.0, 1.0) for _ in range(numel)] for _ in range(n_inputs)]

    reference_module = load_kernel_module(op_spec.entry["reference"], op_spec.directory, backend)
    reference_buffers = [backend.from_list(values) for values in inputs]
    reference_out = backend.zeros(numel)
    _call(reference_module, op_spec.entry["function"], reference_buffers, reference_out, case, dtype)
    backend.sync()
    expected = backend.tolist(reference_out)

    candidate_module = load_kernel_module(candidate_path, op_spec.directory, backend, fresh=True)
    candidate_buffers = [backend.from_list(values) for values in inputs]
    candidate_out = backend.zeros(numel)
    backend.fill(candidate_out, SENTINEL)
    _call(candidate_module, op_spec.entry["function"], candidate_buffers, candidate_out, case, dtype)
    backend.sync()
    actual = backend.tolist(candidate_out)

    untouched = sum(1 for value in actual if value == SENTINEL)
    guard_intact = getattr(candidate_out, "guard_intact", None)

    if untouched > UNTOUCHED_TOLERANCE:
        return {
            "passed": False,
            "reason": f"candidate left {untouched} of {numel} output elements at the sentinel: it did not write its output",
            "shape": case,
            "dtype": dtype,
            "seed": seed,
            "canary_tripped": False,
            "sentinel_untouched": True,
        }
    if guard_intact is False:
        return {
            "passed": False,
            "reason": "candidate wrote outside its output buffer (guard elements were overwritten)",
            "shape": case,
            "dtype": dtype,
            "seed": seed,
            "canary_tripped": True,
            "sentinel_untouched": False,
        }

    ok, error = op_spec.compare(expected, actual)
    return {
        "passed": ok,
        "reason": "" if ok else "output differs from the reference beyond the spec tolerance",
        "shape": case,
        "dtype": dtype,
        "seed": seed,
        "max_abs_err": error.get("max_abs_err"),
        "max_rel_err": error.get("max_rel_err"),
        "canary_tripped": False,
        "sentinel_untouched": False,
    }


def run_suite(op_spec: spec_mod.Spec, candidate_path: Any, backend: backends_mod.BackendBase,
              seed: int = 0, max_cases: Optional[int] = None) -> Dict[str, Any]:
    """Run the typical, edge and fresh-random populations and summarise."""
    cases = op_spec.cases(seed=seed)
    if max_cases:
        cases = cases[: int(max_cases)]
    dtype = op_spec.dtypes[0]
    failures: List[Dict[str, Any]] = []
    passed = 0
    for index, case in enumerate(cases):
        case_seed = seed + index * 7919
        try:
            result = run_case(op_spec, case, dtype, case_seed, candidate_path, backend)
        except ReferenceUnavailable as error:
            raise ModuleLoadError(str(error)) from error
        except Exception as error:  # a candidate that raises is a correctness failure
            result = {
                "passed": False,
                "reason": f"candidate raised {type(error).__name__}: {error}",
                "shape": case,
                "dtype": dtype,
                "seed": case_seed,
                "canary_tripped": False,
                "sentinel_untouched": False,
            }
        if result["passed"]:
            passed += 1
        else:
            failures.append({
                "shape": result["shape"],
                "dtype": result["dtype"],
                "seed": result["seed"],
                "reason": result["reason"],
                "max_abs_err": result.get("max_abs_err"),
                "max_rel_err": result.get("max_rel_err"),
            })
    return {
        "passed": not failures,
        "cases_run": len(cases),
        "cases_failed": len(failures),
        "failures": failures[:20],
        "canary_tripped": any("outside its output buffer" in str(failure.get("reason", "")) for failure in failures),
        "sentinel_untouched": any("sentinel" in str(failure.get("reason", "")) for failure in failures),
        "seed": seed,
    }


def _timing_cheat(op_spec: spec_mod.Spec, cheat: Dict[str, str],
                  backend: backends_mod.BackendBase, seed: int) -> Dict[str, Any]:
    """A cheat aimed at the timing loop, judged by the timing arm.

    The timing arm must refuse it. Correctness passing is expected and is not a hole: it is exactly
    the blind spot this cheat class exists to prove the timing arm covers, because each correctness
    case loads the module fresh and a stateful cheat therefore resets and looks honest.
    """
    from ..harness import timing as protocol

    case = dict(op_spec.shapes["typical"][0])
    try:
        protocol.build_arms(op_spec, cheat["path"], op_spec.entry["baseline"], backend,
                            case=case, seed=seed)
    except protocol.TimingError as error:
        return {"name": cheat["name"], "expect": cheat.get("expect", "caught"), "caught": True,
                "reason": str(error), "detected_by": "timing", "infrastructure_error": False}
    except Exception as error:  # a cheat that cannot load proves nothing
        return {"name": cheat["name"], "expect": cheat.get("expect", "caught"), "caught": False,
                "reason": f"{type(error).__name__}: {error}", "detected_by": "timing",
                "infrastructure_error": True}
    return {"name": cheat["name"], "expect": cheat.get("expect", "caught"), "caught": False,
            "reason": "the timing arm measured it, so a cache cheat would have looked fast",
            "detected_by": "timing", "infrastructure_error": False}


def red_team(op_spec: spec_mod.Spec, cheat_kernels: List[Dict[str, str]],
             backend: backends_mod.BackendBase, seed: int = 0) -> Dict[str, Any]:
    """Run deliberately cheating kernels; the evaluator must catch every one.

    This is the check the build plan calls for before going live. A cheat kernel that passes is a
    hole in Layer 0, and every piece of experience built on top of it is noise.

    Which detector is responsible depends on the cheat. A kernel that writes the wrong answer is
    caught by correctness; a kernel that answers correctly once and then replays a cache is caught
    by the timing arm, and correctness is *supposed* to pass it — each correctness case loads the
    module fresh, so a stateful cheat resets and looks honest. Running every cheat against every
    detector would report either false holes or false catches, so the manifest says which check
    each one tests.
    """
    results: List[Dict[str, Any]] = []
    for cheat in cheat_kernels:
        if str(cheat.get("via") or "correctness") == "timing":
            results.append(_timing_cheat(op_spec, cheat, backend, seed))
            continue
        try:
            report = run_suite(op_spec, cheat["path"], backend, seed=seed)
            reason = report["failures"][0]["reason"] if report["failures"] else ""
            passed = report["passed"]
        except (ModuleLoadError, ImportError, SyntaxError, OSError) as error:
            report, reason, passed = {"passed": False}, f"{type(error).__name__}: {error}", False
        caught = False
        # "Caught" has to mean the evaluator detected the cheat, not that it crashed on it. A
        # cheat kernel that fails to import is caught by nothing and proves nothing: counting it
        # would let a broken red-team suite report a clean sweep.
        infrastructure = isinstance(reason, str) and any(
            marker in reason
            for marker in ("ModuleLoadError", "no kernel module", "cannot import",
                           "ModuleNotFoundError", "ImportError", "SyntaxError", "FileNotFoundError")
        )
        caught = (not passed) and not infrastructure
        results.append({
            "name": cheat["name"],
            "expect": cheat.get("expect", "caught"),
            "caught": caught,
            "reason": reason or "passed the evaluator",
            "infrastructure_error": infrastructure,
        })
    failures = [entry for entry in results if not entry["caught"]]
    return {
        "all_caught": not failures and bool(results),
        "cheats": results,
        "holes": [entry["name"] for entry in failures],
        "infrastructure_errors": [entry["name"] for entry in results
                                  if entry.get("infrastructure_error")],
        "note": ("a cheat that fails to load is reported as an infrastructure error, not as "
                 "caught: the evaluator proved nothing about it"),
    }
