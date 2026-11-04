# Ascend950PR（dav-3510）上的 msopprof 能力边界

本文记录在 `Ascend950PR_9579`（`NpuArch=3510`、`Short_SoC_version=Ascend950`、CCEC `dav-c310-cube` / `dav-c310-vec`）上逐项实测的结果。CANN `9.3.0~weekly.20261009.01`。

## 有效的 metric 集合

逐个 `--aic-metrics=<NAME>` 单独采集一个 `vector_add` 可执行文件，结果：

| metric | 结果 | 产出 |
|---|---|---|
| `Default` | 可用 | 7 份 CSV 全套 |
| `PipeUtilization` | 可用 | PipeUtilization.csv |
| `ArithmeticUtilization` | 可用 | ArithmeticUtilization.csv |
| `Memory` / `MemoryUB` / `MemoryL0` | 可用 | 各自 CSV |
| `L2Cache` | 可用 | L2Cache.csv |
| `ResourceConflictRatio` | 可用 | ResourceConflictRatio.csv |
| `BasicInfo` | 可用 | 仅 OpBasicInfo.csv |
| `Roofline` | 可用 | 7 份 CSV + `0x0D` roofline 记录 |
| `Occupancy` | 可用 | OpBasicInfo + visualize_data |
| `KernelScale` | 可用 | 7 份 CSV |
| `Source` | 部分失败 | 需要 `-g` 编译，否则 "Failed to parse pc to code" |
| `PCSampling` | 部分失败 | 同上；hot spot 计算失败 |
| `TimelineDetail` | **失败** | "Parse kernel data failed"；CSV 全空 |
| `InstrTimeline` | 可用（有限） | trace.json，需 `-g` 才有源码归属 |
| `PipeTimeline` | 可用 | trace.json，每核每流水的 `X` 事件 |

### 组合采集有上限

把 `BasicInfo,Roofline,Occupancy,KernelScale` 一起塞进一个 `--aic-metrics` 列表（共 11 组）时，`msopprof` 返回 0 且报告成功，但**所有 CSV 的计数列全是 `NA`**，`Task Duration` 仍有一个值。这是最危险的一类失败：看起来成功，数据是空的。

工具对这种 capture 标注 `no data (all pipe counters empty)`。规避方式：采集组数不超过 7 组（工具的默认集合就是这个规模），或用 `--replay-mode=application`。

## 只在这个芯片上成立的约束

- **没有 biprof**。`biprof` 是 `cannsim`（仿真器）的 Python 包，仅在 CANN 9.1.0 的 `site-packages/cannsim/biprof` 存在，且需要 `plotly`（镜像里没装），作用是读仿真数据库生成热点报告。真机上等价且更全的能力在 `msopprof`。
- **`msprof op` 就是 `msopprof`**。`msprof --help` 里 "subcommand for operator optimization: op — Use binary msopprof" 说得很清楚，`msprof op ...` 与直接调 `msopprof ...` 等价。直接用 `msopprof` 少一层转发。
- **`msprof` 应用级采集在本机 Python 上会崩**。`msprof`（非 `op` 模式）启动后需要 `python3` 解析结果，而镜像里 `/opt/buildtools/python-3.12.9` 的 `_ctypes` 与解释器不匹配：
  ```
  ImportError: .../_ctypes.cpython-312-x86_64-linux-gnu.so: undefined symbol: _PyErr_SetLocaleString
  ```
  数据仍然采到了（`PROF_*/host/data`、`device_0/data`），但无法解析成 CSV。因此**应用级（端到端）分析在本机不可用**；kernel 级分析走 `msopprof` 完全没有这个问题。
- **`msopprof` 的 kernel 名是 mangled 名**。`--kernel-name` 需要完整签名（如 `_Z10add_kernelIfEvPhS0_S0_llj`）；工具的 `--kernel` 支持子串匹配，会先看采集结果再用子串过滤统计。
- **`msopprof` 要求程序能接受它自己的参数**。带参数的算子（如 matmul 教程要 `m k n`）必须把参数一起传，否则子进程直接退出、采集为空。

## 单位约定（易错）

- `*_datas(KB)` 是 **KiB**（1024 B）
- `*(GB/s)` 是 **GiB/s**（1024³ B/s）

实测校验：`57.375 KiB / 2.267879 us` 用 `1024` 与 `1024³` 换算得 24.127 GB/s，与 CSV 里公布的 `24.126980` 完全一致；换成十进制 GB 会差 4.9%。工具统一按 KiB/GiB 处理，`derive_bw_peaks()` 里有一行注释说明为什么不能用 `1e9`。

## visualize_data.bin 容器格式

```
repeat:
  u64 payload_len    little endian, 不含本头部
  u8  kind           05=identity 06=AIV subblock 07=AIC subblock
                     08=per-core memory map 09=per-block tables
                     0C=op_detail 0D=roofline
  u8  variant        随采集变化，不是身份的一部分
  b'ZZ'              magic
  payload_len-2 B    一个 JSON 对象，NUL padding
```

第二个字节（variant）在每个 capture 里都可能不同：同一个 identity 记录在不同采集里是 `05/00` 或 `05/02`，per-block tables 是 `09/00` 或 `09/01` 或 `09/02`。**只按第一个字节判类型**。

`0x0D` 只在采了 `Roofline` metric 时出现，内容是 `{"advice": "latency bound:pipeline caused", "multiple_rooflines": [...]}`。

## 平台常量位置

`$ASCEND_HOME_PATH/x86_64-linux/data/platform_config/Ascend950PR_9579.ini`。本机实测值：

| 键 | 值 |
|---|---|
| `ai_core_cnt` / `cube_core_cnt` | 28 / 28 |
| `vector_core_cnt` | 56 |
| `cube_freq` / `vec_freq` | 1650 / 1650 MHz |
| `l1_size` / `ub_size` | 524288 / 253952 B |
| `l0_a_size` / `l0_b_size` / `l0_c_size` | 65536 / 65536 / 262144 B |
| `l2_size` | 134217728 B（128 MiB） |
| `l1_to_ub_rate` / `ub_to_l2_rate` / `ub_to_ddr_rate` | 128 / 128 / 128 |
| `ddr_rate`（AICore 段）/ `ddr_rate`（VectorCore 段） | 31 / 16 |

注意 `[AICoreMemoryRates]` 与 `[VectorCoreMemoryRates]` 两段的 `ddr_rate` 不同（31 vs 16），工具只用采集数据反解出的峰值做分母，ini 值仅作交叉校验，避免选错段。
