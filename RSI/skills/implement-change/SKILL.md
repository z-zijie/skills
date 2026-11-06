---
name: implement-change
description: Edit the kernel for exactly one hypothesis card and make it compile, or return a
  diff explaining why it could not. Use at W2 step 4 and on each retry after a correctness
  failure or a not_implemented verdict.
---

# implement-change

One card in, one diff out. The work is bounded on purpose: a second change smuggled into the same
diff makes a confirmed mechanism unattributable, and an unattributable confirmation cannot become
evidence for a rule.

## When it runs

- Step 4 of W2, after `propose-hypothesis` has ranked the cards.
- On the retry from step 5 (correctness failure) and step 6 (`not_implemented`), at most twice for
  each, so the card is retried with a new attempt at the same mechanism.

## What you receive

One JSON object matching `contract/input.schema.json`: the `card`, the `workspace` directory and
the `entry` file inside it, the `build` command when the operator compiles, the `retry` block
carrying either the failing cases or the missing implementation evidence, and `parent_code`.

## Steps

1. Read the card's `scope_of_change` and `evidence_of_implementation` before opening the code. Done
   when you can name the lines the change must touch.
2. Read `parent_code` and locate the hot path the mechanism applies to. Done when the code you are
   about to change is the code that runs for the shapes in the spec.
3. Make the smallest edit that implements the mechanism. Done when the diff touches only the
   files the scope names, and every added line serves the mechanism.
4. Compile. Done when the build command exits zero; when the operator has no build step, done when
   the module imports.
5. Check the card's own evidence statements against the diff. Done when each statement in
   `evidence_of_implementation` is either visible in the diff or plausible in the disassembly the
   build would produce; when one is not achievable, report it rather than rewriting the card.
6. Emit the diff and the list of files touched. Done when the diff applies cleanly to
   `parent_code` and the file list matches the diff.

## On retry

- A correctness failure names the failing shapes. Find the case the change breaks: usually a tail,
  an edge shape or a boundary. Fix the mechanism's coverage, not the tolerance.
- A `not_implemented` verdict names missing evidence. That means the change did not reach the code
  path the card claims, so make the mechanism visible in the diff and in the machine code, rather
  than changing the card's wording.

## Output

`{status, diff, files_changed, build, problems, notes}` per `contract/output.schema.json`. `status`
is `ok` with a diff, or `cannot_implement` with a reason naming what blocks it — a shape the
mechanism cannot cover, a missing primitive on this device, or a scope that would need the host.

## Judging principles

- Implement the card as written. When the card looks wrong, say so in `notes` and implement it
  anyway: the experiment is what tests the card, and a silently improved card tests nothing.
- One variable per diff. If a second improvement suggests itself, note it; the orchestrator can
  propose it as its own card.
- Keep the change inside the operator's workspace. A change to the evaluator, the spec, the
  tolerances or the harness is not an implementation of a hypothesis.
- Report `cannot_implement` with the reason when the mechanism needs a capability the device lacks.
  A clear refusal costs one round; a fake implementation costs a round and produces a misleading
  `not_implemented`.
