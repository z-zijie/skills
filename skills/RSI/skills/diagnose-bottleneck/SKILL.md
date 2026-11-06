---
name: diagnose-bottleneck
description: Turn a normalized profile into a bottleneck signature, compute roofline attainment
  and name the primary bottleneck. Use after collect-profile has produced profile.json and
  before retrieve-knowledge or any hypothesis is written.
---

# diagnose-bottleneck

Mostly script, with a narrow reading step. The discretization is code: canonical metrics plus
this device's thresholds give a class and a tag set, and the roofline gives attainment. What
remains for reading is the one thing thresholds cannot decide — whether the profile is trustworthy
enough to name a bottleneck at all, and what the named bottleneck means in words.

## When it runs

- Step 1 of W2, on the current `best/`.
- After three rounds without improvement, when the bottleneck may have moved.
- Whenever a hypothesis was confirmed but the kernel did not get faster: that combination means
  the diagnosis was wrong, and this skill is where the diagnosis is rebuilt.

## What you receive

One JSON object matching `contract/input.schema.json`:

| Field | Use |
| --- | --- |
| `op`, `hw_id` | Which operator, which device |
| `profile` or `profile_file` | The normalized profile from `collect-profile` |
| `measured_us` | The timing number for the same candidate, from `run-evaluator` |
| `spec` or `spec_file` | The operator spec, for the byte and flop counts |
| `shape` | Which case the timing number refers to; defaults to the first typical shape |
| `thresholds` or `thresholds_file` | This device's threshold block; defaults to the measured layer |
| `extra_tags` | Structural tags such as `plateau`, decided by the campaign state |

## Steps

1. Load the profile and confirm it carries `hw_id` and `env_fingerprint`. Done when both are
   present; a profile from another fingerprint is a different measurement, so report
   `status: unavailable` rather than compare across it.
2. Build the roofline from the spec's cost model and the measured layer:
   `hardware.Roofline.from_measured(...).attainment(bytes, flops, measured_us)`. Done when
   `bound_us`, `measured_us` and `attained_pct` are all numbers, and `limiting_term` names which
   term set the bound.
3. Discretize with `signature.build`: class from the two utilization numbers, tags from every
   threshold decision that fires, plus the structural tags you were given. Done when the result
   validates against `schemas/signature.schema.json`.
4. Read `primary_bottleneck` and confirm it is one of the playbook names the campaign knows. Done
   when the name matches a playbook the orchestrator can open.
5. Write the one-line note: which metrics the verdict rests on, and which are missing. Done when
   `notes` names every metric the class depends on that the profile reported as unavailable.

## Output

`{status, signature, problems, notes}` per `contract/output.schema.json`. `signature` is the
object `schemas/signature.schema.json` defines: `op`, `hw_id`, `class`, `tags`, `metrics`,
`roofline`, `primary_bottleneck`. It is the retrieval key for knowledge and the first field of
every ledger entry, so its field names and threshold semantics are frozen.

## Judging principles

- Decide on the canonical metrics present. Missing utilization is unknown, and the classifier
  already handles that case by falling back to attainment; do not fill a gap to make a class look
  decisive.
- Keep the roofline's `limiting_term` and `assumptions` with the number. Attainment without the
  term that set it reads as "we are at 55% of peak" when the bound may be launch overhead.
- Distinguish "the counters say memory-bound" from "the counters do not say". A signature built
  from attainment alone is legitimate and must say so in `notes`, because a hypothesis written on
  it predicts a mechanism nobody can check.
- Report the primary bottleneck as one name. A tag set is context; the campaign needs one
  playbook to open.
