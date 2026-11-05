# Documented hardware layer

Facts about the machine, taken from the documents that define it — specification sheets, runtime
documentation, profiler metric definitions — each with its source and the date it was read.

This is the first of the three layers of hardware knowledge:

- **documented** (here): what the documents say. Every claim cites its source.
- **measured** (`../measured/`): what the calibration suite measured on this machine.
- **bound rules** (`../../rules/`): the part of the rule store scoped to one hardware id.

An agent's feel for the hardware comes mostly from the measured layer: the measured layer needs
no documentation to exist and is re-measurable at any time. The documented layer's job is to say
which measurements are physically plausible, and which counters to distrust.

## Writing an entry

One fact per entry. A file starts with a small header the indexer reads:

```
---
topic: memory-hierarchy
source: <document, section>
accessed: 2026-11-01
hw_ids: any
---
```

Then the claim in canonical vocabulary (`device_mem`, `scratchpad`, `work_group`, `work_lane`),
then a reliability section naming what can go wrong with each counter the entry mentions. A
vendor-specific difference goes in a clearly labelled section at the end, so the general claim
still reads for a different device.

Run `python3 skills/ingest-docs/scripts/index_documented.py` after adding an entry: the index is
regenerated, never hand-maintained, and `--check` fails when it is stale or when an entry has no
source.
