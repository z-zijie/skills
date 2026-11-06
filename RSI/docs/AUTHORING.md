# Brief for skill, playbook and knowledge-base authors

Read this file, then `docs/vocabulary.md`, `docs/contracts.md` and
`_src/rsi-build-plan.md` (the authoritative plan) before writing anything.

## The one constraint that overrides all others

The framework will be used for **NPU operator performance work** as well as GPU work. So:

- Never name a vendor tool, vendor metric or vendor concept as the thing to do. `ncu`,
  `Nsight`, `nvcc`, `cuda`, `sm_`, `dram`, `l2`, `warp`, `thread`, `block`, `shared memory`,
  `tensor core`, `HBM`, `npu-smi`, `msopprof` may appear **only** in two places: a one-line
  "vendor mapping" note, or an adapter/example explicitly labelled as one device's
  instantiation.
- Use the canonical vocabulary everywhere else: `device_mem`, `onchip_cache`, `scratchpad`,
  `register_file`, `work_lane`, `work_group`, `wave`, `contiguous_flat_buffer` access,
  `tile`, `pipeline stage`.
- Name canonical metrics from `RSI/tooling/rsi/metrics.py`, never raw profiler expressions.
- Timings, throughputs and peak values come from the measured layer or the evaluator. A
  number written into prose is a lie waiting to happen; state the *relationship* instead.
- Say "the device", "the accelerator", "the target machine" — not "the GPU".

A reader on a GPU and a reader on an NPU must both be able to follow every step with only
their own tool substituted. If a sentence only makes sense on one device, rewrite it.

## Vocabulary quick card

| Use | Not |
| --- | --- |
| `device_mem` | HBM, GDDR, global memory, GM |
| `scratchpad` | shared memory, UB, L1 buffer |
| `onchip_cache` | L2, L1 cache, DCache |
| `work_group` | block, CTA |
| `work_lane` | thread |
| `wave` | warp, subcore group |
| `device_mem_throughput_pct` | `gpu__dram_throughput…`, `sm_throughput_pct` |
| `compute_throughput_pct` | `sm__throughput…`, cube ratio |
| `scratchpad_conflict_ratio` | bank conflicts |
| `occupancy_pct` | achieved occupancy |

## How skills are shaped here

- `SKILL.md`: frontmatter (`name`, `description`) plus a body under ~200 lines. The
  description says *when* the skill fires — the trigger branches, front-loaded.
- `contract/input.schema.json` and `contract/output.schema.json`: the frozen interface.
  Derive them from the shared schemas in `RSI/schemas/` by `$ref` where possible; a skill's
  contract narrows what it accepts and states exactly what it emits.
- `strategy/checklist.md` (and `strategy/examples/` where useful): the judgement text the
  outer loop is allowed to edit.
- `scripts/`: deterministic code. Where the logic already exists in `RSI/tooling/rsi/`,
  the script is a thin CLI over it — do not reimplement.
- `tests/`: labelled regression cases as data (`.json` or `.yaml`), plus a `run_tests.py`
  that replays them without needing a device.

Six skills are marked "Script": deterministic code with a one-page calling guide. The model
only invokes them and reads the result. Any judgement that can be written as code must not
be left to a model.

## What every skill body must contain

1. The trigger: when this runs, and what ran before it.
2. `## What you receive` — the input files, by name, matching `contract/input.schema.json`.
3. `## Steps` — numbered, each with a checkable completion criterion.
4. `## Output` — the exact fields, by `$ref` to `contract/output.schema.json`.
5. `## Judging principles` where the skill makes a judgement, phrased as positive
   instructions ("decide on evidence only", "answer not_implemented when evidence is
   missing") rather than as a list of bans.

Keep the body procedural. Long checklists, examples and tables go into `strategy/` and are
reached through a pointer that names the branch that needs them.

## Frontmatter

```
---
name: diagnose-bottleneck
description: Turn normalized profiler metrics into a bottleneck signature plus roofline
  attainment. Use after collect-profile has produced profile.json and before any hypothesis
  is written.
---
```

## Things that must NOT appear

- A skill calling another skill. `orchestrator/` sequences them.
- A model tier inside a skill. Tiers live in `orchestrator/models.yaml`.
- A tolerance, threshold or speedup number decided by a model. Those come from
  `operators/<op>/spec.yaml`, `kb/hardware/measured/`, or the evaluator.
- Any instruction that weakens Layer 0 (editing the evaluator, changing tolerances, reading
  the holdout split, writing the ledger directly).
- Emoji, decorative formatting, or prose that restates a schema field by field.
