# implement-change: one card, one diff, one variable

The implementer is the cheapest model in the loop and the one whose failures are easiest to measure:
its own error rate is the `not_implemented` verdict. So the job is narrow on purpose.

## What "implement the card" means

1. The change is where the card says it is. The hot path, not a helper that runs once, not host
   code, not a comment.
2. The change is what the card says it is. If the card predicts wider loads, the diff issues wider
   loads. A different improvement that happens to compile is not an implementation.
3. The change is the only change. A second improvement in the same diff makes a confirmed mechanism
   unattributable, and an unattributable confirmation is not evidence for a rule.
4. The change survives the build. A diff that does not compile is not a candidate, and leaving it in
   the workspace while reporting failure is what lets a retry build on it.

## The three failure paths and what each means

| Status | Meaning | What happens next |
| --- | --- | --- |
| `build_failed` | the edit did not produce a module that compiles or imports | retry, at most twice |
| `cannot_implement` | the mechanism needs something the device or the scope does not have | report it; a clear refusal beats a fake implementation |
| `scope_violation` | the edit resolved outside the operator workspace | refuse; the workspace is the boundary |

A `cannot_implement` with a reason costs one round. A fake implementation costs a round *and*
produces a `not_implemented` verdict that looks like an implementer failure when it was really a
device limit, which then poisons the routing decision about the implementer's tier.

## Retrying

Two retry kinds, and they want opposite fixes:

- **A correctness failure** names the failing shapes. The mechanism is right and its coverage is
  wrong: a tail, an edge shape, a boundary. Fix the coverage. Never touch the tolerance, and never
  special-case a shape so the test passes while the kernel stays wrong for the next shape.
- **A `not_implemented` verdict** names the missing evidence. The mechanism did not reach the code
  path the card claims. Make it visible in the diff and in the machine code. Rewording the card's
  evidence statements is not a fix.

After two retries of the same kind, the round is recorded as an implementation failure. That counts
toward no rule, and it is the number that says whether the implementer's tier is too weak.

## Applying the edit literally

The change arrives as exact replacements, and the script applies them literally. Two consequences
worth knowing:

- A replacement whose old text does not occur exactly once is refused. That happens when the card
  was written against a different kernel version, and it is better to find out here than to ship a
  partial change.
- An ambiguous replacement is refused too. Two matching places mean the edit is not one change.

The diff is generated from the applied text, so what the verifier reads is what is on disk.

## Scope

The workspace is `operators/<op>/workspace`. Everything else — the spec, the tolerances, the
evaluator, the harness, the benchmark splits — is outside it, and an edit that resolves there is a
scope violation rather than an implementation. That is not a policy the model is asked to respect;
it is a check that fails.

## What this skill never does

- It never sees the timing result. It knows the card and the previous failure, and nothing about
  whether the last attempt was fast.
- It never rewrites the card. When the card is wrong, it says so in `notes` and implements it
  anyway, because the experiment is what tests the card.
- It never decides whether the mechanism landed. That is `verify-mechanism`, which sees the diff and
  the machine code and not the implementer's account of either.
