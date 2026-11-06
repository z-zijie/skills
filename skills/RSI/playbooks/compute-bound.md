# playbook: compute-bound

Applies to: class = compute_bound, with tag `compute_saturated`; sometimes together with
`divergence_high` or `register_pressure`.
Hardware: any
Version: 1
Last replay: never

The dominant compute pipe is at its measured ceiling. The compute term of the roofline is the
limiting term, so the tactics that pay are the ones that reduce the number of operations the pipe
must retire, or move them onto a wider pipe the same instruction count can use. Memory-side work
cannot help: at this utilisation the memory path has spare capacity by definition.

## Confirm first

- `compute_throughput_pct` is at or above the `compute_saturated` threshold, and
  `device_mem_throughput_pct` is below its threshold. If both are high, the signature is `mixed`
  and this is not the right playbook until `roofline.limiting_term` says `compute`.
- `roofline.limiting_term` is `compute`. Confirm the dtype used for the bound is the dtype the hot
  loop actually executes in — an implicit promotion makes the bound wrong in both directions.
- `branch_divergence_pct` is below the divergence threshold. If it is above, run the `divergence`
  playbook first: a divergent group retires fewer useful results per issued instruction, which
  reads as a compute ceiling while it is really a masking problem.
- Read the measured `instruction_cost` table for this `hw_id` before predicting what replacing an
  operation is worth. The ratio between a divide and a multiply is a device property.

## Try in this order

1. **A multiply and an add on the same accumulator** — express it so the compiler emits one fused
   operation (P-006, no rule yet). Expect `instruction_count` down and `compute_throughput_pct`
   up. Check the disassembly: if the pair was already fused, the change is void and the card must
   be marked not implemented.

2. **A division by a loop-invariant value** — hoist the reciprocal and multiply (P-007, no rule
   yet). Expect `instruction_count` down and `compute_throughput_pct` up, in proportion to the
   measured instruction-cost ratio for this device. If the divisor was a compile-time constant the
   compiler has likely done this already.

3. **Arithmetic promoted to a wider dtype than the operator asked for** — force the intermediates
   back to the operator's dtype (P-008, no rule yet; see P-018 for the deliberate version of the
   same idea). Expect `instruction_count` down and `compute_throughput_pct` up. Re-check the
   tolerance on the longest reduction in the spec, not on a small shape.

4. **A short loop body with known trip count** — unroll by a small factor that divides the trip
   count (P-009, no rule yet). Expect `instruction_count` per element down and `stall_memory`
   down, but watch `registers_per_lane`: an unroll that crosses the pressure threshold loses more
   than the loop control saved.

5. **Elements wider than the arithmetic needs, with tolerance to spare** — compute in a narrower
   dtype while accumulating wide (P-018, no rule yet). Expect `device_mem_bytes` down and
   `compute_throughput_pct` up. This is the only tactic here that can also lower the bound, which
   is why it is worth trying even though it is a correctness-adjacent change.

6. **A reduction that stages every partial through device_mem** — fold within the scheduling group
   first (P-019, no rule yet). Expect `scratchpad_throughput_pct` up and `stall_barrier` down, and
   `instruction_count` per output element down.

## Do not

- **Add scratchpad tiling to a compute-bound kernel.** A-001: with no reuse to exploit and no
  memory pressure to relieve, a staged copy is pure addition. The measured symptom is
  `scratchpad_bytes_per_workgroup` up and attainment flat.
- **Change the work-group size to raise occupancy.** A-002: on a saturated pipe the extra
  residency has nothing to hide, and it can reduce the reuse the larger group had.
- **Assume the compiler already fused, unrolled or strength-reduced.** Check the disassembly
  before and after; a change that was already there must be reported as not implemented rather
  than counted as a confirmation.

## Stop

- Stop when attainment reaches the campaign target for this `hw_id`.
- Switch to `divergence` if the after-profile shows `branch_divergence_pct` above threshold: the
  compute ceiling was partly masking, and unmasking it moves the bottleneck.
- Switch to `smem-conflict` if `scratchpad_conflict_ratio` fires and the compute pipe is fed by
  scratchpad reads; a serialised feed looks like a compute ceiling from the utilisation counter.
- Switch to `plateau` when every tactic above is refuted, or when the remaining operations are
  genuinely required by the operator's definition. At that point only an algorithm change —
  fewer operations for the same result — can move the bound.
