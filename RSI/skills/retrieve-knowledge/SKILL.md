---
name: retrieve-knowledge
description: Fetch the rules, anti-patterns, patterns and similar past experiments that match a
  bottleneck signature, capped and ranked. Use at W2 step 2 after diagnose-bottleneck and before
  propose-hypothesis, and again when a campaign changes direction.
---

# retrieve-knowledge

Knowledge is injected on demand. This skill fetches a few entries that match the current
signature and writes them to `context.md`; it does not summarise the knowledge base, and it does
not decide which entry is right.

## When it runs

- Step 2 of W2, between the diagnosis and the hypothesis.
- After a campaign direction change, when the signature has moved.
- Before a plateau decision, so the prior attempts are visible.

## What you receive

One JSON object matching `contract/input.schema.json`: the `signature` from
`diagnose-bottleneck`, an optional `op` for same-operator matching, optional `ledger_file` and
`kb_root` overrides, and `caps` if the orchestrator needs a stricter budget than the default.

## Steps

1. Call `rsi.knowledge.retrieve(signature)`. Done when it returns `rules`, `antipatterns`,
   `patterns` and a `match_level` per store.
2. Call `rsi.knowledge.similar_experiments(signature, entries)` over the ledger. Done when it
   returns at most three entries, ranked by tag overlap then same operator.
3. Attach the measured hardware parameters with `rsi.knowledge.measured_parameters`. Done when the
   parameters are attached or the measured layer is absent and that is stated.
4. Apply the caps: 5 rules, 3 anti-patterns, 3 patterns, 3 experiments. Done when each list is at
   or under its cap and the count of what was dropped is recorded.
5. Render `context.md` in the fixed order the hypothesizer reads: signature, hardware parameters,
   rules, patterns, anti-patterns, past experiments. Done when every entry carries its id, its
   scope, its status and its match level.

## Output

`{status, context_md, counts, match_level, dropped, problems, notes}` per
`contract/output.schema.json`. `context_md` is the file the next step reads; `counts` and
`match_level` say how strong the match was, so a thin match is visible rather than implied.

## Judging principles

- Report the match level honestly. `exact` means tags and hardware both matched; `hw_relaxed` and
  `tags_any` mean the entries are suggestions from a neighbouring situation, and the hypothesizer
  must be told so in the file it reads.
- Prefer fewer entries to more. The caps exist because a long context dilutes attention, so a
  fill-the-quota retrieval is worse than a short honest one.
- Carry status and warning through unchanged: a `candidate` rule is confirmed at least once and
  not established, and a `conjecture` may inspire a hypothesis but cannot be evidence for one.
- Keep anti-patterns in the file even when rules are plentiful. A negative case is what stops a
  plausible idea that already failed.
