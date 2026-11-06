# Quickstart on a new machine

From a bare clone to a working loop, in the order that matters. Each step ends with something
you can check; skip one and the later steps measure nothing.

```sh
cd RSI
```

## 1. Does this machine have what the framework needs?

```sh
tools/rsi-cli doctor
```

It reports the device description, whether a signing key exists, which backends can actually
run, and whether the measured layer has any parameters yet. On a fresh clone the signing key and
the measured layer are the two expected gaps.

```sh
tools/rsi-cli keygen
```

Create the key **as the evaluator's user**, not as an agent's account. It is created with mode
0600 under `.rsi/`, which is in `.gitignore`. Replacing an existing key invalidates every result
already signed, which is why `--force` is required and why the command says so.

## 2. Describe the device

Create `kb/hardware/measured/device.yaml`. Without it the framework runs against the reference
backend and says so, rather than inventing peak numbers:

```yaml
hw_id: accel-a
hw_class: npu              # informational only
model: <the part's model string>
runtime: <driver or runtime version>
driver: <driver version>
compiler: <compiler and version>
backend: portable          # or cpu
array_module: torch_npu    # the array runtime the portable backend should drive
device: npu:0
profiler: msopprof         # or ncu, or null
```

`docs/moving-to-a-new-device.md` explains each field and what else a new device needs.

## 3. Measure it

```sh
tools/rsi-cli calibrate --suite core
```

This writes `kb/hardware/measured/<hw_id>.yaml`: peak bandwidth, peak compute per dtype, the A/A
noise floor, launch overhead, the cache steps and the coalescing penalty. The roofline refuses to
compute without a measured peak, because a bound built on a documented peak is a bound you will
never reach for reasons that have nothing to do with your kernel.

Two checks before moving on:

- The peaks should be in the right order of magnitude for the part. A number that is off by 10x
  is a unit or a granularity mistake, and every attainment figure downstream inherits it.
- The noise floor should be small. If it is wide, the device is shared or thermally unstable, and
  every campaign on it needs a bigger margin before anything counts as a gain.

## 4. Prove the evaluator catches cheating

```sh
tools/rsi-cli redteam --op vec_add
```

Six deliberately cheating kernels, and every one must be caught. This is the cheapest test in the
system and the most important: everything else is built on the assumption that the evaluator
cannot be fooled.

## 5. Onboard your own operator

```sh
cp -r templates/operator operators/<op>
```

Fill in `spec.yaml` (tolerance, shapes, byte and flop formulas), write the reference and the
baseline, add deliberately cheating kernels for *that* operator, and confirm they are caught. W1
in `docs/workflows.md` is the checklist. Done when two independent evaluations of the baseline
differ by less than the measured noise floor.

Pick an operator you have already optimized by hand: you know the answer, so you can check the
system against it instead of the other way round.

## 6. Run a campaign

```sh
tools/rsi-cli campaign --op <op> --budget 20
```

The state machine reports the next step and the skill that runs it. Drive it forward by passing
each step's outcome back:

```sh
tools/rsi-cli campaign --op <op> --outcome outcome.json
```

`templates/campaign/outcome.json` shows the shape.

## 7. Check the whole scaffold

```sh
python3 tools/demo.py
```

Nine steps covering calibration, the red-team check, correctness, signature refusal, signatures,
retrieval, the ledger, the state machine and the test suite. On a machine with no accelerator it
runs against the reference backend, which is the point: the machinery is verifiable before a
device is involved.
