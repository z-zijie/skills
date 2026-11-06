# Closures: what a failed direction actually proved

A campaign's stop condition says "five rounds bring no significant gain and no new hypothesis
predicts one above the noise floor". That sentence is only as good as the refutations behind it.
This page is about the gap between *recorded as refuted* and *refuted*.

## The failure this exists for

An agent tries a direction, the build fails, the probe writes wrong output, the API form it used
turns out to be declared and not linked, or one width of a parameter is tried and the counter does
not move. The card is marked `refuted`, the direction leaves the priority order, and the campaign
walks on. Later the campaign stops with "no open route", reports that the remaining gap needs an
algorithm-level rewrite, and a human reads that as "these ideas were tried".

The report says something narrower, which is why the closure record is a first-class field rather
than a sentence in the verifier's notes: **what was tried was the implementation, not the idea.**

## What the scaffold already did, and why it was not enough

Four mechanisms already existed for exactly this problem, and each one covers part of it:

- **The `not_implemented` verdict counts toward no rule.** A change that never landed is a retry,
  not evidence, and the knowledge base refuses it. This is the right principle, and it only fires
  when the verifier notices: in a campaign where the verifier is written by the implementer, a
  compile failure that nobody files as `not_implemented` becomes a refutation.
- **Both retry loops are bounded at two attempts.** They catch "the implementer could not do it"
  within one round. They do not catch "the implementer tried once, called it a refutation, and the
  next round started somewhere else", because that is a valid round with a valid verdict.
- **`analyze-failures` retires a transformation after two refutations.** That is a reasonable
  threshold, and it treats the verdict word as the fact. Two refutations that never landed retire
  a direction exactly as two that did.
- **The `plateau` playbook says to confirm that the three experiments were real attempts before
  trusting the plateau.** It says so in prose, at the point where a campaign is already about to
  reach a structural conclusion, and there is nothing to check it against: the ledger records the
  verdict, not what the verdict rested on.

So the scaffold's instinct was right and its record was thin. Every one of these mechanisms asks
"was it really implemented?" and can only answer with the verdict word, which is precisely the
thing in question. A closure is the missing field: it makes the answer checkable five rounds later,
when the campaign is deciding whether a direction is dead.

## The evidence classes

`tooling/rsi/closures.py` derives the class from the verifier's own checks, never from the verdict
word and never from the implementer. The mapping is mechanical so two readers of one verdict
cannot disagree:

| `evidence` | Derived from | Negative evidence about the direction? |
| --- | --- | --- |
| `implementation` | the verdict is `not_implemented`, or any group A or B check failed or is `unknown` | no |
| `measurement` | groups A and B passed and every group C check passed, while the verdict is refuted because the score did not follow | no: this is a mislabelled confirmation |
| `premise` | groups A and B passed, group C was measured, and a C check failed | yes |
| `unclassified` | a refutation with no checks recorded | nothing is known |

Group A and B are "the change exists and reached machine code". Group C is "the named counters
moved". The four combinations the verifier already documents map onto these classes one to one,
which is the point: nothing new is being measured, one field is being recorded so that a later
decision cannot quietly reinterpret it.

## The two questions a closure has to answer

1. **Was it real?** Only `premise` is. A change that did not land, or that landed and moved its
   counters while the score did not follow, says nothing about whether the direction pays.
2. **Was it swept?** `points_tried` is how many points of the direction's parameter were tried.
   One point is a sample, not a search. `parameters` names what the direction varies so the next
   card can widen it instead of re-picking one value.

A closure is **retirable** when it is `premise`, tried at `MIN_POINTS` or more points, and records
no `reopen_when`. Every other closure is **open**, and an open closure is not evidence that a
direction does not pay.

## Where the record comes from

`record-experiment` writes the record for every `refuted` and `not_implemented` entry. The
derived fields (`evidence`, `landed`, `reason`, `checks_failed`) come from the verdict document;
the declared fields (`direction`, `points_tried`, `parameters`, `reopen_when`) come from the
request, because they are things the verifier knows and the checks do not.

An entry recorded before this field existed is classified from its archived verdict when one is
available, and stays `unclassified` when it is not. Inferring a class from the verdict word is
exactly the guess this module refuses to make: `refuted` is used for all three of a compile
failure, a confirmed-but-not-faster mechanism, and a real refutation.

## Where the decision is made

`revisit-closures` (state `revisit`) runs on every round between `gap` and `plan`, and again when
`plan-campaign` proposes a stop. It answers one question: is this campaign owed a reopen?

| `decision` | When | What happens |
| --- | --- | --- |
| `reopen` | a revisit is triggered and an open closure has reopens left | the next card is that reopen, and the campaign continues |
| `hold` | closures are open but each has used `MAX_REOPENS` | the campaign proceeds and the report must present them as unresolved |
| `exhausted` | every closure is retirable | stopping is honest, and the report may say the playbook is exhausted |
| `not_triggered` | no stop proposed, fewer than `min_open_closures` open, streak below threshold | nothing changes |

The triggers are a proposed `stop` or `change_direction`, `stalled_rounds` at or above the
revisit threshold, and `min_open_closures` or more open closures. Thresholds live in
`tooling/rsi/closures.py` next to the ones the state machine already owns, so a change to them is
a scaffold change like any other.

## Reopen order and kinds

A reopen is the *same* direction with the defect removed or the sweep widened; it is not a new
idea, and `propose-hypothesis` is still what turns it into a card. Order is by evidence class —
an implementation defect is cheapest to settle and a mislabelled confirmation is the most
informative — then by the speedup the closed card committed to.

| `retry` | For | The card has to |
| --- | --- | --- |
| `fix_implementation` | `implementation` | isolate the failing artefact and assert it alone before any timing |
| `widen_sweep` | `premise` below `MIN_POINTS` | sweep the parameter and predict against the best point |
| `bank_and_recheck` | `measurement` | keep the patch, record the kernel-level gain beside the operator-level non-gain, and retest when the kernel's share of case time rises |
| `classify_then_retry` | `unclassified` | record the checks the verdict is missing |

Each reopen also carries `refutation_would_be`, the condition under which a second refutation is
valid and a third reopen is not owed.

## What the report has to say

`report-campaign` reads the closures. A campaign that stops with `hold` closure open writes
"unresolved" and names the directions; a campaign that stops with `exhausted` closures may write
"needs an algorithm-level rewrite". The difference between those two sentences is the entire
value of the record, because one tells a human that the idea failed and the other tells them that
the attempt failed.

## Where this comes from

The mechanism was designed against one real campaign rather than from a design document: an
operator optimization run with eleven ledger entries, seven of them negative. Of the four whose
verdict documents were archived, one never reached machine code, one was a confirmed mechanism
whose operator score did not follow, and three refuted a mechanism that had been measured at a
single width of its parameter. None of the seven is retirable, and the direction the campaign
named as its largest remaining lever was never actually attempted.

That campaign's audit is in [`campaign-audit.md`](campaign-audit.md), and the worked example in
[`skills/revisit-closures/strategy/checklist.md`](../skills/revisit-closures/strategy/checklist.md)
is its classification, produced by the script this page describes.
