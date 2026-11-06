# The knowledge base

Six stores, one property in common: nothing in them is guesswork wearing a confident voice.
Each entry says where it came from, what conditions it applies under, and how much evidence
stands behind it. That is what lets a rule survive a device swap instead of quietly rotting.

## The six stores

| Store | Holds | Written by | Why it can be trusted |
| --- | --- | --- | --- |
| `hardware/documented` | specs, mechanism notes, metric definitions and known pitfalls | `ingest-docs`, a human | every entry cites its source; an uncited hardware fact is rejected by the indexer |
| `hardware/measured` | parameters and curves measured by the calibration suite, with environment fingerprints | `calibrate-hardware` (evaluator only) | it can be re-measured at any time, and a result that expires is flagged as expired |
| `patterns` | how to do an optimization: mechanism, counters that should move, implementation traps | a human and the docs at first, then `distill-rules` | each pattern is reused by experiments, which is what accumulates evidence for or against it |
| `rules` | conditional claims: under signature X, transformation Y changes metric M by delta, with evidence | `distill-rules` | evidence counts plus replay; the life cycle is applied mechanically |
| `antipatterns` | refuted hypotheses, superficial-implementation forms, known traps | `distill-rules`, `capture-correction` | the refutation counts are attached, not just the claim |
| `conjectures` | unverified ideas | `register-conjecture`, a human | nothing; they are quarantined and labelled unverified |

The three layers of hardware knowledge are the documented layer, the measured layer, and the
part of `rules` bound to a specific device. An agent's feel for the hardware comes mostly from
the measured layer: it does not need anyone to explain the machine, it needs microbenchmarks that
can measure it.

## The rule life cycle

Status is a decision over evidence, not a matter of taste. `tooling/rsi/knowledge.py` computes it
in `lifecycle()`, and `docs/`-level thresholds live in that module so the decision is auditable.

| State | Entry condition | How it may be used |
| --- | --- | --- |
| `conjecture` | registered, no evidence | only to inspire hypotheses; never evidence, never grounds for a verdict |
| `candidate` | mechanism confirmed at least once | retrievable, labelled "thin evidence" |
| `established` | at least three confirmations across two or more operators, confirmation rate 70% or higher | enters the playbook priority order |
| `under_review` | hardware calibration changed, a new counterexample appeared, or prediction error stays above the hole threshold | still retrievable, with a warning; not used to order tactics |
| `deprecated` | fails review, or confirmation rate below 40% | moved to the anti-pattern store or archived; kept as a worked negative |

Two mechanical facts that follow, and that authors must not work around:

- **Prediction error above 0.2 forces `under_review`.** That is the signal that the scope is too
  wide, and the fix is a narrower `scope`, not a new paragraph in the statement.
- **A hardware change demotes.** A rule is bound to `hw_id`; when the device, driver or runtime
  changes, its evidence no longer transfers and it goes back to candidate to be verified again.

The numbers are suggested starting values. They are adjusted with data, never with taste.

## Write discipline

These are the rules every entry in every store obeys. They are short because they are load
bearing.

- **Every entry carries three things**: scope (hardware id, signature tags, class, where relevant
  a dtype or shape constraint), evidence (a list of experiment ids), and the date it was last
  verified.
- **Write conditions, not commands.** "Under condition C, transformation T changes metric M by
  delta." Never "always do T". A rule whose statement survives deletion of its scope was never a
  rule.
- **Keep positive and negative cases together.** Confirmation rate is confirmations divided by
  the number of times the mechanism was really implemented. An entry that lists only
  confirmations is a claim, not evidence.
- **Cite canonical metric names only.** Every metric named in `effect.metric` or
  `expected_counters` is checked against `tooling/rsi/metrics.py` at validation time, so a vendor
  spelling or an invented name fails loudly instead of drifting into a prediction.
- **Bind rules to a hardware id.** The `hw` field is mandatory for exactly this reason.
- **Every rule names its mechanism and the counters that should move.** Without both, the
  hypothesizer cannot write a falsifiable card, and the entry cannot be verified even in
  principle.
- **The knowledge base changes only through proposal and merge.** The inner loop appends
  experiments and never entries. `kb/proposals/**` is the only writable door, and a proposal is
  merged only after its gate passes. An agent that edits a rule while optimizing has broken the
  experiment, not improved the system.
- **State the evidence mode.** Seed example entries say so in a comment at the top of the file.
  When the ledger has real evidence, the seed is replaced, not amended.

## The stores and the retrieval caps

Retrieval is keyed by the signature's tag set plus operator traits and `hw_id`. Exact tag match
first, then relaxation step by step (hardware relaxed, then tags relaxed to "any"), with the match
level returned alongside the results so a thin match is visibly thin.

Each retrieval is capped. Stuffing the store into a context costs more and dilutes attention, so
the caps are small and fixed in `tooling/rsi/knowledge.py`:

| Retrieved | Cap |
| --- | --- |
| rules | 5 |
| antipatterns | 3 |
| patterns | 3 |
| similar past experiments | 3 |
| measured hardware parameters | the ones relevant to the signature's class |

Every returned rule is annotated with its status: a `candidate` carries "thin evidence", an
`under_review` carries its warning, and a `conjecture` carries "unverified: may inspire
hypotheses, cannot be evidence". The annotation travels with the entry, so the hypothesizer sees
how much weight it may put on it.

Tag matching plus full-text search is enough at this size. Vector retrieval is a later
optimization, once there are several hundred entries and a measurement showing plain matching
misses.

## What is in here now, and what it is worth

The stores are seeded, not grown. The patterns are the documented starting catalogue from the
build plan's seed-content table; the rules, anti-patterns and conjectures are worked examples of
every life-cycle state and every card shape, marked as example data at the top of each file. Their
experiment ids are shaped like real ones and do not exist in the ledger. They exist so the
retrieval code, the validator and the playbook cross-references can be exercised end to end before
the first real campaign produces a distilled rule.

Read a seed entry as an illustration of the format. Read a distilled entry, one carrying real
experiment ids and a real `last_verified`, as knowledge.
