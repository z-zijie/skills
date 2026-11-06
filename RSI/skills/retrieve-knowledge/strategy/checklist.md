# retrieve-knowledge: how matching works and why the caps are the point

## Order of matching

`rsi.knowledge` tries three levels and stops at the first that produces anything:

| Level | What is relaxed | What it means |
| --- | --- | --- |
| `exact` | nothing | the entry was scoped to this hardware id and these tags |
| `hw_relaxed` | the hardware constraint | the entry is from another device; the tags still match |
| `tags_any` | tags and classes | the entry shares a situation, not a signature |
| `none` | — | nothing matched |

Within a level, entries are scored by `signature.match_score`: hardware match first, then the
number of required tags, then optional tags, then class. This skill does not re-rank; it renders.

The level travels into `context.md` as a sentence, because the difference between "this rule was
confirmed on this device under this signature" and "this rule was confirmed somewhere similar" is
the difference between precedent and suggestion, and the hypothesizer must not have to infer it.

## Why the caps exist

Five rules, three patterns, three anti-patterns, three experiments. Stuffing a knowledge base into
a context costs tokens and dilutes attention: a hypothesizer reading forty rules weights them
roughly equally and produces a card that restates several of them at once, which is exactly the
multi-variable change the verifier cannot attribute.

A short context is a feature. `dropped` reports how many matched entries were cut, so a caller can
tell "nothing matched" from "plenty matched and you are seeing the top few".

A caller may tighten the caps and may not loosen them past the maximum: the contract's `caps` block
is bounded at 5/3/3/3, so a skill change cannot quietly make retrieval unbounded.

## What is rendered

Fixed order: signature, measured hardware parameters, rules, patterns, anti-patterns, past
experiments. Each entry carries its id, its scope, its status, its warning when it has one, and its
source path.

Statuses matter and are not decoration:

- `established`: enough confirmations across enough operators. Treat as precedent.
- `candidate`: confirmed at least once, not established. Worth trying, not authority.
- `under_review`: calibration moved, or a counterexample appeared, or prediction error is high.
- `conjecture`: unverified. It may inspire a card; it cannot be cited as evidence for one.
- `deprecated`: the confirmation rate fell below the floor. Treat as a warning.

## Measured parameters

The measured layer for this hardware id is attached when it exists: peak bandwidth, peak compute,
cache steps, launch overhead, the noise floor. These are what let a card predict a speedup instead
of guessing one. When the layer is missing, the context says so, and a card written from it should
predict a direction rather than a magnitude.

## What this skill never does

- It never edits a store, a rule or an anti-pattern. Knowledge changes through proposals and a gate.
- It never decides that an entry applies. It reports the match level and lets the hypothesizer cite
  what it used.
- It never truncates an entry's statement to fit. If an entry does not fit, it is not included at
  all, because half a rule is worse than no rule.
