---
name: inspect-binary
description: Turn a compiled artefact plus a disassembly dump into a machine-level summary —
  instruction mix, register use per lane, scratchpad bytes per work-group, launch configuration
  and spills. Use after a build succeeds and before verify-mechanism needs machine-code evidence.
---

# inspect-binary

Script phase. A build produces a binary; this skill produces the *summary* a verifier can cite:
what the machine code actually contains, in canonical terms, with the toolchain kept outside the
skill.

## When it runs

- Step 6 of W2, on the candidate, so `verify-mechanism` can check group B (did the change reach
  machine code).
- After a calibration or toolchain change, to confirm the summary format still parses.

## The adapter point

The toolchain is not part of this skill. A device declares how to obtain a disassembly and how to
read it in an **adapter file**; this skill ships one device-neutral default and no vendor command.

An adapter declares two things:

1. `disassembly`: the argv template that produces a text dump, with `{artifact}` substituted.
   Empty means the caller supplies a dump directly.
2. `symbols`: named patterns over the dump text. Each pattern is a regular expression with one
   capture group holding a number, plus the canonical field it feeds.

The default adapter recognises a generic assembly grammar: mnemonics with an explicit storage
class and a size suffix. A device with different mnemonics supplies its own adapter file, and no
skill changes. The adapter's own `name` and `version` are echoed in the output so a summary is
always attributable.

## What you receive

One JSON object matching `contract/input.schema.json`: the compiled `artifact` path, optionally a
`disassembly_file` (a saved dump) and an `adapter_file` (the device's pattern set), plus the
`work_group` size and `lane_count` used for the launch, and `hw_id`.

## Steps

1. Resolve the adapter: the request's `adapter_file`, else the shipped default at
   `adapters/generic-text.yaml`. Done when the adapter loads and declares `name` and `symbols`.
2. Obtain the dump: read `disassembly_file` when given, otherwise run the adapter's `disassembly`
   argv. Done when a non-empty text dump is in hand; otherwise `status: unavailable` naming which
   of the two was missing.
3. Count instructions by class. Apply each adapter class pattern to every instruction line and get
   a histogram plus the total. Done when the histogram sums to the instruction total.
4. Extract the scalar facts the adapter names: registers per lane, scratchpad bytes per
   work-group, spill bytes, and the launch configuration. Done when each declared symbol was
   either matched or listed in `missing`.
5. Derive the launch configuration from the work-group size and the lane count, and record it
   beside whatever the dump says. Done when `work_group`, `lane_count` and the derived
   `work_groups` are all present.
6. Check the summary against `schemas/` conventions: every count non-negative, every byte field
   an integer. Done when `contract/output.schema.json` accepts it.

## Output

`{status, summary, problems, notes}` per `contract/output.schema.json`. `summary` carries
`adapter`, `adapter_name`, `instruction_total`, `instruction_mix` (class to count),
`registers_per_lane`, `scratchpad_bytes_per_workgroup`, `spill_bytes`, `launch`, `missing`, and the
`artifact` identity. `registers_per_lane` and `scratchpad_bytes_per_workgroup` are the same
canonical names the profiler reports, so a verifier can compare a static reading against a
measured one.

## Judging principles

- Describe what was extracted, not which tool extracted it. A summary that only makes sense with
  one vendor's disassembler is unusable on the next device, and the next device is the point.
- Report a symbol the adapter could not find in `missing`. An absent register count is unknown;
  writing zero would read as "this kernel uses no registers".
- Keep the counts and drop the dump. A verifier cites "wide loads appear 24 times", never a wall
  of disassembly, and the archive keeps the dump for an audit.
