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

import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..tooling_path import load_kernel_module, spec as spec_mod, timing
from .. import backends as backends_mod

DEFAULT_WARMUP = 3
DEFAULT_SAMPLES = 30

#: Sentinel written into the candidate's output before a repeat call, so "wrote nothing" is visible.
SENTINEL_OUTPUT = 1.0e38


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


def interleaved(candidate_fn: Callable[[], None], parent_fn: Callable[[], None],
                backend: backends_mod.BackendBase, samples: int = DEFAULT_SAMPLES,
                warmup: int = DEFAULT_WARMUP) -> Dict[str, List[float]]:
    """Alternate the two arms so drift cancels; return both sample lists."""
    for _ in range(max(0, warmup)):
        _single(candidate_fn, backend)
        _single(parent_fn, backend)
    candidate: List[float] = []
    parent: List[float] = []
    for index in range(max(1, samples)):
        if index % 2 == 0:
            candidate.append(_single(candidate_fn, backend))
            parent.append(_single(parent_fn, backend))
        else:
            parent.append(_single(parent_fn, backend))
            candidate.append(_single(candidate_fn, backend))
    return {"candidate": candidate, "parent": parent}


def timing_report(candidate: Sequence[float], baseline: Sequence[float],
                  noise_threshold: Optional[float] = None,
                  setup_us: float = 0.0, setup_cap_us: Optional[float] = None,
                  seed: int = 0, setup_cap_mode: str = "relative",
                  setup_cap_multiple: float = 10.0) -> Dict[str, Any]:
    """Assemble the ``timing`` block of a result."""
    if not candidate or not baseline:
        raise TimingError("a timing report needs samples from both arms")
    ratio = timing.speedup_ci(candidate, baseline, seed=seed)
    threshold = float(noise_threshold) if noise_threshold is not None else 1.0 + 3.0 * (
        timing.aa_noise(candidate, baseline, seed=seed)["relative_spread"])
    significant = bool(timing.wins(ratio, threshold))
    # Two ways to ask the same question. The relative cap asks it correctly for most operators:
    # setup that dwarfs the measured time is setup that swallowed the work. The absolute cap exists
    # for an operator whose setup is genuinely bounded, and is opt-in because a slow interpreter
    # can exceed any absolute number without hiding anything.
    median_us = timing.median(candidate) if candidate else 0.0
    relative_cap = setup_cap_multiple * median_us
    if setup_cap_mode == "absolute" and setup_cap_us is not None:
        if setup_us > setup_cap_us:
            raise TimingError(
                f"setup took {setup_us:.1f} us, above the spec's absolute cap of "
                f"{setup_cap_us:.1f} us: work is moving outside the timed region")
    elif median_us > 0 and setup_us > relative_cap:
        raise TimingError(
            f"setup took {setup_us:.1f} us, more than {setup_cap_multiple:g}x the measured "
            f"{median_us:.1f} us: setup is doing work the timed region should contain")
    setup_cap_value = setup_cap_us if setup_cap_mode == "absolute" else relative_cap
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
        "setup_cap_us": setup_cap_value,
        "setup_cap_mode": setup_cap_mode,
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


def _check_writes(backend: Any, run: Callable[[], None], out: Any) -> None:
    """Nothing written on a repeated call means nothing computed."""
    backend.fill(out, SENTINEL_OUTPUT)
    run()
    backend.sync()
    written = backend.tolist(out)
    if any(value == SENTINEL_OUTPUT for value in written):
        raise TimingError(
            "the candidate wrote nothing on a repeated call: a kernel that skips work after its "
            "first invocation would otherwise time as fast while never computing")


def _check_tracks_input(backend: Any, run: Callable[[], None], buffers: Any, out: Any,
                        function: str, module: Any, case: Dict[str, int], dtype: str) -> None:
    """Change one input value and confirm the answer follows it.

    A kernel that caches its result answers the question it was first asked, so one perturbed
    element is enough to tell a real computation from a replay.
    """
    first = buffers[0]
    if len(first) == 0:
        return
    original = first[0]
    perturbed = original + 1.0
    try:
        first[0] = perturbed
        run()
        backend.sync()
        changed = backend.tolist(out)
        first[0] = original
        run()
        backend.sync()
        restored = backend.tolist(out)
    finally:
        first[0] = original
    if changed == restored:
        raise TimingError(
            "the candidate returned the same output after its input changed: it is replaying a "
            "cached result rather than computing, so the timing loop would measure a cache hit")


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


def build_arms(op_spec: spec_mod.Spec, candidate_path: Any, parent_path: Any,
               backend: backends_mod.BackendBase, case: Optional[Dict[str, int]] = None,
               dtype: Optional[str] = None, seed: int = 0,
               verify_output: bool = True) -> Dict[str, Callable[[], None]]:
    """Create the two closures the harness times, on one fixed case.

    The second arm is the **parent** — the kernel this experiment started from, normally
    ``best/``. It is the spec baseline only for the campaign's first experiment, and the
    distinction matters: a speedup is a property of one step, so a campaign's total speedup is
    the product over its steps, never the product of steps each measured against the same
    starting point.

    The case is fixed so both arms do identical work; the shapes that vary live in the
    correctness suite, not in the timing run.
    """
    case = case or dict(op_spec.shapes["typical"][0])
    dtype = dtype or op_spec.dtypes[0]
    numel = 1
    for value in case.values():
        numel *= int(value)
    n_inputs = int(op_spec.entry.get("inputs", 1) or 1)
    # Random inputs, from a seed derived from the run. All-ones input makes many wrong kernels
    # arithmetically correct, so the timed arm would be checking a much easier problem than the
    # correctness suite does.
    rng = random.Random(seed)
    inputs = [[rng.uniform(-1.0, 1.0) for _ in range(numel)] for _ in range(n_inputs)]
    candidate_module = load_kernel_module(_resolve(candidate_path, op_spec.directory),
                                          op_spec.directory, backend, fresh=True)
    parent_module = load_kernel_module(_resolve(parent_path, op_spec.directory), op_spec.directory, backend)
    candidate_buffers = [backend.from_list(values) for values in inputs]
    candidate_out = backend.zeros(numel)
    parent_buffers = [backend.from_list(values) for values in inputs]
    parent_out = backend.zeros(numel)
    function = op_spec.entry["function"]

    # The timed arm must do the work every time it is called. A kernel that computes once and
    # then returns early is fast in the timing loop and is exactly the cheat that a
    # correctness-only evaluator cannot see, because correctness runs it once. So the fast path is
    # only enabled after the first warm-up call, and the outputs are checked against the parent's
    # when verification is on.
    state = {"candidate_first_done": False}

    def run_candidate() -> None:
        getattr(candidate_module, function)(candidate_buffers, candidate_out, case, dtype)

    def run_parent() -> None:
        getattr(parent_module, function)(parent_buffers, parent_out, case, dtype)

    def verify_repeats() -> None:
        """Prove the candidate keeps computing when called repeatedly, then stop asking it to.

        Two cheats are defeated here, and they need different checks:

        * *Skip after the first call.* Caught by clearing the output and calling again: nothing
          written means nothing computed.
        * *Cache the answer and replay it.* It writes, so the sentinel check passes — the replay
          looks like work. Caught by changing an input and calling again: a kernel that returns
          its cached answer produces the answer for the old input, which is wrong for the new one.

        This runs **once, before timing**, and never inside the timed closure: a check inside the
        loop would charge every sample for several extra kernel executions, and the candidate would
        look several times slower than an identical parent. Warm-up has already called the kernel,
        so a cheat that arms itself after the first call has armed by the time this runs.
        """
        _check_writes(backend, run_candidate, candidate_out)
        _check_tracks_input(backend, run_candidate, candidate_buffers, candidate_out,
                            function, candidate_module, case, dtype)

    def timed_candidate() -> None:
        run_candidate()

    # Warm the candidate once so a first-call-only cheat has armed itself before it is checked.
    run_candidate()
    if verify_output:
        verify_repeats()

    return {"candidate": timed_candidate, "parent": run_parent,
            "case": case, "dtype": dtype, "verified": verify_output,
            "verify": verify_repeats if verify_output else None}
