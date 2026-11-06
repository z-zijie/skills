# Canonical vocabulary

Every skill, schema and store in this framework speaks one vocabulary. Adapters translate
between this vocabulary and whatever a particular device, runtime or profiler calls the same
thing. Nothing outside `evaluator/profiler/adapters/` and `kb/hardware/` names a vendor.

Two rules keep it portable:

- A canonical name is a **role in the machine**, never a vendor term. `device_mem` is the
  off-chip memory of any accelerator (HBM, GDDR, …), `scratchpad` is the software-managed
  on-chip memory of any accelerator.
- A canonical metric carries a **unit and a direction**, so a rule distilled on one device
  stays readable on another. A signature tag is a threshold decision over canonical metrics,
  so a threshold file per hardware id is enough to move the whole system to a new device.

## 1. Memory hierarchy

| Canonical | Meaning | Typical vendor names |
| --- | --- | --- |
| `device_mem` | off-chip device memory | HBM / GDDR; Ascend HBM |
| `onchip_cache` | hardware-managed on-chip cache levels | L1 / L2; Ascend L2, ICache, DCache |
| `scratchpad` | software-managed on-chip storage with a declared size and banking | CUDA shared memory, Ascend Unified Buffer / L1, OpenCL local memory |
| `register_file` | per-lane registers | CUDA registers, Ascend vector / scalar registers |

Byte formulas in `spec.yaml` are written with these names, and the roofline uses
`device_mem` bytes only: it is the one level whose bandwidth is a fixed device property.

## 2. Canonical metrics

`evaluator/profiler/adapters/` maps each canonical metric to a profiler expression and
declares its provenance. A metric an adapter cannot produce is reported as `null`, never
guessed, and a hypothesis that names it is rejected by `propose-hypothesis` validation.

| Canonical metric | Unit | Better | Meaning |
| --- | --- | --- | --- |
| `device_mem_throughput_pct` | % of measured peak | lower at same speed | device-memory bandwidth utilisation |
| `device_mem_bytes` | byte | — | bytes moved to and from device memory in the profiled window |
| `cache_hit_pct` | % | higher | hit rate of the outermost hardware-managed cache level |
| `cache_throughput_pct` | % of peak | — | pressure on the hardware-managed cache levels |
| `compute_throughput_pct` | % of peak | — | utilisation of the dominant compute pipe |
| `occupancy_pct` | % | higher | resident work items over the device maximum |
| `sectors_per_request` | transactions / request | lower | device-memory transactions per request; 1.0 is ideal contiguity. The adapter's transaction granularity (32 B on NVIDIA, 32 B or burst on Ascend MTE) is recorded next to the number. |
| `stall_memory` | fraction of issue slots | lower | issue slots idle waiting on device memory |
| `stall_barrier` | fraction of issue slots | lower | idle waiting on a work-group barrier |
| `scratchpad_throughput_pct` | % of peak | — | pressure on scratchpad bandwidth |
| `scratchpad_conflict_ratio` | conflicts / access | lower | scratchpad bank or vault conflicts per access |
| `scratchpad_bytes_per_workgroup` | byte | — | static scratchpad allocation per work-group |
| `registers_per_lane` | register | lower at same speed | register pressure per work-lane |
| `spill_bytes` | byte | lower | register spills to local memory |
| `branch_divergence_pct` | % | lower | lanes that take a different path than their group |
| `launch_overhead_us` | µs | lower | fixed launch cost measured by the calibration suite |
| `instruction_count` | instruction | lower | instructions executed in the kernel |

`gpu_*`/`npu_*`-prefixed metric names are not allowed in schemas. If a device exposes
something with no canonical counterpart yet, it is added here first, together with its
adapter mapping, and only then used by skills.

## 3. Signature classes and tags

A signature is a class plus a tag set plus the metrics behind it. Tags are produced by
comparing canonical metrics with thresholds from `kb/hardware/measured/thresholds.yaml`
for the current hardware id.

| Class | Condition (default thresholds) |
| --- | --- |
| `memory_bound` | `device_mem_throughput_pct ≥ 60` and `compute_throughput_pct < 60` |
| `compute_bound` | `compute_throughput_pct ≥ 60` and the compute term sets the roofline bound |
| `latency_bound` | neither above 60, and `attained_pct < 60` |
| `mixed` | both above 60 |

| Tag | Condition |
| --- | --- |
| `mem_saturated` | `device_mem_throughput_pct ≥ 80` |
| `uncoalesced` | `sectors_per_request ≥ 1.5` |
| `cache_hit_low` | `cache_hit_pct < 60` |
| `compute_saturated` | `compute_throughput_pct ≥ 80` |
| `occupancy_low` | `occupancy_pct < 50` |
| `memory_stall_high` | `stall_memory ≥ 0.4` |
| `scratchpad_conflict` | `scratchpad_conflict_ratio ≥ 1.2` |
| `divergence_high` | `branch_divergence_pct ≥ 20` |
| `register_pressure` | `registers_per_lane ≥ 0.75 × device maximum` |
| `spills` | `spill_bytes > 0` |
| `launch_dominated` | `launch_overhead_us ≥ 10 %` of measured time |
| `plateau` | attainment stalled over the last 3 experiments |

`dram_saturated`, `l2_hit_low`, `sm_throughput_pct` and similar vendor-flavoured aliases are
accepted on read by the validators, mapped to their canonical form, and flagged as legacy.
Write canonical names.

## 4. Hardware identity

| Field | Meaning |
| --- | --- |
| `hw_id` | stable local id, e.g. `accel-a`, `accel-b`. Rules bind to it, so a device swap demotes them. |
| `array_module` | which array runtime the portable backend drives. This is what decides whether a measurement describes the device or the host: a device that declares an accelerator while `array_module: numpy` runs is measuring the host CPU, and the calibration reports say so rather than recording a peak for the wrong machine. |
| `hw_class` | `gpu`, `npu`, `other` — informational only; no logic branches on it. |
| `env_fingerprint` | device model, driver / runtime version, compiler version and flags, OS. Stored with every measurement and every ledger entry. |

## 5. Work-unit vocabulary

| Canonical | Meaning | Typical vendor names |
| --- | --- | --- |
| `work_lane` | the smallest independently executing element | CUDA thread, Ascend vector lane |
| `work_group` | a barrier-synchronised group sharing `scratchpad` | CUDA block / CTA, Ascend block, OpenCL work-group |
| `wave` | one scheduling quantum of resident work groups | CUDA warp/schedule unit, Ascend AIC/AIV core |

Skills say "work-group size", not "block size"; they say "lane", not "thread". The mapping
table above is the whole of the translation layer.
