# Glossary

Terms this framework uses in a specific sense. Where a term has an everyday meaning too, the
specific meaning is the one that applies here.

**Attainment** — the roofline bound divided by the measured time, as a percentage. The objective
answer to "are we at the limit yet". A kernel at 85 % attainment has little routine tuning left.

**A/A test** — the same kernel measured as if it were two candidates. The apparent difference is
the noise floor. It is measured per device and re-measured when the environment changes, because
a hardcoded tolerance is a tolerance for the wrong machine.

**Bottleneck signature** — a class plus a tag set plus the metrics behind it. Tags are threshold
decisions over canonical metrics, so the same signature means the same thing on a different
device once its thresholds are calibrated. It is the retrieval key for everything the knowledge
base holds.

**Card** — a hypothesis card: one falsifiable experiment, with a mechanism, named counters with
predicted directions, an expected speedup, and the evidence that would show the change landed.

**Conjecture** — an unverified idea. It may inspire a hypothesis and may not be evidence. Stored
separately so it cannot be mistaken for a rule.

**Confirmed / refuted / not implemented** — the three verdicts. The third is not a result: it
means the change did not land, so it goes back to the implementer and never enters the knowledge
base as experience.

**Environment fingerprint** — device, runtime, driver, compiler and flags, and host OS, in one
string. Two results are comparable only within one fingerprint.

**Established rule** — a rule with enough confirmations across enough operators to enter a
playbook's priority order. The thresholds are starting values in `tooling/rsi/knowledge.py`.

**Inner loop** — one experiment: diagnose, retrieve, hypothesize, implement, evaluate, verify,
record, promote. W2.

**Layer 0** — the evaluator. Frozen, signed, the only source of truth. Agents call its CLI and
never read or edit its source.

**Ledger** — the append-only record of every experiment, with the scaffold commit that was live.
Attribution depends on that field.

**Noise floor** — the smallest speedup that is distinguishable from measurement jitter on this
device. The win rule is the lower confidence bound of a speedup clearing it.

**Outer loop** — improving the scaffold: cluster failures, distill rules, audit the knowledge
base, edit one playbook or skill strategy, replay, merge or roll back. W5.

**Playbook** — the "given this bottleneck signature, what do we try and in what order" carrier.
The outer loop's main target.

**Replay** — running an old and a new scaffold on the same operators with the same budget, at
least three times, and merging only if the primary metric improves on dev with an interval that
excludes zero while holdout holds.

**Scaffold** — everything around the model: knowledge base, playbooks, skills, prompts, tools.
The thing that improves. Model weights never do.

**Signature tag** — one discretized metric condition, e.g. `uncoalesced`. Tags are what retrieval
matches on.

**Superficial implementation** — a change that is written but did not land, or landed without
moving what it promised. The mechanism verifier's group D checklist is the growing catalogue.

**Token / device-second budget** — the campaign's cost ceiling. Stop conditions include spending
it; the outer loop has its own weekly cap because replay is the expensive part.
