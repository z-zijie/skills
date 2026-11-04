# 消融轴清单

按"先看瓶颈落到哪"排序。每一条都是一个 `-D` 宏，改完重编译重测，不与别的改动叠加。

实测前提（Ascend950PR，56 AIV，UB 248 KiB，L2 128 MiB）：多数单 case kernel 的真实瓶颈不在算法，而在**下发地板**与**标量开销**。先量，再选组。

## 底线：下发地板

56 block 的空 kernel 地板约 1.3 µs，其中核内指令只占 ~0.53 µs，其余是逐 block 派发（~14 ns/block）。1M 级小 case 的全部时间常常只有 3 µs，地板占了四成。

推论：
- 小 case 的优化空间受地板压制，方向应放在"减少 block 数带来的派发"或"让首块 DMA 不与 Init 串行"，而不是继续压向量指令。
- 报告里写清实测上限。地板 + 实测峰值带宽算出的分数天花板，比"还没到 91 分"有用得多。

## A 组：标量开销

现象：scalar 占比 40%+，向量与带宽都不高。

- `AscendC::Mutex` → `SetFlag/WaitFlag`。Mutex 每次锁都走标量路径；事件标志便宜。注意事件 id 只有 8 个（`2*PDEPTH <= 8`）。
- 热循环内不构造 `LocalTensor`：循环前取好 `__ubuf__ T*` 基址，循环内只做指针加法。`LocalTensor` 构造 + `GetPhyAddr` 是每 tile 固定标量成本。
- 消 64 位除法：`fullTiles` / `tailElems` 由 host 算好传入，kernel 里不做 `int64` 除法与取模。
- `InitSocState()` 是否可省：单独测一条，收益通常很小但成本也小。
- kernel 参数个数：每个参数都是一次 scalar 载入 + 一次 `PARA_BASE` 偏移计算。

## B 组：核间不均衡

现象：逐核 duration 从 1.45 到 3.99 µs（spread 93%），沿 blockIdx 单调。

- 反转 blockIdx 映射：`bid = GetBlockNum() - 1 - bid`。实测能把 spread 从 58% 压到 52%，但 Duration 常只动 1%。
- 互质交错映射：`bid = (bid * K) % blocks`，K 取 3/5/9。常是负结果。
- subcore 均衡：把每核工作量按 AIC/AIV 子核重排。

注意：spread 是**线索**不是判据。它本身抖 6 个点，且 Duration 由最慢核决定——spread 改善而 Duration 不动，说明瓶颈已经移位。

## C 组：tiling 与流水

- 每核趟数 2/3/4/6/8（`-DTILES=`）。趟数少 = 描述符大、DMA 少；趟数多 = 流水更深。两极都实测过负结果，中间值随 case 变。
- ping-pong 深度（`-DPDEPTH=`）：2/4/8。实测 4 与 8 在噪声内等价。
- UB 预算：`UB_NUM/UB_DEN` 取 15/16、7/8、31/32。预算越紧 tile 越小、流水越浅。
- 每核最小 tile 数（`-DMIN_TILE=`）：控制尾块粒度。
- 写回 cache 模式（`CACHE_MODE_LAST`）：小 case 上常见负收益，但要在自己的 case 上量。

## D 组：向量体

- 原生低精度指令：fp16 域的 `VEXP.f16` 把 `VLD → VCVT → VEXP.f32 → VCVT → VST` 五条压到三条编译产物，且一个 vreg 装 128 个 fp16（fp32 是 64），重复次数减半。
- **精度判定先于性能判定**：fp16 域内算 exp 的误差是 1–3 ulp（~1e-3），可能过不了 `MERE < 9.77e-4` 的门。两版都实现，只有过了门的那版算数。
- 掩码：`PLT.b32 P1, S64` 的 `S64` 是谓词元素数，fp16 路径要设 128；`VLOOPV2` 的 repeat 数按每寄存器元素数重算。

## 已证伪（不要重复交学费）

在本机 1M fp16 exp case 上实测无收益或负收益：

- 原地计算（输入输出共用 UB 缓冲），tile 翻倍
- 512 B 边界对齐替代 64 元素对齐
- block 数扫描 1/7/14/28/56/84/112/168（56 最优）
- 少核、超额订阅、MTE2 拆分、首块预取
- 写穿 L2（case13 +83%）

负结果同样是产出：写进台账，下一轮从先验出发。

## 工具链摩擦（不是算法问题，但会吞掉半天）

- `fast.sh` / `run_evaluation.sh` 会 `pip install --force-reinstall` 到共享 evalenv，容易静默失败并挂住；直调链路正是为了绕开它。
- 探针脚本的 shebang 在 `cnb-env` 下会解析到没有 `torch_npu` 的系统 python；显式指定解释器。
- 设备二进制是私有 ISA，`objdump -m aarch64` 零命中；反汇编要用 CANN 自带的 dav-3510 target 工具。
