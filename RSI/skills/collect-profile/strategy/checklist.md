# collect-profile: what the reconciliation decides

The profile is the only description of the machine state the diagnoser ever sees, so the
mechanical part of this skill is deciding which names survive and what "missing" means.

## Name resolution, in order

1. A name the registry knows is kept under its canonical spelling.
2. A legacy or vendor spelling the registry aliases is rewritten, and the rewrite is recorded as
   a note. Old profiles stay readable without any skill learning the old name.
3. A name the registry does not know is moved out of `metrics` into `unavailable` with a note.
   It is not evidence: no rule can be scoped to a counter that has no unit and no direction, and
   no verifier can compare it across two runs without knowing what it measures.

Adding a genuinely new metric is a change to `docs/vocabulary.md` plus an adapter mapping, made
once by a human, after which every skill sees it.

## Availability

`metrics` and `unavailable` partition the canonical registry. A metric that was neither produced
nor declared is listed as unavailable, never as zero. The difference matters downstream:

- `device_mem_throughput_pct: 0` says the kernel does not touch device memory.
- Absence says this device or profiler cannot tell you.

A hypothesis that names an unavailable metric is rejected by `propose-hypothesis`, and a
verifier refuses to confirm a mechanism whose named counter is unavailable. Both depend on this
partition being honest here.

## Device neutrality of the adapter

An adapter maps canonical names to whatever the installed profiler reports, and records its own
name and version in the profile. This skill never knows which profiler ran: it receives canonical
names or legacy spellings and nothing else. When a device exposes a counter with no canonical
counterpart yet, the metric is added to the vocabulary first, together with its adapter mapping,
and only then used by a skill.

The null adapter is a real adapter: it produces a complete profile in which every metric is
unavailable. That keeps the pipeline exercisable on a machine with no profiler, and it keeps the
diagnoser honest, since it must then fall back to class and timing rather than to counters.

## Transaction granularity

`sectors_per_request` is only interpretable next to `transaction_granularity_bytes`, because the
denominator is a device property. The profile carries it so that a signature with
`sectors_per_request` 1.4 on one device and 1.4 on another is not read as the same thing.

## What this skill never does

- It never edits the adapter, the profiler, or the profiler's flags.
- It never turns an unavailable metric into a number.
- It never treats a profile's wall time as performance data: profiling and timing are separate
  runs, and the timing number comes from `run-evaluator`.
