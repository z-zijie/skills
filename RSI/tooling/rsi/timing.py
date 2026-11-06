"""Timing statistics: medians, bootstrap intervals, interleaved speedup and the noise floor.

The evaluator's job is to say "this is faster" only when the claim survives the machine's own
noise. Two devices differ by an order of magnitude in stability, so nothing here hardcodes a
tolerance: every threshold is measured on the device in front of you (``aa_test``), stored in
``kb/hardware/measured/``, and re-measured when the environment changes.

All randomness is seeded, so a report can be recomputed from archived samples.
"""

from __future__ import annotations

import math
import random
from typing import Dict, Iterable, List, Optional, Sequence

#: Below this many samples a reported interval is labelled provisional.
MIN_SAMPLES = 30
#: Default resamples for the percentile bootstrap.
BOOTSTRAP_RESAMPLES = 2000


def median(values: Sequence[float]) -> float:
    data = sorted(float(value) for value in values)
    if not data:
        raise ValueError("median of an empty sequence")
    middle = len(data) // 2
    if len(data) % 2:
        return data[middle]
    return 0.5 * (data[middle - 1] + data[middle])


def quantile(values: Sequence[float], fraction: float) -> float:
    data = sorted(float(value) for value in values)
    if not data:
        raise ValueError("quantile of an empty sequence")
    if len(data) == 1:
        return data[0]
    position = max(0.0, min(1.0, fraction)) * (len(data) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (position - low)


def trimmed_mean(values: Sequence[float], fraction: float = 0.1) -> float:
    """Drop the slowest and fastest ``fraction`` before averaging: a drift-resistant summary."""
    data = sorted(float(value) for value in values)
    if not data:
        raise ValueError("mean of an empty sequence")
    cut = int(len(data) * fraction)
    kept = data[cut: len(data) - cut] if len(data) - 2 * cut > 0 else data
    return sum(kept) / len(kept)


def bootstrap_ci(
    values: Sequence[float],
    statistic=median,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: float = 0.95,
    seed: int = 0,
) -> Dict[str, float]:
    """Percentile bootstrap interval for ``statistic`` over one sample."""
    data = [float(value) for value in values]
    if not data:
        raise ValueError("bootstrap of an empty sample")
    rng = random.Random(seed)
    stats: List[float] = []
    size = len(data)
    for _ in range(resamples):
        draw = [data[rng.randrange(size)] for _ in range(size)]
        stats.append(statistic(draw))
    alpha = (1.0 - confidence) / 2.0
    return {
        "point": statistic(data),
        "low": quantile(stats, alpha),
        "high": quantile(stats, 1.0 - alpha),
        "confidence": confidence,
        "resamples": resamples,
        "samples": size,
    }


def speedup_ci(
    candidate: Sequence[float],
    baseline: Sequence[float],
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: float = 0.95,
    seed: int = 0,
) -> Dict[str, Optional[float]]:
    """Interval for ``baseline_median / candidate_median``, resampling both arms jointly.

    Resampling each arm independently ignores that they were measured in the same thermal
    state; when the two arms are interleaved the pairs are resampled jointly instead, so
    drift that moves both arms cancels.
    """
    cand = [float(value) for value in candidate]
    base = [float(value) for value in baseline]
    if not cand or not base:
        raise ValueError("speedup needs both arms")
    paired = min(len(cand), len(base)) == max(len(cand), len(base))
    rng = random.Random(seed)
    ratios: List[float] = []
    for _ in range(resamples):
        if paired:
            size = len(cand)
            indices = [rng.randrange(size) for _ in range(size)]
            cand_draw = [cand[index] for index in indices]
            base_draw = [base[index] for index in indices]
        else:
            cand_draw = [cand[rng.randrange(len(cand))] for _ in range(len(cand))]
            base_draw = [base[rng.randrange(len(base))] for _ in range(len(base))]
        cand_median = median(cand_draw)
        if cand_median <= 0:
            continue
        ratios.append(median(base_draw) / cand_median)
    if not ratios:
        return {"point": None, "low": None, "high": None, "confidence": confidence,
                "resamples": resamples, "paired": paired, "samples": min(len(cand), len(base))}
    alpha = (1.0 - confidence) / 2.0
    return {
        "point": median(base) / median(cand),
        "low": quantile(ratios, alpha),
        "high": quantile(ratios, 1.0 - alpha),
        "confidence": confidence,
        "resamples": resamples,
        "paired": paired,
        "samples": min(len(cand), len(base)),
    }


def aa_noise(samples_a: Sequence[float], samples_b: Sequence[float],
             seed: int = 0) -> Dict[str, float]:
    """The noise floor from an A/A test: the same kernel measured as two candidates.

    Returns the observed spread of the fake speedup ratio and the threshold a real candidate
    must beat. ``threshold`` is the upper end of the A/A ratio distribution, so an honest
    measurement of the same kernel is expected to fall below it.
    """
    ratio = speedup_ci(samples_a, samples_b, seed=seed)
    spread = abs((ratio["high"] or 1.0) - 1.0)
    return {
        "ratio_point": ratio["point"] or 1.0,
        "ratio_low": ratio["low"] or 1.0,
        "ratio_high": ratio["high"] or 1.0,
        "threshold": max(1.0 + spread, 1.0 + _relative_median_spread(samples_a, samples_b)),
        "relative_spread": _relative_median_spread(samples_a, samples_b),
    }


def _relative_median_spread(samples_a: Sequence[float], samples_b: Sequence[float]) -> float:
    a = median(samples_a)
    b = median(samples_b)
    if a <= 0 or b <= 0:
        return 0.0
    return abs(a - b) / max(a, b)


def wins(speedup: Dict[str, Optional[float]], noise_threshold: float,
         min_speedup: float = 1.0, min_samples: Optional[int] = None) -> bool:
    """The win rule: the lower confidence bound must clear the measured noise floor.

    A single sample or a wide interval fails, which is the point: the system would rather miss
    a real gain than bank a fake one.
    """
    low = speedup.get("low")
    point = speedup.get("point")
    if low is None or point is None:
        return False
    if (speedup.get("samples") or 0) < (MIN_SAMPLES if min_samples is None else min_samples):
        return False
    return low > max(noise_threshold, min_speedup)


def summarise(samples: Sequence[float], seed: int = 0) -> Dict[str, object]:
    """Median plus interval plus the summary a ledger entry records."""
    interval = bootstrap_ci(samples, seed=seed)
    return {
        "median_us": interval["point"],
        "ci_low_us": interval["low"],
        "ci_high_us": interval["high"],
        "samples": len(samples),
        "provisional": len(samples) < MIN_SAMPLES,
        "trimmed_mean_us": trimmed_mean(samples),
        "min_us": min(samples),
        "max_us": max(samples),
    }


def log_ratio_error(measured: Optional[float], predicted: Optional[float]) -> Optional[float]:
    """Prediction error as ``|log(measured / predicted)|``; ~0.2 is a 20 % miss."""
    if not measured or not predicted or measured <= 0 or predicted <= 0:
        return None
    return abs(math.log(measured / predicted))


def interleave(candidate: Iterable[float], baseline: Iterable[float]) -> List[Dict[str, object]]:
    """Fold two arms into the alternated order a timing run should use.

    Interleaving is what cancels thermal and frequency drift: a drift that slows both arms
    equally does not turn into a fake speedup.
    """
    cand = list(candidate)
    base = list(baseline)
    order: List[Dict[str, object]] = []
    for index in range(max(len(cand), len(base))):
        if index < len(cand):
            order.append({"arm": "candidate", "sample": index, "us": cand[index]})
        if index < len(base):
            order.append({"arm": "baseline", "sample": index, "us": base[index]})
    return order
