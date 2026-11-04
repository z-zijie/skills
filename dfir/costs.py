"""Calibrated cost models per pipe.

All constants come from measurement on the Ascend950PR (dav-3510) using
HARDWARE COUNTERS (`aiv_total_cycles`), not wall-clock time: the counter has
1-4% spread where wall-clock has 7-14%, and 5% accuracy is unreachable at the
latter.

MTE2 (GM->UB) -- calibrated, M1
-------------------------------
Law fitted on 61 (K,S,B) points spanning B=1..56, S=32/64KB, K=1..2
(`cal5.py`, in-sample 3.23%, LOO-CV 3.29%):

    cycles = B*t_setup
           + B*S        * r1*(1 + (B/k1)^p1)      first copy
           + B*(K-1)*S  * r2*(1 + (B/k2)^p2)      each additional copy

Two things this captures that a naive "bytes / bandwidth" model cannot:

  * the per-core setup cost (t_setup) is paid once per core, not per byte;
  * an ADDITIONAL copy is cheaper per byte than the first at low core counts but
    dearer at high ones (r2's smaller knee), because it queues behind the first.
    Measured delta(K=2)-delta(K=1) per byte rises from 0.014 to 0.050 over B=1..56.

Other pipes are NOT yet calibrated (that is M1/M2 work); they carry
`calibrated=False` and the estimator warns when they are used, so a 5% claim is
never silently attributed to an unmeasured engine.
"""
import math
from dataclasses import dataclass


@dataclass
class MTE2Cost:
    # fitted on bsweep_hw.csv (65 pts, B=1..56, S=32/64KB, K=1..2)
    # LOO-CV 3.51%, median 1.55%, 77% within +-5%
    t_setup: float = 1788.0
    r1: float = 0.01608
    k1: float = 26.0
    p1: float = 1.20
    c_extra: float = 230.0        # size-independent per-additional-copy term
    r2: float = 0.00674
    k2: float = 12.0
    p2: float = 1.20
    calibrated: bool = True
    source: str = "hw-counters, 63 pts (2 topology-breakout excluded), LOO 3.91%"
    # EXCLUDED and why: (K=2, S=32KB, B>=52) jumps +175k cycles above the trend
    # -- an 73% step at B=48->52 that K=1 does not show (169k->172k). That is a
    # resource/topology threshold (4-byte-wide dual-copy streams saturating above
    # 48 cores), not a smooth function of bytes and cores. It is flagged here
    # rather than absorbed, so the estimator does not silently average across a
    # discontinuity. Modelling it is M1 follow-up work.

    def per_core_cycles(self, nbytes: int, copies: int, cores: int) -> float:
        """Cycles a SINGLE core spends doing `copies` copies of `nbytes` each,
        while `cores` cores run concurrently.

        NB the fitted law is expressed the other way round: what the counter
        records for a launch is the AIV cycle count with all cores running, and
        the fit found it proportional to B (=cores). So the per-core figure is
        the fitted total divided by cores.
        """
        return self.cycles(nbytes, cores, copies) / max(1, cores)

    # Steady-state check: pipeline marginal (757457) - MTE3 marginal (405141)
    # = 352316 for (MTE2 + VEC); subtracting VEC's 47310 leaves MTE2 at 305006,
    # against 307329 measured standalone -- within 0.7%. So MTE2 and MTE3 DO
    # overlap without a measurable surcharge on this hardware; there is nothing
    # to charge. (An earlier 1.16 contention factor came from a decomposition
    # that had double-counted a 4-store total as one store.)
    contention_when_shared: float = 1.0

    # In a STREAMING pipeline (many iterations, transfers kept in flight) the same
    # 100KB load costs 305006 cyc at B=56, versus 423880 when issued in isolation:
    # 28% cheaper, because the load fills bus slack that a lone transfer cannot.
    # Composing single-pipe isolated laws therefore over-predicts pipelines by
    # ~35%, which is exactly what happened before this factor existed.
    streaming_b56_cycles_per_100k: float = 305006.0

    def cycles_streaming(self, nbytes: int, cores: int) -> float:
        """Cost of one load inside a saturated pipeline, at 56 cores scaled by B."""
        B = max(1, cores)
        per_100k = self.streaming_b56_cycles_per_100k / 56.0 * B
        return per_100k * (nbytes / 102400.0)

    def cycles(self, nbytes: int, cores: int, copies: int = 1,
               shared_bus: bool = False) -> float:
        """Fitted cycle count for the launch, cores running concurrently."""
        B = max(1, cores)
        K = max(1, copies)
        first = nbytes * self.r1 * (1.0 + (B / self.k1) ** self.p1)
        # An additional copy is NOT simply "the same bytes again": measured
        # delta(K=2)-delta(K=1) has a size-independent part (c_extra, negative:
        # the extra copy amortises startup) plus a per-byte part that grows with
        # cores faster than the first copy does.
        extra = (K - 1) * (self.c_extra +
                           nbytes * self.r2 * (1.0 + (B / self.k2) ** self.p2))
        scale = self.contention_when_shared if shared_bus else 1.0
        return scale * B * (self.t_setup + first + extra)


@dataclass
class MTE3Cost:
    """MTE3 (UB->GM). Calibrated in M1 with hardware counters, mode-1 kernel.

    measured per-100KB store increment (K=8 minus K=4, divided by 4):
      B=56 -> 405k cycles/100KB; the B-scaling is much steeper than MTE2's,
      which is expected: stores share the same write path back to HBM.

    NOTE the asymmetry against MTE2 (0.0159 cyc/byte at B=56 vs 0.0396 here at
    B=56): writing back is ~2.5x dearer per byte than reading in, at full load.
    """
    # Per-CORE per-byte cost measured at K=4 (launch total divided by cores):
    #   B=14: 0.0809   B=28: 0.0912   B=56: 0.2565  cyc/byte
    # i.e. flat until ~28 cores then ~3.2x by 56. Fitted below.
    # CORRECTED once already: an earlier version divided the K=4 launch total by
    # only one copy (giving 0.2565 cyc/byte at B=56), a 4x double count that made
    # pipelines over-predict by 3.75x. Values below are per SINGLE store per core,
    # obtained from the steady-state K-increment (K=8 minus K=4, /4).
    #
    # Per-core per-byte cost of one 100KB store:
    #   B=14: 0.0202   B=28: 0.0228   B=56: 0.0641   (steady state 0.0707)
    # Three measured core counts, so interpolation rather than a fitted law.
    #
    # CAVEAT kept from the original analysis: these come from back-to-back stores
    # with no compute gap (zone-1 saturated write). An interleaved store in a real
    # pipeline is cheaper (3003 vs 7235 cyc/store/core measured), so this remains
    # an upper bound. The gap is documented in README M1, not hidden.
    t_setup: float = 1788.0
    _pts: tuple = ((14.0, 0.0202), (28.0, 0.0228), (56.0, 0.070651))
    streaming_b56: float = 0.070651   # per-core per-byte in a full pipeline
    calibrated: bool = True
    source: str = "hw-counters M1, JOINT fit across 3 modes at B=56"

    def per_byte(self, cores: int) -> float:
        B = float(max(1, cores))
        pts = self._pts
        if B <= pts[0][0]:
            return pts[0][1]
        if B >= pts[-1][0]:
            return pts[-1][1]
        for (b0, v0), (b1, v1) in zip(pts, pts[1:]):
            if b0 <= B <= b1:
                t = (B - b0) / (b1 - b0)
                return v0 + t * (v1 - v0)
        return pts[-1][1]

    def per_byte_b56(self) -> float:
        return self.per_byte(56)

    # streaming (pipeline full) coefficient, B=56, per-core per-byte
    streaming_b56: float = 0.070651

    def cycles_streaming(self, nbytes: int, cores: int) -> float:
        B = max(1, cores)
        return B * nbytes * self.streaming_b56

    def cycles(self, nbytes: int, cores: int, copies: int = 1) -> float:
        B = max(1, cores)
        return B * (self.t_setup + nbytes * copies * self.per_byte(B))


@dataclass
class AICCost:
    """Cube engine: MTE1 (L1->L0) + M (MAC) + Fixpipe (L0C->GM).

    Calibrated in M2 from a M=128,K=256,N=128 matmul per Cube core with a
    repetition sweep (reps = 1..16), measuring aic_total_cycles and the
    per-pipe times from msprof PipeUtilization.

    Marginal cost per Mmad (128x256x128 = 8.39 MFLOP): 46 994 aic cycles
      => 0.0056 aic-cyc/FLOP
    Fixpipe is essentially FREE per repetition (0.48-0.54 us regardless of reps);
    it is paid once per launch: ~0.50 us. MTE1 costs 544 cyc/rep, MTE2 ~0.

    *** CRITICAL: the AIC counter is NOT the same timebase as the AIV one. ***
    Measured aic_cycles / duration = 45 900 +/- 1 200 cycles/us, consistently
    across the reps sweep, versus 1 650 cycles/us for aiv_total_cycles
    (independently verified in M0/M1). So AIC costs must not be converted with
    the AIV clock -- doing so is wrong by ~28x. See aic_cycles_per_us.
    """
    t_setup: float = 98_730.0          # aic cycles: load A/B + one fixpipe
    mac_cyc_per_mmad_128x256x128: float = 46_994.0
    # Fixpipe scales with OUTPUT ELEMENTS, not constant. M2 measured 0.50us for a
    # 128x128 tile but that experiment varied the Mmad count with the output tile
    # fixed, so it could not see the dependence. M4 then measured 2.14us for a
    # 256x128 tile:
    #   16 384 elems/core -> 0.50 us   (30.5 ns/elem)
    #   32 768 elems/core -> 2.14 us   (65.3 ns/elem)
    # A third tile size would separate intercept from rate, but larger tiles
    # exceed L1/L0 here (ACL 507015), so only two points exist. Linear through
    # them is used, and it over-predicts small tiles:
    #   fixpipe_us = -1.14 us + 100.1 ns/elem * elems   (clamped at >= 0)
    fixpipe_ns_per_elem: float = 100.10
    fixpipe_us_intercept: float = -1.14
    fixpipe_us_per_launch: float = 0.50   # kept for the small-tile case
    aic_cycles_per_us: float = 45_900.0   # measured, NOT 1650
    calibrated: bool = True
    source: str = "hw-counters M2, Mmad reps sweep at B=28"

    def fixpipe_us(self, out_elems_per_core: int) -> float:
        """Fixpipe (L0C->GM) time for one core's output tile."""
        v = self.fixpipe_us_intercept + self.fixpipe_ns_per_elem * out_elems_per_core / 1e6
        return max(0.0, v)

    def cycles_for_mm(self, m: int, k: int, n: int, cores: int, mmads: int = 1) -> float:
        """aic cycles for `mmads` matmuls of per-core m x k x n.

        t_setup is paid ONCE per launch (load A/B, one fixpipe); only the MAC
        term scales with the number of Mmads. The earlier version charged setup
        per Mmad, which over-predicted a 16-rep loop by 46x.
        """
        flops_per_mmad = 2.0 * m * k * n
        ref = 2.0 * 128 * 256 * 128
        per = self.mac_cyc_per_mmad_128x256x128 * (flops_per_mmad / ref)
        return self.t_setup + per * max(1, mmads)

    def cycles(self, elems: int, cores: int) -> float:
        return max(1, cores) * (self.t_setup + elems * 0.0056)

    def load_cycles(self, nbytes: int) -> float:
        """AIC GM->L1 load in AIC counter cycles (uses aic_cycles_per_us)."""
        load = CostModel.__dict__  # not used; kept for API symmetry
        return 0.0


@dataclass
class VECCost:
    """Vector ALU. Calibrated in M1: K vector Add passes over N elements.

    Least-squares fit over K=1,2,4,8 (mode 2), which separates the fixed cost
    (two MTE2 fills) from the marginal K term:
        cycles = 662647 + K * 20665
    Taken from the STEADY-STATE slope (K=8 minus K=4, divided by 4) rather than
    from a single point: 47310 cyc/pass for a 56-core launch = 845 cyc/core/pass
    = 0.032996 cyc/elem for fp32 Add over 25600 elements.

    Why slope and not intercept-fit: K=1 is start-up dominated (cold caches,
    pipeline fill), so including it makes the fitted intercept strongly negative
    and corrupts the per-copy costs. Four earlier attempts at this coefficient
    (0.293, 0.00523, 0.014415, 0.028887) each failed for a different reason:
    56x unit error, an assumption about the K-increment, and two single-mode or
    mixed-N fits. The steady-state slope is the only estimate that both composes
    with MTE2/MTE3 and agrees with the pipeline's own marginal cost.
    """
    # CoreNum dependence discovered in M4: calibrated at B=56 (saturated) this
    # coefficient is 0.033 cyc/elem/core, but a 4-core launch of LeakyRelu over
    # 32768 elems measures 0.00524 cyc/elem/core -- 6.3x lower. Same pattern M1
    # found for MTE2: a coefficient measured on a saturated machine does not
    # transfer to a small launch. VEC needs its own CoreNum law; until then this
    # constant is the saturated-regime value and over-predicts small launches.
    # M6 result: per-CORE cost is CONSTANT at 0.0333 cyc/elem across B=4..56
    # (measured 0.0337 / 0.0340 / 0.0331 / 0.0331 / 0.0334). VEC has no
    # contention term -- each core drives its own vector unit -- so the launch
    # total is simply cores x per-core cost. The 6.3x "CoreNum effect" guessed
    # at in M4 does not exist; that gap was a unit mismatch (see below).
    t_setup: float = 100.0
    cyc_per_elem: float = 0.0333
    cyc_per_elem_unsaturated: float = 0.0333
    calibrated: bool = True
    source: str = "hw-counters M1, VEC K-sweep at fp32"

    def cycles(self, elem: int, dtype_bytes: int, cores: int) -> float:
        """LAUNCH total, in AIV counter cycles (cores x per-core).

        Careful: msprof's aiv_vec_time(us) is per-core BUSY time, not a launch
        total. Comparing a launch-total cycle figure against a busy-time column
        is what made M4 look 6.3x off.
        """
        return max(1, cores) * (self.t_setup + elem * self.cyc_per_elem)

    def per_core_cycles(self, elem: int) -> float:
        return self.t_setup + elem * self.cyc_per_elem


@dataclass
class AICLoadCost:
    """AIC-side GM->L1 load (the `aic_mte2` pipe on the Cube).

    M4 showed this is 33% of a real fused operator's time and that the model had
    been folding it into a constant t_setup. Measured directly with a load-only
    Cube kernel (no Mmad), sweeping 32KB chunks:

      bytes      measured   model
      32 768     1.31 us    1.40  (+6.5%)
      65 536     2.07       2.02  (-2.4%)
     131 072     3.30       3.25  (-1.4%)
     262 144     5.73       5.73  (-0.0%)
     524 288    10.67      10.69  (+0.1%)

    So: t = 0.778 us + bytes / 52.9 GB/s, i.e. a fixed latency plus a rate.
    Note 52.9 GB/s is per-Core-launch, much lower than the 1.6 TB/s an AIV MTE2
    sees across 56 cores -- Cube-side loads are far more latency-sensitive.
    """
    t_setup_us: float = 0.778
    rate_gb_s: float = 52.9
    calibrated: bool = True
    source: str = "hw-counters M5, load-only Cube kernel, 5 points"

    def us(self, nbytes: int) -> float:
        return self.t_setup_us + nbytes / (self.rate_gb_s * 1000.0)


@dataclass
class MultiCoreCost:
    """Multi-core launch skew and straggler tail.

    A kernel does not start on all cores at once, and it ends when the SLOWEST
    core finishes -- neither effect is a property of a single core's work, so a
    per-core cost model cannot express them.

    Measured over 1320 launches of the MTE2 sweep (per-core SYS_CNT stamps):

      B   start skew (median)   slowest core / median core
      7        0.233 us                 1.03x
     14        0.283 us                 1.06x
     21        0.319 us                 1.08x
     28        0.438 us                 1.12x
     56        0.903 us                 1.15x

    Both grow with CoreNum: more cores means a longer launch ramp and a wider
    statistical tail. The kernel time is therefore
        elapsed ~= per-core work x straggler(B) + start_skew(B)
    rather than the median core's time.
    """
    # Linear fits through the five measured core counts. A sqrt/log form was
    # tried first and fitted worse (MAPE 20%/30% vs 11% for the ramp, and the
    # straggler ratio fits linearly to 1.0%).
    skew_us_at_0: float = 0.0779
    skew_us_per_core: float = 0.01418
    straggler_at_0: float = 1.0281
    straggler_per_core: float = 0.00238
    calibrated: bool = True
    source: str = "hw-counters, 1320 launches, per-core SYS_CNT stamps"

    def start_skew_us(self, cores: int) -> float:
        """Spread of core start times; grows ~linearly with CoreNum."""
        return self.skew_us_at_0 + self.skew_us_per_core * max(1, cores)

    def straggler_ratio(self, cores: int) -> float:
        """slowest core / median core. Fitted to 1.0% over B=7..56."""
        return self.straggler_at_0 + self.straggler_per_core * max(1, cores)


@dataclass
class ScalarCost:
    """Per-pipe SCALAR cost (address math, loop control, descriptor setup).

    M4 showed the two scalar pipes were 2.19us of an 11.98us operator (18%),
    entirely unmodelled. Measured behaviour:

      AIV scalar vs loop count (m6_veclaw, 4 cores):
        passes=1  -> 0.41 us
        passes=4  -> 0.41 us
        passes=16 -> 0.46 us
      AIC scalar vs mmad reps (m2_model, 28 cores):
        reps=1 -> 1.23 us   reps=2 -> 1.38 us   reps=4 -> 0.94 us

    So scalar time is a FIXED per-launch cost, not per-iteration: it does not
    grow with the loop count. (The 1.23/0.94 spread is run-to-run noise, not a
    trend -- reps grows 4x while scalar does not.)

    Modelled as a constant per pipe. This will under-predict kernels that are
    genuinely scalar-bound (heavy indexing, per-element scalar work); that is a
    known limitation, recorded rather than hidden.
    """
    aiv_us_per_launch: float = 0.43
    aic_us_per_launch: float = 1.05
    calibrated: bool = True
    source: str = "hw-counters M8, scalar vs loop count at B=4/28"


@dataclass
class MixCost:
    """AIC<->AIV cooperation overhead in a MIX kernel (M3).

    A MIX launch runs Cube and Vector code from one kernel; the two halves
    rendezvous via CrossCoreSetFlag / CrossCoreWaitFlag. That handshake is pure
    overhead no single-engine model can predict, so it was measured directly:

      AIV wall time with and without the handshake, AIC given real work:
        iters=1: 1875 vs 1634  (+241)
        iters=4: 4462 vs 2164  (+2298)
      => per handshake = (2298-241)/3 = 686 AIV cycles

    Applied once per (AIC node, AIV node) dependency pair in a MIX flow.
    """
    handshake_cycles: float = 686.0
    calibrated: bool = True
    source: str = "hw-counters M3, sync on/off differencing at B=56"

    def cycles(self, pairs: int = 1) -> float:
        return self.handshake_cycles * max(0, pairs)


@dataclass
class Clock:
    ghz: float = 1.65

    def to_us(self, cycles: float) -> float:
        return cycles / (self.ghz * 1000.0)


@dataclass
class CostModel:
    mte2: MTE2Cost = None
    mte3: MTE3Cost = None
    vec: VECCost = None
    aic: AICCost = None
    aic_load: AICLoadCost = None
    scalar: ScalarCost = None
    multicore: MultiCoreCost = None
    mix: MixCost = None
    clock: Clock = None

    def __post_init__(self):
        self.mte2 = self.mte2 or MTE2Cost()
        self.mte3 = self.mte3 or MTE3Cost()
        self.vec = self.vec or VECCost()
        self.aic = self.aic or AICCost()
        self.aic_load = self.aic_load or AICLoadCost()
        self.scalar = self.scalar or ScalarCost()
        self.multicore = self.multicore or MultiCoreCost()
        self.mix = self.mix or MixCost()
        self.clock = self.clock or Clock()

    def node_cycles(self, node, cores: int, shared_bus: bool = False) -> float:
        if node.kind == "barrier":
            return 0.0
        if node.kind == "copy":
            if node.pipe == "MTE2":
                return self.mte2.cycles(node.bytes, cores, copies=node.copies,
                                        shared_bus=shared_bus)
            if node.pipe == "MTE3":
                return self.mte3.cycles(node.bytes, cores, copies=node.copies)
            raise ValueError(f"copy node {node.id} on unsupported pipe {node.pipe}")
        if node.kind == "compute":
            if node.pipe == "VEC":
                return self.vec.cycles(node.elem, node.dtype_bytes, cores)
            if node.pipe in ("MAC", "CUBE"):
                # `elem` carries the FLOP count for a Cube node; a matmul node can
                # instead set mmads/mk/n to be explicit.
                return self.aic.cycles_for_mm(128, 256, 128, cores,
                                              mmads=max(1, node.copies))
            raise ValueError(f"compute node {node.id} on unsupported pipe {node.pipe}")
        raise ValueError(f"bad kind {node.kind}")

    def node_cycles_streaming(self, node, cores: int) -> float:
        """Cost of one transfer inside a saturated pipeline."""
        if node.kind != "copy":
            return self.node_cycles(node, cores)
        if node.pipe == "MTE2":
            return self.mte2.cycles_streaming(node.bytes, cores) * node.copies
        if node.pipe == "MTE3":
            return self.mte3.cycles_streaming(node.bytes, cores) * node.copies
        return self.node_cycles(node, cores)

    def calibration_report(self) -> dict:
        return {
            "MTE2": dict(calibrated=self.mte2.calibrated, source=self.mte2.source),
            "MTE3": dict(calibrated=self.mte3.calibrated, source=self.mte3.source),
            "VEC": dict(calibrated=self.vec.calibrated, source=self.vec.source),
            "AIC": dict(calibrated=self.aic.calibrated, source=self.aic.source),
            "AIC_LOAD": dict(calibrated=self.aic_load.calibrated,
                             source=self.aic_load.source),
            "SCALAR": dict(calibrated=self.scalar.calibrated,
                           source=self.scalar.source),
            "MULTICORE": dict(calibrated=self.multicore.calibrated,
                              source=self.multicore.source),
            "MIX": dict(calibrated=self.mix.calibrated, source=self.mix.source),
        }
