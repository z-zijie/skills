# diagnose-bottleneck: what the signature means and what it does not

A signature is a class plus a tag set plus the numbers behind them. It is produced mechanically so
that two people, or two devices, reading the same counters reach the same name.

## The class

Decided from the two utilization numbers against this device's thresholds:

| Observation | Class |
| --- | --- |
| device-memory utilization high, compute low | `memory_bound` |
| compute utilization high, device memory low | `compute_bound` |
| both high | `mixed` |
| neither high | `latency_bound` |

When one utilization is missing, the class comes from whichever is present. When both are missing,
it comes from attainment. That fallback is legitimate and it is why `status` can be `provisional`:
a class derived from how far the kernel is from its bound is weaker evidence than one derived from
what the machine was doing.

## The tags

Each tag is one threshold decision from `signature.tags`, so a tag is only as trustworthy as the
counter behind it. The order in `primary_bottleneck` is deliberate: a specific tag (`plateau`,
`scratchpad_conflict`, `divergence_high`, `occupancy_low`, `uncoalesced`, `cache_hit_low`,
`mem_saturated`, `compute_saturated`, `launch_dominated`) beats a class tag, because a specific tag
names a mechanism and a class tag names a region.

`plateau` is the one structural tag: no counter produces it. The campaign supplies it after three
rounds without improvement, and it outranks everything, because "we have already tried the obvious
things for this class" is the most important fact at that moment.

## Attainment

`attained_pct = bound_us / measured_us`. The bound is `max(bytes / peak_bandwidth, flops /
peak_flops)`, with both peaks taken from the measured layer for this device. Read it with its
`limiting_term`:

- `device_mem` means the bandwidth term set the bound, so a change that reduces bytes or improves
  contiguity moves attainment.
- `compute` means the flop term set it, so a change that reduces instructions or widens the vector
  moves attainment.
- A bound below the measured launch overhead is not achievable at that shape, and the signature
  should be read as launch-dominated regardless of what the class says.

Attainment is the campaign's objective and the replay's primary metric. It is not a score for a
single experiment: an experiment's contribution is the change in attainment it produced.

## What the signature is not

- It is not a verdict on code quality. A kernel at 30% attainment may be at its algorithmic limit
  for the shape.
- It is not comparable across fingerprints. Two signatures from two runtimes are two measurements;
  the `env_fingerprint` field is what makes that checkable.
- It is not a hypothesis. It says where the kernel loses time; `propose-hypothesis` says what to
  change and predicts the counters.

## Reporting a provisional signature

Say which counters were missing and that the attribution is a class default. A hypothesis written
on a provisional signature predicts a mechanism nobody can measure, so the honest move is often to
run calibration first and re-diagnose, rather than to guess.
