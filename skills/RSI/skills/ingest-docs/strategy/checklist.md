# Documented-layer checklist

Use before an entry is written. The documented layer is the one store a reader trusts without
re-measuring, so its bar is higher than the others.

## The claim

- One fact per entry. Two facts in one entry means one of them will be stale first and nobody
  will notice which.
- Stated as a claim with a scope: what is true, on which part or generation, under which
  conditions.
- Stated in canonical vocabulary — `device_mem`, `scratchpad`, `work_lane`, `work_group`. A
  vendor's name for a thing goes in the device-specific section, where someone searching for it
  will still find it.
- Carries its unit and the document's own number. No unit conversion, no rounding that adds
  confidence.

## The source

- Names the document and the section, closely enough that a reader can find the sentence.
- Carries an access date; hardware documents change under their own URLs.
- Is a primary source where one exists. A blog post summarising a specification is evidence
  that the specification says something, not evidence of what it says.

## Reliability

- Every counter the entry mentions has a reliability line, even if that line is "no known issue".
- A known misread is recorded with the condition that triggers it: a value that can exceed its
  intuitive maximum, a counter that includes retries, a metric that mixes units.
- Anything the mechanism verifier could cite is here, because this file is where it looks
  before resting a verdict on that counter.

## Scope

- The general claim stands on its own with the device-specific section deleted.
- A fact that only holds on one part is written as a device-specific fact, not generalised.
- A fact that only holds under a condition states the condition: dtype, alignment, shape range,
  work-group size.

## Conflicts

- A contradiction with an existing entry is recorded, not resolved by overwriting.
- Both sources are kept, and a proposal names the conflict for a human.
- Two documents disagreeing is a finding. Two documents disagreeing and one being quietly
  dropped is how a knowledge base becomes confidently wrong.
