"""Timing protocol: warm up, sync, interleave, and win only above the measured noise floor.

Every choice here is a defence against a specific way a measurement lies:

* **Warm-up first, then time.** A cold run measures the clock ramp, not the kernel.
* **Sync before and after, and time only the kernel.** A kernel that queues work and returns
  would otherwise look infinitely fast.
* **Interleave candidate and baseline.** Thermal and frequency drift then hits both arms, so
  drift cannot become a fake speedup.
* **At least 30 samples, report the median and a bootstrap interval.** A single run cannot
  distinguish a real gain from jitter.
* **The win rule is the lower confidence bound clearing the A/A noise threshold.** Measured,
  not assumed: the noise floor belongs to the device and the environment fingerprint.
* **Setup time is timed separately and capped.** Otherwise work migrates out of the timed
  region and the kernel looks faster than it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..tooling_path import load_kernel_module, spec as spec_mod, timing
from .. import backends as backends_mod

DEFAULT_WARMUP = 3
DEFAULT_SAMPLES = 30


class TimingError(RuntimeError):
    """Raised when a timing run cannot produce a defensible number."""


def _single(callable_fn: Callable[[], None], backend: backends_mod.BackendBase) -> float:
    backend.sync()
    start = backend.now()
    callable_fn()
    backend.sync()
    return (backend.now() - start) * 1e6


def measure(callable_fn: Callable[[], None], backend: backends_mod.BackendBase,
            samples: int = DEFAULT_SAMPLES, warmup: int = DEFAULT_WARMUP) -> List[float]:
    """Time one arm: ``warmup`` discarded runs, then ``samples`` kept ones, all in microseconds."""
    for _ in range(max(0, warmup)):
        _single(callable_fn, backend)
    return [_single(callable_fn, backend) for _ in range(max(1, samples))]


def interleaved(candidate_fn: Callable[[], None], baseline_fn: Callable[[], None],
                backend: backends_mod.BackendBase, samples: int = DEFAULT_SAMPLES,
                warmup: int = DEFAULT_WARMUP) -> Dict[str, List[float]]:
    """Alternate the two arms so drift cancels; return both sample lists."""
    for _ in range(max(0, warmup)):
        _single(candidate_fn, backend)
        _single(baseline_fn, backend)
    candidate: List[float] = []
    baseline: List[float] = []
    for index in range(max(1, samples)):
        if index % 2 == 0:
            candidate.append(_single(candidate_fn, backend))
            baseline.append(_single(baseline_fn, backend))
        else:
            baseline.append(_single(baseline_fn, backend))
            candidate.append(_single(candidate_fn, backend))
    return {"candidate": candidate, "baseline": baseline}


def timing_report(candidate: Sequence[float], baseline: Sequence[float],
                  noise_threshold: Optional[float] = None,
                  setup_us: float = 0.0, setup_cap_us: Optional[float] = None,
                  seed: int = 0) -> Dict[str, Any]:
    """Assemble the ``timing`` block of a result."""
    if not candidate or not baseline:
        raise TimingError("a timing report needs samples from both arms")
    ratio = timing.speedup_ci(candidate, baseline, seed=seed)
    threshold = float(noise_threshold) if noise_threshold is not None else 1.0 + 3.0 * (
        timing.aa_noise(candidate, baseline, seed=seed)["relative_spread"])
    significant = bool(timing.wins(ratio, threshold))
    if setup_cap_us is not None and setup_us > setup_cap_us:
        raise TimingError(
            f"setup took {setup_us:.1f} us, above the spec cap of {setup_cap_us:.1f} us: "
            "work is moving outside the timed region"
        )
    candidate_summary = timing.summarise(candidate, seed=seed)
    baseline_summary = timing.summarise(baseline, seed=seed)
    return {
        "candidate_us": candidate_summary["median_us"],
        "baseline_us": baseline_summary["median_us"],
        "speedup": ratio["point"],
        "speedup_ci_low": ratio["low"],
        "speedup_ci_high": ratio["high"],
        "samples": candidate_summary["samples"],
        "noise_threshold": threshold,
        "significant": significant,
        "setup_us": setup_us,
        "setup_cap_us": setup_cap_us,
        "interleaved": True,
        "candidate_summary": candidate_summary,
        "baseline_summary": baseline_summary,
        "samples_candidate": list(candidate),
        "samples_baseline": list(baseline),
    }


def aa_test(run: Callable[[], None], backend: backends_mod.BackendBase,
            samples: int = DEFAULT_SAMPLES, warmup: int = DEFAULT_WARMUP,
            seed: int = 0) -> Dict[str, Any]:
    """The same kernel measured as if it were two candidates.

    The "difference" is the noise floor. It is measured when an operator is onboarded and
    re-measured when the environment changes; a candidate must beat it, not beat 1.0.
    """
    for _ in range(max(0, warmup)):
        _single(run, backend)
    arm_a = [_single(run, backend) for _ in range(max(1, samples))]
    arm_b = [_single(run, backend) for _ in range(max(1, samples))]
    noise = timing.aa_noise(arm_a, arm_b, seed=seed)
    ratio = timing.speedup_ci(arm_a, arm_b, seed=seed)
    return {
        "threshold": noise["threshold"],
        "relative_spread": noise["relative_spread"],
        "ratio_point": ratio["point"],
        "ratio_low": ratio["low"],
        "ratio_high": ratio["high"],
        "samples": len(arm_a),
        "arm_a_us": timing.median(arm_a),
        "arm_b_us": timing.median(arm_b),
    }


def drift_test(run: Callable[[], None], backend: backends_mod.BackendBase,
               duration_samples: int = 200, warmup: int = 0) -> Dict[str, float]:
    """Log throughput as the device heats; report how long warm-up must be.

    A long unbroken run tells you two things the A/A test cannot: how many iterations it takes
    before the number stabilises, and how much the number moves afterwards. Both go into the
    calibration record and into the warm-up length the harness uses.
    """
    for _ in range(max(0, warmup)):
        _single(run, backend)
    samples = [_single(run, backend) for _ in range(max(2, duration_samples))]
    window = max(1, len(samples) // 10)
    head = timing.median(samples[:window])
    tail = timing.median(samples[-window:])
    plateau = tail
    warmup_iterations = 0
    for index in range(0, len(samples) - window, window):
        if abs(timing.median(samples[index: index + window]) - plateau) <= 0.02 * plateau:
            warmup_iterations = index
            break
    return {
        "warmup_iterations": float(warmup_iterations),
        "head_us": head,
        "tail_us": tail,
        "drift_fraction": (head - tail) / tail if tail else 0.0,
        "samples": float(len(samples)),
    }


def _resolve(candidate_path: Any, op_directory: Any) -> str:
    """Resolve a candidate the way the CLI does: absolute, then relative to the operator.

    A path that is silently reinterpreted is how a measurement ends up describing the baseline
    while the report says it described the candidate.
    """
    path = Path(candidate_path)
    candidates = [path] if path.is_absolute() else [Path.cwd() / path, Path(op_directory) / path]
    for option in candidates:
        for form in (option, option.with_suffix(".py")):
            if form.is_file():
                return str(form.resolve())
    return str(path)


def build_arms(op_spec: spec_mod.Spec, candidate_path: Any, baseline_path: Any,
               backend: backends_mod.BackendBase, case: Optional[Dict[str, int]] = None,
               dtype: Optional[str] = None) -> Dict[str, Callable[[], None]]:
    """Create the two closures the harness times, on one fixed case.

    The case is fixed so both arms do identical work; the shapes that vary live in the
    correctness suite, not in the timing run.
    """
    case = case or dict(op_spec.shapes["typical"][0])
    dtype = dtype or op_spec.dtypes[0]
    numel = 1
    for value in case.values():
        numel *= int(value)
    n_inputs = int(op_spec.entry.get("inputs", 1) or 1)
    inputs = [[1.0] * numel for _ in range(n_inputs)]
    candidate_module = load_kernel_module(_resolve(candidate_path, op_spec.directory), op_spec.directory, backend)
    baseline_module = load_kernel_module(op_spec.entry["baseline"], op_spec.directory, backend)
    candidate_buffers = [backend.from_list(values) for values in inputs]
    candidate_out = backend.zeros(numel)
    baseline_buffers = [backend.from_list(values) for values in inputs]
    baseline_out = backend.zeros(numel)
    function = op_spec.entry["function"]

    def run_candidate() -> None:
        getattr(candidate_module, function)(candidate_buffers, candidate_out, case, dtype)

    def run_baseline() -> None:
        getattr(baseline_module, function)(baseline_buffers, baseline_out, case, dtype)

    return {"candidate": run_candidate, "baseline": run_baseline, "case": case, "dtype": dtype}
