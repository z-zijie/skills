# Moving to a new device

Only three parts of this system are device-specific: the profiler adapter inside the evaluator,
the hardware knowledge base, and the pattern library's device-bound entries. Everything else
moves unchanged. That is the design, and this page is the checklist that makes it true.

The whole framework is written against the canonical vocabulary in `vocabulary.md`, so "moving
to a new device" is filling in the four adapters below, not rewriting any workflow.

## 1. Describe the machine

Create `kb/hardware/measured/device.yaml`:

```yaml
hw_id: accel-a            # stable local id; rules bind to it
hw_class: npu             # informational only: no logic branches on it
model: <the part's model string>
runtime: <driver or runtime version>
driver: <driver version>
compiler: <compiler and version>
compiler_flags: <default flags>
backend: portable         # or cpu, or a backend you add under evaluator/backends/
array_module: torch_npu   # which array runtime the portable backend should drive
device: npu:0             # passed to that runtime unchanged
profiler: msopprof        # or ncu, or null
```

`backend` and `profiler` are names, resolved through registries. Adding a device normally means
adding no code: the portable backend drives any array runtime that exposes the NumPy array API,
which covers both GPU and NPU runtimes on most stacks.

## 2. Create the evaluator's key

```sh
tools/rsi-cli keygen
```

Once, as the evaluator's user. Every result is signed with it, and the ledger accepts nothing
unsigned.

## 3. Measure it

```sh
tools/rsi-cli calibrate --suite core
```

This writes `kb/hardware/measured/<hw_id>.yaml` with the peak bandwidth, peak compute per dtype,
the A/A noise floor, the launch overhead, the cache steps, the coalescing penalty and the rest.
Nothing downstream may assume a peak: the roofline reads this file, and without it the roofline
refuses to compute rather than inventing a bound.

## 4. Map the profiler

If the device has a profiler that is not yet mapped, add one adapter file under
`evaluator/profiler/adapters/` following `README.md` in `evaluator/backends/`. It declares, for
each canonical metric, the expression that profiler needs, and records which metrics it genuinely
cannot produce. A metric the adapter cannot produce stays unavailable; it never becomes zero.

Two adapters ship as examples of the shape: one for a GPU profiler and one for an NPU profiler.
Neither is the framework; both are one device's instantiation.

## 5. Seed the documented layer

Write `kb/hardware/documented/` entries for anything specific to this part that changes what an
optimization means: cache capacities, scratchpad banking, transaction granularity, instruction
throughput quirks. Every entry cites its source, and any counter known to misread is recorded
here so the verifier cross-checks it.

## 6. Re-tune the thresholds

Signature tags are threshold decisions over canonical metrics, and thresholds live in
`kb/hardware/measured/thresholds.yaml`. Start from the defaults, then override what this device's
calibration says is different. A tag that fires on every kernel on this device is a threshold
that has not been calibrated.

## 7. Re-baseline

Run `evaluate` and `profile` on every operator's baseline, record them in the ledger, and confirm
that two independent evaluations differ by less than the measured noise floor. Until that holds,
nothing this device measures is evidence.

## Rules that bind to a hardware id

A rule records the hardware ids it applies to. After a device change, the rules bound to the old
id are demoted to `candidate` and re-verified rather than silently reused. `tooling/rsi/knowledge.py`
implements the demotion; the workflow is in `workflows.md` W4.

## What must not change

The evaluator's interface, the skill contracts, the tolerance mechanism, the holdout split, and
the canonical vocabulary. Those are what let a rule learned on one device still be readable on
another — and, more importantly, what lets a human compare the two.
