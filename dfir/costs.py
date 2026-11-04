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

    def cycles(self, nbytes: int, cores: int, copies: int = 1) -> float:
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
        return B * (self.t_setup + first + extra)


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
    # CAVEAT: this is calibrated from a kernel that stores back-to-back with no
    # compute between stores, i.e. it measures SATURATED store throughput. In an
    # interleaved pipeline the same store costs ~2.4x less (measured: mode-1 says
    # 7235 cyc/store/core, pipeline mode-3 shows 3003). So this number is an
    # upper bound that over-predicts pipelines; that gap is recorded in the M1
    # status, not hidden.
    #
    # Per-CORE per-byte cost measured at K=4 (launch total / cores):
    #   B=14: 0.0809   B=28: 0.0912   B=56: 0.2565  cyc/byte
    # Piecewise-linear interpolation through those three measured points. With
    # only three core counts a closed form (1+(B/k)^p) is underdetermined -- it
    # fitted at ~98% error -- so interpolation is used instead of pretending a
    # smooth law is known. Extrapolation outside [14,56] is clamped.
    t_setup: float = 1788.0
    _pts: tuple = ((14.0, 0.0809), (28.0, 0.0912), (56.0, 0.2565))
    calibrated: bool = True
    source: str = "hw-counters M1; 3 measured core counts, piecewise-interpolated"

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

    def cycles(self, nbytes: int, cores: int, copies: int = 1) -> float:
        B = max(1, cores)
        return B * (self.t_setup + nbytes * copies * self.per_byte(B))


@dataclass
class AICCost:
    """Cube: MAC + MT1/MTE2/MTE3/Fixpipe. Not calibrated yet (M2)."""
    t_setup: float = 2000.0
    mac_cyc_per_elem: float = 0.5
    fixpipe_cyc_per_elem: float = 0.05
    calibrated: bool = False
    source: str = "PROVISIONAL -- AIC/Fixpipe not measured yet (M2)"

    def cycles(self, elems: int, cores: int) -> float:
        return max(1, cores) * (self.t_setup +
                                elems * (self.mac_cyc_per_elem + self.fixpipe_cyc_per_elem))


@dataclass
class VECCost:
    """Vector ALU. Calibrated in M1: K vector Add passes over N elements.

    measured per-pass increment (K=8 minus K=1, over 7 passes):
      N=25600 -> 7497 cycles/pass in aiv_total_cycles, which is the SUM over all
      56 cores. Per core that is 7497/56 = 134 cycles/pass = 0.00523 cyc/elem
      for fp32 Add. (Using 0.293 here was a 56x unit error: cyc/elem must be
      per-core because the formula multiplies by cores.)
    """
    t_setup: float = 100.0
    cyc_per_elem: float = 0.00523
    calibrated: bool = True
    source: str = "hw-counters M1, VEC K-sweep at fp32"

    def cycles(self, elem: int, dtype_bytes: int, cores: int) -> float:
        # dtype scaling unknown: fp32 measured, others assumed proportional to
        # element count (not widened), flagged in the report rather than guessed.
        return max(1, cores) * (self.t_setup + elem * self.cyc_per_elem)


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
    clock: Clock = None

    def __post_init__(self):
        self.mte2 = self.mte2 or MTE2Cost()
        self.mte3 = self.mte3 or MTE3Cost()
        self.vec = self.vec or VECCost()
        self.aic = self.aic or AICCost()
        self.clock = self.clock or Clock()

    def node_cycles(self, node, cores: int) -> float:
        if node.kind == "barrier":
            return 0.0
        if node.kind == "copy":
            if node.pipe == "MTE2":
                return self.mte2.cycles(node.bytes, cores, copies=node.copies)
            if node.pipe == "MTE3":
                return self.mte3.cycles(node.bytes, cores, copies=node.copies)
            raise ValueError(f"copy node {node.id} on unsupported pipe {node.pipe}")
        if node.kind == "compute":
            if node.pipe == "VEC":
                return self.vec.cycles(node.elem, node.dtype_bytes, cores)
            if node.pipe == "MAC":
                return self.aic.cycles(node.elem, cores)
            raise ValueError(f"compute node {node.id} on unsupported pipe {node.pipe}")
        raise ValueError(f"bad kind {node.kind}")

    def calibration_report(self) -> dict:
        return {
            "MTE2": dict(calibrated=self.mte2.calibrated, source=self.mte2.source),
            "MTE3": dict(calibrated=self.mte3.calibrated, source=self.mte3.source),
            "VEC": dict(calibrated=self.vec.calibrated, source=self.vec.source),
            "AIC": dict(calibrated=self.aic.calibrated, source=self.aic.source),
        }
