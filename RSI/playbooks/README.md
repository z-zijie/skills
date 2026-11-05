# Playbooks

A playbook answers one question: **given this bottleneck signature, what do we try, and in what
order?** It is the carrier for priority — the order in which tactics are attempted — and nothing
else.

That division of labour is the point. A **skill** says how a kind of action is performed
(`propose-hypothesis` says how to write a falsifiable card). A **pattern** says how one
transformation works and which counters it should move. A **rule card** says under which
conditions a transformation has been observed to work, with evidence counts. A **playbook** says
which of those to reach for first when the machine is in a particular state, and when to stop
reaching. Only the playbook holds an ordering, and the ordering is the thing the system learns.

## Layout

One file per bottleneck signature class. The names match `tooling/rsi/signature.py`, which maps a
tag set to the playbook that should be opened first:

| Playbook | Opened when |
| --- | --- |
| `mem-dram-saturated.md` | `mem_saturated`; the memory term is the limiting term |
| `mem-cache-miss.md` | `uncoalesced` or `cache_hit_low`; the access pattern is defective |
| `compute-bound.md` | `compute_saturated`; the compute term is the limiting term |
| `low-occupancy.md` | `occupancy_low`; the machine holds too little work and is idle |
| `latency-bound.md` | `launch_dominated`, or neither utilisation counter clears its threshold |
| `divergence.md` | `divergence_high`; groups are paying for both sides of a branch |
| `smem-conflict.md` | `scratchpad_conflict`; on-chip accesses are serialised |
| `plateau.md` | `plateau`; the tactical playbooks have stopped paying |

Each file carries the same six sections: a header (`Applies to`, `Hardware`, `Version`,
`Last replay`), `## Confirm first`, `## Try in this order`, `## Do not`, `## Stop`.

`Applies to` names the canonical class and tags the file is written for. It is documentation for
a human reader; the machine-readable routing decision lives in `signature.py`, so the two must
agree. When they disagree, the header is wrong.

## How the priority order relates to rule status

An entry in `## Try in this order` cites either a rule id or a pattern id, and its position
depends on how much is known about it:

- **An `established` rule enters the priority order at the position its effect size earns.**
  Three or more confirmations across two or more operators, confirmation rate at or above 70% —
  at that point the tactic is expected to work, and the position reflects how much it is expected
  to be worth, not how plausible it sounds.
- **A `candidate` is labelled "thin evidence" and sits lower**, usually after the established
  tactics and the ones that need no evidence to try (cheap reversibility). One confirmation is a
  lead: worth an experiment, not worth building a campaign on.
- **An `under_review` rule is still citable but carries its warning in the text**, so a reader
  sees the counterexamples before the claim. It does not order the list.
- **A `deprecated` rule is not cited as a tactic at all.** Its actionable form appears under
  `## Do not`, and the anti-pattern id that carries the refutations is what the reader is sent to.
- **A tactic with no rule yet is marked as such in the text** (`no rule yet`) and cites its
  pattern id instead. It can sit high in the list when the mechanism is well understood and the
  measured curve makes the expected gain predictable, but the absence of a rule is stated, so
  nobody mistakes familiarity for evidence.

The ordering is therefore a claim about this device, this signature, and the current state of the
evidence. It is the thing the outer loop tunes.

## How a playbook changes

Playbooks are edited by the outer loop, and only through a proposal plus replay. The path is:

1. **A failure pattern is identified.** `analyze-failures` clusters what went wrong across a
   campaign: a tactic that kept being tried and refuted, a tactic that was never tried when it
   should have been, a stop condition reached too late.
2. **A proposal is written** to `kb/proposals/` as kind `playbook`, naming the target file, the
   diff, and the evidence (experiment ids or a replay report id). The write goes through
   `paths.PROPOSAL_ONLY`, which is why no agent can edit `playbooks/` in place.
3. **Dev replay runs.** The proposed ordering is replayed over the dev split: does a campaign
   following this order reach higher attainment, or reach the same attainment in fewer
   experiments? A change that makes the order worse on dev is rolled back without touching
   holdout.
4. **Holdout replay runs** only for a change that passed dev. This is the guard against
   overfitting the order to the operators that produced the proposal.
5. **Merge or rollback.** Either outcome is recorded in `CHANGELOG-scaffold.md`, and every ledger
   entry keeps the scaffold commit that was live, so the question "which ordering change produced
   this improvement" is answerable later.

Two things a proposal may not do: change what the metrics mean, and add a tactic whose mechanism
is not stated. The first is a contract change (human only); the second makes the tactic
unverifiable, so a replay cannot tell a real improvement from a lucky one.

## Versioning

`Version` increments on every merged change. `Last replay` records the last replay outcome for
this file — `never` means exactly that: the ordering has been written from documented mechanism
and seed evidence, and no campaign has yet tested it. Every playbook in this directory currently
says `never`, which is the honest state of a system whose first campaign has not run.

`Hardware: any` means the file states relationships rather than numbers, so it is not bound to a
device. Any threshold it refers to is read from `kb/hardware/measured/` for the current `hw_id`;
any expected gain is read from the measured curves. A playbook that states a number is a
playbook that has gone stale in a way nobody will notice.
