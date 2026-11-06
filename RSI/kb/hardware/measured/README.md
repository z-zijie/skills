# Measured hardware layer

Parameters this machine actually measured: peak bandwidth, peak compute per dtype, the A/A noise
floor, launch overhead, cache steps, the coalescing penalty. Written only by the calibration
workflow inside the evaluator (`tools/rsi-cli calibrate`); read by everything else.

**One file per hardware id**, named `<hw_id>.yaml`, plus `device.yaml` describing the local device
and `thresholds.yaml` for signature thresholds.

## Why nothing is committed here

A measured number describes one machine in one environment. A checked-in calibration would be
inherited by every other machine that lacks its own `device.yaml`, and a noise floor measured on a
laptop would silently become the win threshold on an accelerator — which makes every campaign on
it either wildly permissive or unable to report any gain at all.

So the directory ships with this README and nothing else. The first thing a deployment does is
`tools/rsi-cli calibrate`, and until then the roofline refuses to compute rather than inventing a
bound. `trustworthy_for_roofline: false` marks a run that measured the host instead of the
declared accelerator; the roofline refuses those peaks too.

## What a file contains

| Field | Meaning |
| --- | --- |
| `peak_bandwidth_gbps` | measured device-memory bandwidth |
| `peak_flops` | measured compute per dtype, in **FLOP/s** (the unit the roofline divides by) |
| `noise_floor.threshold` | the A/A ratio a real candidate must beat |
| `launch.overhead_us` | fixed launch cost |
| `counter_variation` | per-counter run-to-run jitter, which the mechanism verifier uses as its band |
| `env_fingerprint` | the environment these numbers describe |
| `measured_at` | when |
| `history` | previous runs and what moved, so a re-measure after an upgrade is comparable |
| `trustworthy_for_roofline` | `false` when the numbers describe the host rather than the device |
