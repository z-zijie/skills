# calibrate-hardware: what a measurement means and when it expires

## What the suite produces

Each microbenchmark yields one parameter and the curve that produced it. The parameters the rest of
the system consumes are:

| Parameter | What consumes it |
| --- | --- |
| `peak_bandwidth_gbps` | the bandwidth term of the roofline |
| `peak_flops[dtype]` | the compute term of the roofline |
| `noise_floor.threshold` | the evaluator's win rule, and the minimum reportable speedup |
| `launch.overhead_us` | the floor for small shapes; whether merging kernels can pay |
| `cache` steps | choosing tile sizes |
| `coalescing` curve | estimating the gain from a layout change |
| `drift` | the warm-up length the timing protocol uses |

A parameter that is not measured stays absent. The roofline then reports that it cannot compute a
bound, and the hypothesizer predicts a direction instead of a magnitude. A zero in this file would
make the roofline produce a bound of zero, which reads as infinite attainment.

## Why every entry carries a fingerprint and a date

Floating-point behaviour and scheduling change with the driver, the runtime and the compiler flags.
Two measurements from two fingerprints are two measurements, not a comparison. So:

- `env_fingerprint` is stored with every measurement, and the ledger entry of every experiment
  carries it too, which is what makes a past speedup interpretable.
- A run under a new fingerprint compares nothing: the skill says the fingerprint changed instead of
  reporting a delta.
- A parameter that moves beyond its own change threshold sets `under_review`, and the rules that
  depend on it go under review through a proposal. The skill flags; it does not rewrite rule text.

The change thresholds belong to the calibration protocol inside the evaluator, which records which
parameters moved in `history[-1].moved`. This skill reads that decision rather than re-deciding it
with numbers of its own, so "moved" has exactly one definition in the system.

## Thresholds derived from the curves

The tag set needs thresholds, and hand-tuned numbers do not travel to a new device. So the skill
derives the ones a measurement can support and leaves the rest at the device-neutral default:

- `min_reportable_speedup` from the measured noise floor: a device with a wider floor needs a
  bigger margin before a gain is called.
- `uncoalesced_sectors` from the coalescing curve, and only when the worst stride costs enough to
  be worth a tag.

Everything else — the utilization bands, the occupancy floor, the divergence band — keeps the
default until a device's own curves justify moving it. A threshold file that changes without a
measurement behind it is a scaffold change, and it goes through replay like any other.

## The three rules of use

- Microbenchmark code belongs to the evaluator. A new microbenchmark is proposed by
  `design-microbench` and added after a human approves it. This skill runs what exists.
- Prediction error drives new measurements. When a campaign's prediction errors cluster, the
  hardware model has a hole there, and that is a calibration problem before it is a hypothesis
  problem.
- Results expire. A driver upgrade, a runtime upgrade or a device swap invalidates the measured
  layer, and the monthly routine exists so the invalidation is noticed early.

## After a calibration

Re-run each operator's `best/`. A parameter that moved means the same code may now attain a
different fraction of the bound, and an apparent regression after a calibration is a real signal
about the environment rather than about the code.
