# msopprof capability boundaries on Ascend950PR (dav-3510)

Each item below was measured on `Ascend950PR_9579` (`NpuArch=3510`, `Short_SoC_version=Ascend950`, CCEC `dav-c310-cube` / `dav-c310-vec`) with CANN `9.3.0~weekly.20261009.01`.

## Metric sets that work

Collected one at a time with `--aic-metrics=<NAME>` against a `vector_add` binary:

| Metric | Result | Output |
|---|---|---|
| `Default` | works | the full set of 7 CSVs |
| `PipeUtilization` | works | PipeUtilization.csv |
| `ArithmeticUtilization` | works | ArithmeticUtilization.csv |
| `Memory` / `MemoryUB` / `MemoryL0` | works | their respective CSVs |
| `L2Cache` | works | L2Cache.csv |
| `ResourceConflictRatio` | works | ResourceConflictRatio.csv |
| `BasicInfo` | works | OpBasicInfo.csv only |
| `Roofline` | works | 7 CSVs + the `0x0D` roofline record |
| `Occupancy` | works | OpBasicInfo + visualize_data |
| `KernelScale` | works | 7 CSVs |
| `Source` | partly fails | needs a `-g` build, otherwise "Failed to parse pc to code" |
| `PCSampling` | partly fails | as above; hot-spot computation fails |
| `TimelineDetail` | **fails** | "Parse kernel data failed"; all CSVs empty |
| `InstrTimeline` | works, limited | trace.json; source attribution needs `-g` |
| `PipeTimeline` | works | trace.json with an `X` event per core and pipe |

### Combining metric sets has a ceiling

Passing eleven groups at once (`BasicInfo,Roofline,Occupancy,KernelScale` on top of the core seven) makes `msopprof` return 0 and report success while **every CSV counter column is `NA`**; `Task Duration` still carries a value. This is the most dangerous failure class: it looks successful and the data is empty.

The tool flags such a capture as `no data (all pipe counters empty)`. Avoid it by keeping a pass to seven groups or fewer (the tool's default set is that size) or by changing `--replay-mode`.

## Constraints that hold only on this part

- **No biprof.** `biprof` is a `cannsim` (simulator) Python package, present only in CANN 9.1.0 under `site-packages/cannsim/biprof`, and it needs `plotly`, which the image does not have. Its job is to read simulator databases and produce hotspot reports. On real hardware the equivalent and far more complete capability is `msopprof`.
- **`msprof op` is `msopprof`.** `msprof --help` states it plainly: "subcommand for operator optimization: op — Use binary msopprof to operator optimization (msprof op ...)". `msprof op ...` and calling `msopprof ...` directly are equivalent; going straight to `msopprof` skips a forwarding layer.
- **Application-level `msprof` crashes on this host's Python.** After starting, `msprof` (the non-`op` mode) needs `python3` to parse results, and the image's `/opt/buildtools/python-3.12.9` has a `_ctypes` that does not match the interpreter:
  ```
  ImportError: .../_ctypes.cpython-312-x86_64-linux-gnu.so: undefined symbol: _PyErr_SetLocaleString
  ```
  The data is still collected (`PROF_*/host/data`, `device_0/data`) but cannot be parsed into CSVs. So **application-level (end-to-end) analysis is unavailable on this host**; kernel-level analysis through `msopprof` is unaffected.
- **`msopprof`'s kernel name is the mangled name.** `--kernel-name` wants the full signature (e.g. `_Z10add_kernelIfEvPhS0_S0_llj`) and matches it as a glob, not a substring. The tool's `--kernel` wraps the pattern in `*` so a bare name behaves as expected.
- **`msopprof` passes its own arguments to the program.** An operator that takes arguments (the matmul tutorial needs `m k n`) must receive them, otherwise the child process exits immediately and the capture is empty.

## Unit conventions (easy to get wrong)

- `*_datas(KB)` is **KiB** (1024 B)
- `*(GB/s)` is **GiB/s** (1024^3 B/s)

Verification: `57.375 KiB / 2.267879 us`, converted with `1024` and `1024^3`, gives 24.1270 GB/s, matching the published `24.126980` exactly; using decimal GB instead is off by 4.9%. The tool works in KiB/GiB throughout, and `derive_bw_peaks()` carries a comment explaining why `1e9` is wrong.

## visualize_data.bin container format

```
repeat:
  u64 payload_len    little endian, excludes this header
  u8  kind           05=identity 06=AIV subblock 07=AIC subblock
                     08=per-core memory map 09=per-block tables
                     0C=op_detail 0D=roofline
  u8  variant        varies per capture; not part of the identity
  b'ZZ'              magic
  payload_len-2 B    one JSON object, NUL padded
```

The second byte (variant) differs between captures: the same identity record appears as `05/00` or `05/02`, and per-block tables as `09/00`, `09/01` or `09/02`. **Classify by the first byte only.**

`0x0D` appears only when the `Roofline` metric was collected; its content is `{"advice": "latency bound:pipeline caused", "multiple_rooflines": [...]}`.

A different record kind, `0x01`, is not JSON: it is a length-prefixed string table of source paths and source text, used for source-level attribution.

## Where the platform constants live

`$ASCEND_HOME_PATH/x86_64-linux/data/platform_config/Ascend950PR_9579.ini`. Values measured on this part:

| Key | Value |
|---|---|
| `ai_core_cnt` / `cube_core_cnt` | 28 / 28 |
| `vector_core_cnt` | 56 |
| `cube_freq` / `vec_freq` | 1650 / 1650 MHz |
| `l1_size` / `ub_size` | 524288 / 253952 B |
| `l0_a_size` / `l0_b_size` / `l0_c_size` | 65536 / 65536 / 262144 B |
| `l2_size` | 134217728 B (128 MiB) |
| `l1_to_ub_rate` / `ub_to_l2_rate` / `ub_to_ddr_rate` | 128 / 128 / 128 |
| `ddr_rate` (`[AICoreMemoryRates]`) / `ddr_rate` (`[VectorCoreMemoryRates]`) | 31 / 16 |
| `io_die_num` (`[DieInfo]`) | 2 |

Note that `[AICoreMemoryRates]` and `[VectorCoreMemoryRates]` disagree on `ddr_rate` (31 against 16). The tool uses only peaks inverted from the capture as denominators and keeps the ini values as a cross-check, so picking the wrong section cannot corrupt a percentage.

## Metric definitions from the CANN source

`tools/profiler/profiler_tool/analysis/common_func/constant.py` holds two key structures:

- `PmuMetricCalculate.pmu_metric_calculate_with_freq()` - the general formula for bandwidth metrics:
  ```
  value = float_bit * pmu * pip_size * scalar / (task_cyc / freq) / 8589934592.0
  ```
  `8589934592 = 8 * 1024^3`, i.e. **8 GiB**. This independently confirms that GB here means GiB: the divisor is 1024^3, not 1e9.

- `PmuMetricCalculate.pmu_metric_calculate_without_freq()` - ratios:
  ```
  value = float_bit * pmu / task_cyc
  ```
  A pure ratio with no unit conversion, which is why these are tier B (exact derivation).

Measured `pip_size * scalar` per bandwidth metric:

| Metric | pip_size | scalar |
|---|---:|---:|
| `ub_read_bw` | 256 | 4 |
| `l2_read_bw` | 256 | 8 |
| `l1_read_bw` | 256 | 16 |
| `main_mem_read_bw` | 8 | 8 |
| `ub_write_bw_scalar` | 128 | 1 |
| `fixp2ub_write_bw` | 256 | 8 |

Raw PMU event ids:

- `Constant.L2_CACHE_EVENTS`: the 32 event ids used for L2 collection
- `Constant.SOC_PMU_EVENTS`: SOC-level events (`0x2, 0x8a, 0x8b, 0x8c, 0x8d`)
- `msconfig/soc_pmu_config.py`: the default event triple `request_events=0x8a`, `hit_events=0x8c,0x8d`, `miss_events=0x2`

These are the ids programmed into the hardware. `msopprof`'s `--aic-metrics` is a higher-level preset and does not expose per-event selection, which is why some metrics CANN can compute have no collection path.

### Metric index order (from the Ascend/msprof source)

`analysis/csrc/domain/entities/metric/include/metric.h` defines the index enums, and `metric.cpp` binds each enum to its header string. In a PMU result vector the position is the enum value, so this is the authoritative ordering:

- `PipeLineUtIndex`: VecTime, VecRatio, MacTime, MacRatio, ScalarTime, ScalarRatio, Mte1Time, Mte1Ratio, Mte2Time, Mte2Ratio, Mte3Time, Mte3Ratio, FixPipeTime, FixPipeRatio, ICacheMissRate
- `MemoryIndex`: UBReadBw, UBWriteBw, L1ReadBw, L1WriteBw, MainMemReadBw, MainMemWriteBw, L2ReadBw, L2WriteBw
- `MemoryL0Index`: L0aReadBw, L0aWriteBw, L0bReadBw, L0bWriteBw, L0cReadBw, L0cWriteBw, L0cReadBwCube, L0cWriteBwCube
- `ResourceConflictIndex`: VecBankGroupCfltRatio, VecBankCfltRatio, VecRescCfltRatio
- `MemoryUBIndex`: UbReadBwVector, UbWriteBwVector, UbReadBwScalar, UbWriteBwScalar, Fixp2UbWriteBw (v6 only)
- `L2CacheIndex`: WriteCacheHit, WriteCacheMissAllocate, R0ReadCacheHit, R0ReadCacheMissAllocate, R1ReadCacheHit, R1ReadCacheMissAllocate, then the **v6-only** ReadLocalL2Hit, ReadLocalL2Miss, ReadLocalL2Victim, WriteLocalL2Hit, WriteLocalL2Miss, WriteLocalL2Victim
- `MemoryAccessIndex`: ReadMainMemoryData, WriteMainMemoryData, GmToL1Data, L0CToL1Data, L0CToGmData, GmToUbData, UbToGmData

The `L2CacheIndex` v6 group is what the CSV exposes as the close/far hit/miss/victim split: dav-3510 is a v6-class part, so the local/remote hit-miss-victim counters are the ones actually populated.

## InstrTimeline inputs and outputs

Input constraints (verbatim errors measured):

- `--instr-timeline-pipe` accepts only `cube|fixp|vector|mte1|mte2|mte3`; `scalar` is rejected:
  ```
  [ERROR] --instr-timeline-pipe only support pipes in [cube fixp vector mte1 mte2 mte3]
  ```
- Mutually exclusive with `PipeTimeline`:
  ```
  [ERROR] Unexpected argument --aic-metrics, 'PipeTimeline' and 'InstrTimeline' cannot be specified together.
  ```

Output artifacts (one collection):

| File | Size order | Content |
|---|---:|---|
| `trace.json` | ~10 KB | Chrome tracing; `X` events carry `args.pc_addr` and `dur` |
| `dump/dfx_region_map.txt` | ~3 KB | pc to pipe/opcode map |
| `dump/pc_start_addr.txt` | 14 B | absolute base address |
| `dump/timeline.bin.0` | ~25 MB | raw timeline (undocumented format) |
| `dump/OperandRecord.bin` | ~190 MB | operand record (undocumented format) |

The last two have no public format and this tool does not decode them; everything usable already comes from `trace.json` plus the region map.

## Root cause of application-level msprof failure

```
ImportError: /opt/buildtools/python-3.12.9/lib/python3.12/lib-dynload/_ctypes.cpython-312-x86_64-linux-gnu.so:
  undefined symbol: _PyErr_SetLocaleString
```

`msprof` in non-`op` mode uses the host `python3` to parse the capture, and the image's Python is self-built with a `_ctypes` that does not match the interpreter. The data itself is collected (`PROF_*/host/data` and `device_0/data` both have content), it simply cannot be parsed. Using CANN's bundled `python` or repairing `_ctypes` would fix it; this tool does not depend on that path.

## Runtime and driver library reverse engineering (2026-11-04 16:5x)

Asked to check whether the runtime and driver library chains expose finer counters than the profiler does.

### Libraries

| Library | Path | Size order |
|---|---|---|
| runtime (ACL) | `x86_64-linux/lib64/libascendcl.so` | 268 KB, very few exported symbols (one prof-related) |
| profiler API | `x86_64-linux/lib64/libprofapi.so` | 76 `*prof*` exported symbols |
| driver HAL | `/usr/local/Ascend/driver/lib64/driver/libascend_hal.so` | 1757 exported symbols |
| mspti (newer profiling interface) | `tools/mspti/lib64/libmspti.so` + `include/mspti*.h` + py wheel | activity-based |

### libprofapi.so: per-op timestamps, no PMU

Exported symbols fall into three groups:

- **Lifecycle**: `ProfAclInit/Start/Stop/Finalize/SetConfig/Subscribe/UnSubscribe`
- **Instrumentation**: `ProfAclPush/Pop/RangeStart/RangeStop/RangePushEx/RangePop/Mark/MarkEx`, `ProfAclSetStamp*`
- **Retrieval**: `ProfAclGetOpVal`, `ProfAclGetOpTime`, `ProfAclGetId/GetOpAttriVal/GetCompatibleFeatures`

The subscribe path in `x86_64-linux/include/acl/acl_prof.h` (623 lines) gives **start/end/duration for every operator**:

```c
aclprofGetOpStart(opInfo, len, index)      // uint64_t
aclprofGetOpEnd(opInfo, len, index)        // uint64_t
aclprofGetOpDuration(opInfo, len, index)   // uint64_t
aclprofGetOpNum / GetOpName / GetOpType / GetModelId / GetOpFlag
```

That is an **operator-level** timeline, not block level, and it carries no PMU counters.

### libascend_hal.so: no PMU exposure

Of 1757 exported symbols the only PMU-related one is `dmanage_get_pmu_voltage` (power management, not performance counting), and the only profiling-related ones are `drv_event_proc` and `drv_registert_event_proc` (event callback registration). **There is no usable PMU counter read interface at the driver HAL layer** - counter access lives inside the profiler driver and is not exported through HAL.

### mspti: kernel-level timestamps, no counters

`include/mspti_activity.h` defines 13 activity kinds (KERNEL / API / MEMCPY / HCCL / MARKER ...). The `msptiActivityKernel` structure:

```c
msptiActivityKind kind;   // MSPTI_ACTIVITY_KIND_KERNEL
uint64_t start;           // ns
uint64_t end;             // ns
struct { uint32_t deviceId, streamId; } ds;
uint64_t correlationId;
const char *type, *name;
```

The valuable part: **nanosecond kernel timestamps** plus `streamId` and `correlationId`, which the CSV's single `Task Duration` does not carry. There is no block-level breakdown and no PMU counter.

### Conclusion

None of the three paths offers more **hardware counters** than `msopprof`:

| Wanted | runtime | driver | mspti | msopprof |
|---|---|---|---|---|
| PMU counters | no | no | no | yes (7 preset groups) |
| sector / coalescing | no | no | no | no |
| outstanding / snoop | no | no | no | no |
| per-op start/end | yes (aclprof) | no | yes (kernel level) | total only |
| per-block start/end | no | no | no | yes (PipeTimeline, subset of cores) |
| stream / correlation association | yes | no | yes | no |

So: **counters come from msopprof and nowhere else, and it will not yield more than its preset groups**; the genuine increment available from runtime/mspti is timestamp granularity and stream association, not counters. If stream-level queueing analysis is wanted later, mspti is the right entry point - that is scheduling semantics, not hardware counting.
