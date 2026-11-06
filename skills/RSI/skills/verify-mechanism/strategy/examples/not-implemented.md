# Worked example: a not-implemented verdict

The numbers are illustrative.

**The card.** Claims that a scratchpad tile removes repeated device-memory reads, and states as
its evidence that the hot loop reads each element once into the scratchpad and re-reads only from
there.

**What the diff shows.** A scratchpad buffer is allocated, filled once, and then the computation
still reads the original input array inside the inner loop.

**Which checks fail.**

- **A1** pass — the diff is on the hot path.
- **B1** fail — the disassembly shows scratchpad accesses, but the inner loop still issues
  device-memory loads.
- **C1** fail — `device_mem_bytes` did not fall; it rose slightly, because the tile is written and
  read back without replacing anything.
- **D2** fail — this is the textbook form: the buffer exists, the repeated reads do not.

**Verdict:** `not_implemented`. `missing` names the two absences the implementer needs: the loop
must read from the scratchpad, and `device_mem_bytes` must fall. The implementer gets two more
attempts; after that the experiment is recorded as an implementation failure and counts toward
no rule.

The tempting wrong verdict is `refuted` — "the tile did not help". It did not help because it was
never finished, and recording it as a negative result would teach the knowledge base that tiling
does not work.
