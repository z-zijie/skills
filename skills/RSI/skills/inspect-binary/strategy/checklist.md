# inspect-binary: the adapter contract and what a summary is evidence for

A verifier's group B asks one question: did the change reach machine code? That question is only
answerable if the summary describes the machine code and not the tool that printed it.

## Where the toolchain lives

Not here. A device supplies an adapter file with four blocks:

```yaml
name: <adapter id>
version: "<n>"
line:      { instruction: <regex with named groups>, skip_prefixes: [...], skip_mnemonics: [...] }
disassembly: { command: [<argv with {artifact}>] }
symbols:   { registers_per_lane: {pattern: ...}, scratchpad_bytes_per_workgroup: {...}, ... }
instruction_classes: [ {name: load_wide, pattern: ..., role: device_mem_read}, ... ]
```

The shipped `adapters/generic-text.yaml` is device-neutral: it recognises an explicit storage class
with a width suffix, and it declares no disassembly command on purpose, so this skill cannot
pretend a toolchain exists on a machine that has none. A device replaces the file; no skill text
changes and no script branches.

The one mapping note that belongs in the adapter: a wrapper for a specific accelerator puts that
vendor's mnemonics in the same `pattern` keys. That sentence is the whole vendor surface.

## What the summary is evidence for

| Claim on a hypothesis card | Field in the summary that checks it |
| --- | --- |
| "loads got wider" | `instruction_mix.load_wide` rose and `load_narrow` fell |
| "the loop now fuses multiply and add" | `instruction_mix.fma` rose while `compute` fell |
| "scratchpad tiling was added" | `scratchpad_load` and `scratchpad_store` appear; `scratchpad_bytes_per_workgroup` rises |
| "register pressure went up" | `registers_per_lane` rises, and `spill_bytes` may become non-zero |
| "the launch configuration matches the card" | `launch.work_group`, `launch.work_groups` |
| "one fewer barrier per tile" | `barrier_count` falls |

The `before` and `after` variants of the same artefact are what make this a comparison. A feature
present in the baseline summary means the compiler was already doing it, and a change that claims
credit for it is void. That is why `variant` is on the input and echoed in the summary.

## Unknowns

`missing` lists symbols the adapter declared that the dump did not yield. A verifier treats those
as unknown and says so; it does not read them as zero. `registers_per_lane: 0` would mean a kernel
with no registers, which is a different and false claim.

A work-group count computed from the work-group size and the problem size is not missing: it is
derived, and `launch.source` says which it is. A verifier comparing a card that names a launch
configuration to a derived value is comparing against arithmetic, which is legitimate; comparing
it to a dump value is comparing against the toolchain, which is stronger.

## Static versus measured

`registers_per_lane` and `scratchpad_bytes_per_workgroup` here are static: what the binary
declares. The profiler reports measured values under the same names. They can differ, and the
difference is itself informative: a static allocation that the profiler never uses points at dead
scratchpad, while a measured figure above the static one points at an adapter mapping problem.

## What this skill never does

- It never concludes that a mechanism was implemented. It produces the evidence; the verifier
  weighs it.
- It never runs a build. A missing artefact is reported, not produced.
- It never archives the raw dump inside the summary. The dump goes to
  `ledger/artifacts/<exp_id>/`, and the summary cites counts.
