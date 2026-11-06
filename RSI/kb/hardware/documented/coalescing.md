---
id: hw-doc-coalescing
title: Contiguity, transaction granularity and sectors per request
store: hardware/documented
source:
  - docs/vocabulary.md section 2 (sectors_per_request and its recorded granularity)
  - _src/rsi-build-plan.md section 11 (coalescing penalty and load width microbenchmarks)
  - evaluator/microbench/kernels.py (coalescing_penalty, load_width)
  - "Hennessy and Patterson, Computer Architecture: A Quantitative Approach, chapter 2 (memory interleaving and burst transfers)"
last_reviewed: 2026-11-05
---

# Coalescing

## The concept

`device_mem` is not addressed a byte at a time. A request from a `work_group` is served in
transactions of a fixed granularity, and every transaction that carries at least one useful byte
is paid for in full. When the bytes one request needs are contiguous and aligned, they fall into
the minimum number of transactions. When they are strided or scattered, the same useful bytes
are spread over more transactions, and the extra transactions are pure waste: the bandwidth is
spent, the bytes are not used.

`sectors_per_request` is exactly this ratio, in canonical form: device-memory transactions per
request, where 1.0 is ideal contiguity. The adapter records the transaction granularity it used
next to the number, because the same access pattern produces a different ratio on a device with
a different granularity. That is the whole reason the metric is expressed as a ratio rather than
as a byte count.

## The three parameters that decide it

- **Contiguity.** The addresses touched by one request, in address order. Contiguous means the
  neighbouring lanes of a request touch neighbouring addresses.
- **Alignment.** Where the run starts relative to the transaction boundary. A contiguous but
  misaligned run straddles one extra transaction at each end; for short runs that is most of the
  cost.
- **Granularity.** The smallest transaction the device issues. Fixed per device, recorded by the
  adapter. A pattern that is perfectly contiguous at one granularity can still be split at
  another.

## Why the penalty curve is measured, not assumed

The naive model — "strided access costs N times more for stride N" — is wrong in both
directions, and the error is device-specific:

1. **Transaction granularity differs.** A stride that wastes nothing on a device with a wide
   granularity may waste three quarters of every transaction on a device with a narrow one.
2. **The hardware may merge or split requests.** Some devices coalesce adjacent requests from
   neighbouring lanes before they reach the memory controller, and some do not; the merging
   window is a device property.
3. **The cache absorbs part of the penalty.** `onchip_cache` catches repeated strided access
   whose working set fits, so the measured penalty at small sizes is lower than at large sizes
   for the same stride. A single "cost of stride 4" number hides that curve.

The `coalescing_penalty` microbenchmark therefore sweeps stride 1, 2, 4 and up to random and
reports throughput versus stride for this device, and `load_width` compares narrow, aligned and
wide loads. Those two curves are what a playbook points at when it says "coalescing is worth
trying here": the expected gain is read off the measured curve for the current `hw_id`, never
assumed from the stride.

## The two shapes of the fix

- **Change the layout so the existing traversal becomes contiguous.** The classic case is an
  array of structures read field by field: every field access walks one element's stride. Split
  it into one array per field and each traversal becomes contiguous. See pattern `P-001`.
- **Change the traversal so the existing layout is read contiguously.** When the layout is
  fixed by an external interface, reorder the loop nest so that neighbouring lanes consume
  neighbouring addresses, and let the reduction or transpose be paid for once. See `P-005`.

Either way, the counter to watch is `sectors_per_request`, and the second counter is
`device_mem_throughput_pct` or `device_mem_bytes`: a layout change that lowers the ratio but
raises total bytes has moved the problem, not removed it.

## Device-specific notes

- On devices whose memory path issues burst transfers, the effective granularity can be larger
  than the documented transaction size, and the useful measure is the burst length the adapter
  records; on devices with a narrower path the same code shows a lower ratio.
