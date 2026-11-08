# Decision records

One file per durable decision that code, tests, and existing docs cannot explain. Mechanical or local edits need no record.

## When to write one

- The change makes a structural choice (module split, public interface, data flow, repository policy) with real alternatives.
- The rationale will matter to a future contributor or reviewer.
- A proposal for substantial future work.

## File name and format

`yyyy-mm-dd-topic.md`, with these sections:

```markdown
# Decision: <title>

## Problem

## Decision

## Alternatives considered

## Consequences
```

- `Problem` stands without the solution.
- `Decision` describes what is now true, in the present tense.
- `Alternatives considered` records each genuine alternative and why it lost; a decision without what it beat invites re-litigation.
- `Consequences` records what the trade-off cost and bought.
- Link the record from the PR. When a decision is replaced, add a link from the old record to the new one; do not rewrite the old record into its opposite.
- Decisions already recorded in `RSI/docs/` do not need a new file.
