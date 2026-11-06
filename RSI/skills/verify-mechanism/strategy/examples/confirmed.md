# Worked example: a confirmed verdict

The numbers are illustrative. Read them as a shape, not as a measurement.

**The card.**

```yaml
id: H-0142
op: reduce_sum
transformation: contiguous_access
mechanism: >
  Each lane reads every fourth element, so one scheduling group's access spans about four memory
  transactions. With contiguous indexing, transactions per request should approach one.
predictions:
  counters:
    - {metric: sectors_per_request, direction: down, expect: "<= 1.5"}
    - {metric: device_mem_throughput_pct, direction: up}
  speedup: {expect: 1.25, low: 1.10, high: 1.45}
evidence_of_implementation:
  - the inner loop indexes the input by the contiguous lane stride, not by stride 4
  - the disassembly shows consecutive loads stepping by the element size
scope_of_change: indexing and traversal order only; the reduction algorithm is unchanged
```

**How each group answers.**

- **A1** pass — the inner loop's index expression changed.
- **A2** pass — the diff touches the index and nothing else.
- **A3** pass — the fast path is taken for every shape in the suite; there is no tail branch.
- **A4** pass — flags unchanged.
- **B1** pass — `binary_after.json` shows loads stepping by 4 bytes; `binary_before.json` shows
  a stride of 16.
- **B2** pass — the stride-16 load is genuinely gone, not merely also present.
- **B3** pass — registers per lane rose by 2 and occupancy did not fall.
- **C1/C2/C3** pass — `sectors_per_request` fell from 3.8 to 1.2, above the measured jitter, at or
  below the card's bound; `device_mem_throughput_pct` rose.
- **C4** pass — no unnamed counter got clearly worse.
- **D1–D8** all pass — full coverage of the shapes, no buffer added, no work moved, precision
  unchanged.

**Verdict:** `confirmed`, and the counters moved as predicted. Whether it is *faster* is decided
later by the timing result, which the verifier never saw.
