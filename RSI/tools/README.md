# tools

`rsi-cli` is a convenience wrapper, not a second interface. Every subcommand either calls a
skill's `scripts/` entry point or the evaluator's own CLI; if a subcommand is missing, the script
it wraps is still the real interface.

```sh
tools/rsi-cli doctor                    # what this machine has, and what is missing
tools/rsi-cli calibrate --suite core    # measure the device (Layer 0)
tools/rsi-cli redteam --op vec_add      # prove the evaluator catches cheating (Layer 0)
tools/rsi-cli evaluate --op X --candidate P   # signed correctness and timing result
tools/rsi-cli profile --op X            # canonical counters
tools/rsi-cli analyse --op X --profile P --measured-us N   # signature plus attainment
tools/rsi-cli retrieve --signature S    # knowledge matching a signature, with the caps applied
tools/rsi-cli record --result R         # append one experiment (refuses unsigned results)
tools/rsi-cli campaign --op X           # the state machine: which skill runs next
tools/rsi-cli report --op X             # campaign report from the ledger
tools/rsi-cli replay --proposal P       # replay judge on a scaffold change
tools/rsi-cli kb-audit                  # knowledge-base health
```

## Why a wrapper exists at all

Two reasons, both about mistakes that are expensive to catch later:

- **One place that names the evaluator.** Layer 0 is frozen and reachable only through one
  command shape. An agent cannot construct a different invocation, so it cannot call a command
  that does not exist or pass a flag that changes the protocol.
- **One place that checks a result was signed.** `record` verifies the evaluator's signature
  before a line reaches the ledger. A result that was edited by hand is refused there, not
  discovered a month later in a rule card's evidence count.

The scripts under `skills/*/scripts/` are the interfaces of record. This wrapper just makes them
discoverable in one list.
