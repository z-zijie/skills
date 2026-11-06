# Ledger

`experiments.jsonl` is append-only: one JSON object per line, one line per experiment, and no
line is ever edited or deleted. `artifacts/<exp_id>/` holds the evidence for that line — the
diff, the binary summary, the profiles and the logs.

Each entry carries the scaffold commit that was live when the experiment ran. Without that
field, nobody can answer the only question the outer loop cares about: which scaffold change
actually produced the improvement.

An entry is accepted only when it is built from a result the evaluator signed. A forged result
is refused, and a missing signature is treated as a forgery rather than as an oversight.

Reading history is always allowed. Writing is a tool call (`record-experiment`, or
`tools/rsi-cli record`), never a direct edit.
