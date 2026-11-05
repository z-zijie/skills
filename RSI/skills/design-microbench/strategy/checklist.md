# design-microbench strategy

## What the suite already answers

Check this before proposing anything. An item that already observes the metric is extended, not
duplicated.

| Suite item | Parameter it produces | Metric it observes |
| --- | --- | --- |
| `noise_floor` | Noise threshold at each time scale | — |
| `drift` | Warm-up length, drift size | — |
| `memory_bandwidth` | Peak bandwidth | `device_mem_throughput_pct` |
| `peak_compute` | Peak flops per dtype | `compute_throughput_pct` |
| `cache_hierarchy` | Effective cache sizes and latency steps | `cache_hit_pct`, `cache_throughput_pct` |
| `coalescing_penalty` | Throughput versus access stride | `sectors_per_request` |
| `load_width` | Throughput ratio of wide loads | `instruction_count` |
| `bank_conflicts` | Slowdown versus conflict degree | `scratchpad_conflict_ratio` |
| `occupancy_vs_registers` | Throughput versus occupancy | `occupancy_pct`, `registers_per_lane` |
| `branch_divergence` | Slowdown versus divergent fraction | `branch_divergence_pct` |
| `instruction_cost` | Relative cost of expensive operations | `instruction_count` |
| `launch_overhead` | Fixed launch cost | `launch_overhead_us` |
| `sync_cost` | Cost per work-group barrier | `stall_barrier` |
| `atomics` | Cost versus contention | — |

The gaps that legitimately justify a new item are combinations the suite does not vary: two
mechanisms interacting, a mechanism that only appears at a size the suite does not reach, or a
metric the device exposes that no item moves.

## Designing the measurement

- **One variable.** The sweep changes one dimension and holds everything else at the value the
  operator uses. A sweep that changes stride and occupancy together answers neither question.
- **Include the operator's actual value.** A curve that brackets the real operating point can be
  read directly; one that starts where the operator is not wastes its resolution.
- **At least three repeats per point,** and report the spread. A single reading of a 2% effect on
  a device with a 4% noise floor is not a measurement.
- **Instrument nothing.** The measurement uses the evaluator's own timing and profiling paths, so
  its numbers are comparable with the ledger's.
- **Fingerprint and date every result.** Driver, runtime and compiler versions move these
  parameters, and a curve without its fingerprint cannot be trusted or invalidated correctly.

## Choosing between this and a calibration run

| Situation | Answer |
| --- | --- |
| A parameter moved after a driver upgrade | `calibrate-hardware`, not a new microbenchmark |
| The suite has no item for the metric | This skill: a new item |
| The item exists but does not reach this operating point | This skill: extend the item's sweep |
| The model is wrong about a mechanism the suite measures | Neither: the rules built on the old parameter need review |
| The metric is timing-derived and noisy | Neither until the noise floor is re-measured |

## Writing the proposal

- The rationale is the misses: experiment ids, their log-ratio errors, and the shared mechanism.
  A reader should be able to check the claim against the ledger in one pass.
- `used_by` names the consumer precisely: "the `effect.range` of R-031 and R-044", or "the
  device-memory term of the roofline for this dtype".
- `would_settle` is a question with two possible answers, and the design should make both
  outcomes cheap: if the result confirms the model, the rules get their prediction range back; if
  it refutes it, the affected rules go under review with a measurement to cite.
- The cost is device time for the sweep times the repeats. State it, because the human approving
  this is comparing it with a replay.

## After approval or rejection

**Approved.** The code lands through the evaluator's owner, the suite gains the item, and
`calibrate-hardware` runs it. The result goes to `kb/hardware/measured/` with its fingerprint,
and the rules the measurement touches lose `under_review` when the range is corrected.

**Rejected.** The affected rules keep a wide prediction range and stay under review. That is
honest and it is also a real cost: the hypothesizer will keep over- and under-pricing changes
around that mechanism. Record the rejection with its reason, so the same proposal is not
re-made every time the cluster reappears.

## Failure modes

| Symptom | Meaning |
| --- | --- |
| Every run proposes the same item | The previous proposal is unactioned; surface it rather than restating it |
| The sweep has one value | That is a single experiment, and it cannot separate the mechanism from its context |
| The parameter is never cited by a rule or the roofline | The measurement will be forgotten; either name the consumer or do not measure |
| The proposal arrives with no misses | The trigger is a cluster, not an intuition |
