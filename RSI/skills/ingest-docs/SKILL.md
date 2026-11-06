---
name: ingest-docs
description: Turn specification sheets, runtime documentation and profiler metric definitions
  into documented hardware-knowledge entries, each with its source and its date. Use when a new
  device is being onboarded, when a documented fact is stale or missing, or when a counter's
  meaning or reliability is in doubt.
---

# ingest-docs

The documented layer answers "what does this machine do, according to the people who built it".
It is not measured and not inferred: every entry cites the document it came from, because an
uncited fact about hardware is a rumour that will outlive the person who started it.

The three layers of hardware knowledge are the **documented** layer, the **measured** layer and
the part of `rules` bound to a specific device. An agent's feel for the hardware comes mostly
from the measured layer. The documented layer is what tells it which measured numbers are even
possible, and which counters to distrust.

## What you receive

A document set: paths to specification sheets, runtime documentation, or profiler metric
definitions, plus the hardware id they describe. Match `contract/input.schema.json`.

Long documents are read at the section that answers a question, not start to finish. Decide the
questions first:

1. What are the memory levels, their capacities, and which one is software-managed?
2. What is the unit of device-memory access, and what does a request cost when it is not
   contiguous?
3. What limits how much work can be resident at once, and which resource usually binds first?
4. Which profiler counters exist, what exactly do they count, and where are they known to
   mislead?

## Steps

1. **Pick the entries.** For each question above, name the fact the system currently lacks or
   holds wrongly. Done when each gap has a question it answers and a section of a document that
   answers it.

2. **Extract the fact, the condition and the source.** Write the entry as a claim with its
   scope: what is true, on which part or generation, under which conditions. Done when a reader
   could tell from the entry alone whether it applies to a different device.

3. **Record reliability, not just meaning.** Where a counter's definition admits a known
   misread — a value that can exceed the intuitive maximum, a counter that counts retries as
   work, a metric that mixes two units — say so in the entry's reliability section. This is what
   the mechanism verifier consults before resting a verdict on that counter. Done when every
   counter the entry mentions carries a reliability statement or an explicit "no known issue".

4. **Write the entry.** One fact per entry, in `kb/hardware/documented/<topic>.md`, with a
   `source:` and an `accessed:` date in the header. Add it to `index.yaml`. Done when the file
   renders and the index lists it under the right topic.

5. **Keep the device-specific part separate.** A vendor difference belongs in a clearly labelled
   section at the end, not woven through the general claim. The general claim is what a
   different device's reader needs; the device section is what one device's reader needs.
   Done when the general claim is readable and useful with the device section removed.

6. **Check for conflict.** If a new entry contradicts an existing one, do not silently replace
   it: record both, with both sources, and open a proposal that names the conflict. A
   contradiction between two documents is a real finding about the hardware or about the
   documents, and hiding it is how a knowledge base becomes confidently wrong.

## Output

Markdown entries under `kb/hardware/documented/` plus an updated `index.yaml`. Match
`contract/output.schema.json`. Each entry carries:

- `topic`, `hw_ids` it applies to (`any` for device-independent)
- `claim`: one paragraph, in canonical vocabulary
- `source`: document and section; `accessed`: date
- `reliability`: per counter, what can go wrong (or "no known issue")
- `device_specific`: optional, clearly laboured section for one vendor's difference

## Judging principles

- A claim without a source is a conjecture. Write it to `kb/conjectures/` instead, where it is
  visibly unverified.
- Quote the number the document gives, with its unit. Do not convert it to a nicer unit and do
  not round it into confidence.
- Prefer the narrow claim. "On this generation, the software-managed memory is banked by 4-byte
  words" survives a device change; "the machine has banked memory" does not.
- A documented peak is a ceiling, not an achievement. The roofline uses the **measured** peak;
  the documented one only tells you whether a measurement is plausible.
- Write in canonical vocabulary. `scratchpad`, not one vendor's name for it, with the vendor's
  name in the device-specific section where a reader searching for it will find it.
