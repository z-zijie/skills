# Pipeline waveform from raw CANN data

`msopprof`'s CSV + `visualize_data.bin` path (`kernel_perf.py`) answers *how
much* of the kernel went to each pipe. It cannot answer *when*, or *in what
order* -- and "the pipes are at 40% each" reads very differently depending on
whether they overlap or take turns.

The pipeline waveform answers the ordering question, and it does so from the one
artefact the vendor does not publish a spec for: the per-cycle pipe stream that
the profiler dumps as `dump/timeline.bin.0`. `scripts/pipeline_waveform.py`
decodes it.

## Collect

```bash
source $ASCEND_HOME_PATH/set_env.sh        # npu-compute/msprof are not on the login PATH

# PipelineTimeline is what writes dump/timeline.bin.0. Adding PipeUtilization
# costs nothing and gives the step-3 cross-check.
msprof op --aic-metrics=PipeTimeline,PipeUtilization --warm-up=3 --output ./prof ./your_app
```

`--output` creates `./prof/OPPROF_<timestamp>_<id>/`. Keep the whole directory:
the decoder needs `dump/timeline.bin.0`, and the cross-check needs `trace.json`.

> **Use `msprof op`, not the bare `msopprof` binary.** Both take the same flags
> and both write a `trace.json`, but only the `msprof op` wrapper stages the raw
> stream into `device0/.../dump/timeline.bin.0`. Calling
> `tools/msopprof/bin/msopprof` directly produces a capture with every parsed
> file and **no raw stream at all** -- measured on the same binary and kernel:
> direct call, 11 files, zero `timeline.bin.0`; `msprof op`, 12 files including
> the 25 MB raw stream. There is no flag that turns it back on. `kernel_perf.py`
> routes its `PipeTimeline` pass through `msprof op` for this reason.

| File | Size (vector_add) | What it is |
|---|---:|---|
| `dump/timeline.bin.0` | 25 MB | **the raw per-cycle pipe stream** -- the waveform |
| `trace.json` | 8 KB | the vendor's own decode of it (sampled and clipped) |
| `dump/op_basic_info.txt` | 153 B | kernel name, block dim, clock |
| `PipeUtilization.csv` | 10 KB | per-block pipe time, for the cross-check |

> `trace.json` is not the waveform. Every `dur` in it is a whole number of
> 1650 MHz cycles because it is already parsed, and it keeps only the first
> sampling group per core. The raw stream is the source; `trace.json` is the
> evidence used to verify the decoder.

## Decode

```bash
# report + machine-readable numbers
python3 scripts/pipeline_waveform.py --capture ./prof/OPPROF_xxx --out ./waveform

# one core only (name, number, or kind -- all accepted)
python3 scripts/pipeline_waveform.py --capture ./prof/OPPROF_xxx --core core4.veccore0
python3 scripts/pipeline_waveform.py --capture ./prof/OPPROF_xxx --core 4 --core 5
python3 scripts/pipeline_waveform.py --capture ./prof/OPPROF_xxx --core aic

# verify the decoder against the vendor's own parse of the same run
python3 scripts/pipeline_waveform.py --capture ./prof/OPPROF_xxx --verify

# Chrome/Perfetto trace, all cores on one axis
python3 scripts/pipeline_waveform.py --capture ./prof/OPPROF_xxx --chrome trace.json
```

Add `--out` to write `waveform.md` and `waveform.json`; without it the report
goes to stdout. `--json` prints the full decode, so another tool can consume it
without re-parsing markdown.

## What the capability can and cannot do

Answers to the questions people actually ask, with the limits attached.

### Can I look at one specific core?

Yes. `--core` accepts the vendor's name (`core4.veccore0`), a bare number
(`4`, which selects every subcore of that core), or a kind (`aic`/`aiv`), and
repeats. This is a **view filter, not a collection setting**: the stream always
holds every core the profiler sampled, so the same capture answers "what did
core 4 do" without re-running the kernel.

You cannot ask the profiler to sample a core it did not choose. `--core-id` is
rejected (`requires --aic-metrics=TimelineDetail`) and TimelineDetail fails on
dav-3510. Which cores get sampled is up to the instrumentation, and it varies:
captures on this machine have come back with 3, 5 and 6 cores.

### How long a wave can I see?

Not a duration you choose — the profiler decides. What you get is a series of
**sampling windows**, each a few microseconds long, separated by a gap. Measured
on this machine:

| Kernel | Windows per core | Window span | Total observed | Share of kernel |
|---|---:|---:|---:|---:|
| vector_add, 4.2 us | 2 | ~2.5 us | ~5 us | window sum exceeds the kernel, so coverage is not computable |
| long kernel, 22.4 ms | 30 | ~3.3 us | 100 us | **0.45%** |

So the honest answer is: **you see a sparse strobe, not a continuous recording.**
Short kernels are covered well enough to characterise; a long kernel is sampled
at well under one percent. There is no flag to widen the window or raise the
window count — the cadence is the hardware's.

### Can I control the sampling precision?

No. Every candidate knob was measured and none of them change the waveform:

| Knob | Effect on the waveform |
|---|---|
| `--warm-up` (0 vs 3) | none — identical record and window counts |
| `--launch-count 2` | none — still 2 windows per record, so a window is not a launch |
| `--launch-skip-before-match 1` | produces an **empty** capture (it skips the only launch) |
| `--core-id` | rejected outright on this part |

What you *can* control is the analysis: which cores to include (`--core`), the
clock used for unit conversion (`--freq`), and the output format (`--json`,
`--chrome`, `--out`).

### How is it normally used?

1. **Collect** with `--with-timeline` (or `msprof op --aic-metrics=PipeTimeline`).
2. **Read the verdict, not the table.** The serial-vs-overlapped section answers
   the question the CSV path cannot: two pipes can hold identical busy totals
   whether they ran together or took turns.
3. **Only then** decide what to change. If the verdict is "never overlap", tiling
   work is wasted and double buffering is the fix.
4. **Re-collect and compare shapes.** The state-sequence fingerprint is stable
   across runs where the percentages are not, so it is the better A/B signal.

### What can it do?

- Per-pipe busy share per core, pooled over every window, **with the spread**
  across windows so a number cannot be quoted more precisely than it is known
- Idle/bubble intervals with duration and position (the CSV path cannot express
  "no pipe was busy")
- Overlap between any two pipes — the ordering evidence
- Per-core span, window count, longest bubble
- A Chrome/Perfetto trace of every window, one lane per (core, pipe)

### What extra information does it carry?

Beyond the report, `--json` and `waveform.json` expose: per-pipe `pct`, `pct_sd`,
`pct_min`, `pct_max`, `windows`; per-core `span_cycles`, `observed_cycles`,
`idle_cycles`, `idle_pct_sd`, `interval_count`; and `unknown_masks` for pipe bits
the decoder has not verified. The raw stream also contains `0x0d99`-`0x0dff`
counters and, under `InstrTimeline`, an instruction-level token vocabulary —
both preserved but **not decoded**, and reported as such.

## The container format

CANN publishes no specification for `timeline.bin.0`. The layout below was
recovered by decoding captures and checking the result against the vendor's
`trace.json` and `PipeUtilization.csv` for the same runs; the checks are
reproducible with `--verify`.

The file is a fixed-stride array of records:

```
stride = 524290 words = 2 097 160 bytes

record:  word[0]  id    = 0x000V_CCCC     V = subcore + 1, CCCC = core index
         word[1]  blen  = payload bytes
         word[2:] payload = blen/4 words, zero-padded to the stride
```

An AIV record is `0x0001_CCCC` (veccore0) or `0x0002_CCCC` (veccore1); an AIC
record is `0x0000_CCCC`.

Each payload word is `tag:16 | value:16`, tag in the high half:

| tag | meaning |
|---|---|
| `0xe0xx`-`0xe3xx` | record header (op id, block dim, clock); carries no time |
| `0xf0xx` | timed token: `value` = cycles |
| `0x0dxx` | counter block; also ends a sampling group |

### Timing convention

For a timed token, the low byte of the tag is the pipe bitmask that is busy, and
**the run it describes is the one *after* it** -- the duration is carried by the
next timed token. Put the other way: token *k*'s own value is the time that had
already elapsed before its state took effect.

This one detail is worth stating plainly because it is not guessable. Reading
mask and duration from the same token also produces a plausible timeline, but it
shifts every interval by one and disagrees with the vendor's parse. The pairing
that reproduces the vendor *exactly* is the lookahead one.

The very first timed token's value is the lead-in before any state is recorded;
it must still advance the clock, or the whole timeline shifts left.

Mask `0x00` means "no pipe busy" and is emitted as a real interval. Those are
the bubbles.

Bit assignments, by core kind:

| bit | AIV | AIC |
|---|---|---|
| `0x01` | SCALAR | SCALAR |
| `0x02` | VECTOR | -- |
| `0x04` | -- | CUBE |
| `0x08` | -- | MTE1 |
| `0x10` | MTE2 | MTE2 |
| `0x20` | MTE3 | -- |
| `0x40` | -- | FIXP |

The decoder carries a per-kind list of bits it has verified (`AIV_CONFIDENT`,
`AIC_CONFIDENT`). A mask outside that list is reported as an *unnamed* state
with its cycle count rather than being given a name it has not earned.

### Sampling groups

A `0x0dxx` counter block ends the current group. Everything after it is a
separate sample of the same core, starting again from cycle 0, with a different
counter block (the first group carries `0xd88`, later ones `0xd99` onward).

**Every group is a sample, and all of them are used.** They are not a continuous
timeline -- each restarts at cycle 0 -- so they must not be concatenated into one
span, but pooling them is what makes the occupancy numbers trustworthy. On a
30-window capture, group 0 alone reports SCALAR at 24.1% while the figure across
all windows is **40.2%**. Dropping 29 of 30 windows is not a rounding difference,
it is a different answer.

The report therefore averages each pipe's **per-window ratio** across all windows
and cores, and prints the standard deviation across windows next to it. That
spread is the honest uncertainty: a pipe at 40% +/- 20 and one at 40% +/- 2
support very different optimisation decisions.

`--verify` picks its comparison mode from the vendor's own span: if the vendor
trace covers more than the first window it has spliced all of them, so the
concatenation is compared; otherwise the first window is. The mode used is
reported.

## Verification

`--verify` returns one of three verdicts, and the difference matters:

| verdict | meaning | exit code |
|---|---|---|
| `VERIFIED` | every pipe interval matched the vendor exactly | 0 |
| `NOT VERIFIED` | the comparison was possible and disagrees -- a real finding | 1 |
| `INCONCLUSIVE` | the comparison cannot be made (no trace, or the vendor trace is instruction-level rather than pipe-level) | 2 |

`INCONCLUSIVE` is not a failure. An earlier version collapsed it into "failed"
and so reported 11 of 17 captures as broken when the vendor trace simply used a
different vocabulary (`GET_BUF`/`VF`). A check that cries wolf is worse than no
check, because it teaches people to ignore it.

Measured on CANN 9.3.0 / Ascend950PR_9579 / dav-3510, 1650 MHz:

| Capture | Kernel | Records | Checks | Result |
|---|---|---:|---:|---|
| vector_add | `add_kernel`, 56 blocks, AIV | 12 | 48 | pass |
| vector_add (2nd run) | `add_kernel`, 56 blocks, AIV | 4 | 16 | pass |
| matmul 1024 | `MatmulKernel`, 28 blocks, AIC | 6 | 20 | pass |
| matmul 128 | `MatmulKernel`, 28 blocks, AIC | 6 | 10 | pass |
| end-to-end (fresh) | `add_kernel` via `profile --with-timeline` | 12 | 48 | pass |

Both core kinds, small and large kernels, including one captured by the
collector itself rather than by hand.

Over the wider 17-capture corpus in `/workspace/kperf` the verdicts split as:
**5 VERIFIED, 11 INCONCLUSIVE** (instruction-level vendor traces, comparison not
applicable), **1 NOT VERIFIED** (a 30-window long kernel whose all-windows splice
still drifts ~16 cycles -- a genuine open item and the only known disagreement).

The check is only worth what a failure it can catch is worth, so it is tested
both ways in `selftest.py`: a `trace.json` that disagrees must yield
`NOT VERIFIED`, a matching one must yield `VERIFIED`, and an instruction-level
one must yield `INCONCLUSIVE` rather than either. A plain mirror of the decoder
would pass the first and fail the other two.

A third check is possible against `PipeUtilization.csv`, which the profiler
produces through a different path. On the vector kernel it agrees to the CSV's
own 3-decimal rounding:

| Core | Pipe | Decoded cycles | CSV us x 1650 |
|---|---|---:|---:|
| `core4.veccore0` | SCALAR | 1463 | 1463 |
| `core4.veccore0` | MTE2 | 2326 | 2326 |
| `core4.veccore0` | VECTOR | 263 | 263 |
| `core4.veccore0` | MTE3 | 494 | 494 |

**It does not agree on the cube kernel, and that disagreement is in the vendor's
own files, not in this decoder.** For the same `MatmulKernel` run, `trace.json`
and `PipeUtilization.csv` contradict each other on four of the five AIC pipes:

| Pipe | `trace.json` cycles | `PipeUtilization.csv` x 1650 |
|---|---:|---:|
| CUBE | 646 | 512 |
| FIXP | 441 | 633 |
| MTE1 | 283 | 284 |
| MTE2 | 842 | 1307 |
| SCALAR | 971 | 683 |

Since both files come from the vendor and describe the same launch, this tool
cannot be validated against both. It is validated against `trace.json`, because
that is the file decoded from the *same* sampled records as the raw stream; the
CSV is an independent aggregation whose cube-side numbers evidently come from a
different pass. Treat a cube-kernel cycle count as tier C and prefer
`trace.json`/the raw decode when the two disagree, or re-collect.

Only one block in that CSV carries non-zero AIC time (block 0); the other 27
report zeros. The sample is a single core, which is why the CSV totals cannot be
matched block-for-block against a multi-core decode in the first place.

## Reading the result

Three questions, in this order:

1. **Is the kernel ever idle?** `idle_pct` per core. Idle means *no* pipe was
   busy -- the core was waiting on something outside the pipes (a dependency, a
   sync, a launch gap). High idle and low pipe shares together point away from
   the pipes entirely.
2. **Do the pipes overlap?** Memory and compute overlapping is what double
   buffering buys. Zero overlap between MTE2 and the compute pipe means they
   serialise, and no amount of tiling width will help until that changes.
3. **Which pipe is the long pole?** The busiest pipe's share, read next to its
   `wait` ratio from `ResourceConflictRatio.csv`. A pipe that is busy and
   waiting is blocked; a pipe that is busy and not waiting is at its limit.

On `add_kernel` (56 blocks, vector): mean idle 2.0%, MTE2 30.9%, SCALAR 34.9%,
VECTOR 7.4%, MTE3 11.6%. SCALAR outweighs VECTOR by 4.7x -- the kernel spends
more time on address arithmetic and loop control than on arithmetic, which is
the signature of too few elements per iteration rather than a memory problem.

On `MatmulKernel` (28 blocks, cube): CUBE 47.8%, MTE2 39.5%, MTE1 11.8%,
SCALAR 5.7%, FIXP 3.4%, with CUBE and MTE2 overlapping across the whole body.
That is a healthy double-buffered matmul: the cube is the long pole, memory
feeds it, and the flame graph shows MTE1 loading L1 just ahead of each MMAD.

## Limits

- **Per-core sampling, not a chip-wide trace.** The profiler instruments a
  subset of cores per collection and the sampling groups are short. A 56-block
  kernel yields 12 veccore records; the span is the sampled record's own, not
  the kernel's wall-clock duration. Do not add per-core spans together to
  estimate kernel time.
- **AIC bit assignment is inferred**, not documented. It is verified for the
  five pipes seen on `MatmulKernel` against `trace.json`; a sixth pipe on
  another operator would show up as an unnamed mask rather than a wrong label.
- **`0x0dxx` counters are not decoded.** They are preserved in
  `waveform.json` under `counters` but this tool does not claim to know what
  they mean.
- **`timeline.bin.0` is large** (25 MB for a 4 us vector kernel, nearly all zero
  padding). It is per-collection, so prune old `OPPROF_*` directories.

## What this adds over `trace.json`

Same source data, three things the vendor's parse does not give you:

| | `trace.json` | this decoder |
|---|---|---|
| Sampling groups | first only, and clipped | every group, complete |
| Idle / bubbles | not emitted | explicit intervals with duration |
| Time base | microseconds, rounded | integer cycles |

`npu-compute --section Pipeline` produces a similar Chrome trace, but its
`.npu-rep` container holds **no raw stream at all** -- only parsed files. When
you need to re-derive or question a number, the raw stream is the only thing
that can answer.
