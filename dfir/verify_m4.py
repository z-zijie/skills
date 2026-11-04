#!/usr/bin/env python3
"""M4: predict a REAL fused operator and compare with measurement.

Operator: samples/.../matmul_leakyrelu_basic_api
  M=512 K=128 N=128 half, split over 2 Cube cores (singleCoreM=256),
  each producing 256x128 then applying LeakyRelu on the AIV half.

Measured (msprof, MIX_AIC, warm launch):
  Task Duration 11.98 us
  aic_mte2 (GM->L1 load) 3.99   33%
  aic_fixpipe            2.24   19%
  aic_scalar             0.97    8%
  aic_mac                0.75    6%
  aic_mte1 (L1->L0)      0.28    2%
  aiv_scalar             0.94    8%
  aiv_mte2               0.48    4%
  aiv_vec                0.10    1%

This script reports the IR prediction against that, and diagnoses the gap.
"""
import sys
sys.path.insert(0, ".")
from ir import parse
from estimate import estimate
from costs import CostModel

M, K, N = 512, 128, 128
singleCoreM = 256
cores_aic = M // singleCoreM          # 2 Cube cores
cores_aiv = cores_aic * 2             # sample maps 2 Vector cores per Cube core

m = CostModel()

# --- what the IR currently predicts ------------------------------------
flow = parse({"name": "matmul_leakyrelu", "cores": cores_aiv, "nodes": [
    {"id": "mm", "kind": "compute", "pipe": "CUBE", "copies": 1},
    {"id": "relu", "kind": "compute", "pipe": "VEC",
     "elem": singleCoreM * N, "deps": ["mm"]}]})
r = estimate(flow)

MEASURED_US = 11.977

# Model v2: add the AIC GM->L1 load term that M4 exposed as missing.
# Per core: A tile 256x128 half = 64KB, B tile 128x128 half = 32KB.
aic_load_us = m.aic_load.us((singleCoreM * K + K * N) * 2)
aic_mac_us = (m.aic.mac_cyc_per_mmad_128x256x128
              * (2.0 * singleCoreM * K * N) / (2.0 * 128 * 256 * 128)
              / m.aic.aic_cycles_per_us)
aic_fix_us = m.aic.fixpipe_us_per_launch
aiv_vec_us = m.vec.cycles(singleCoreM * N, 4, cores_aiv) / (m.clock.ghz * 1000.0)
pred2 = aic_load_us + aic_mac_us + aic_fix_us + aiv_vec_us

pred_us = r.cycles / m.aic.aic_cycles_per_us     # v1: Cube expr in AIC cycles
print("=== M4: real fused operator (matmul + LeakyRelu) ===")
print(f"v1 (before M5 load term) : {pred_us:6.2f} us  "
      f"({100*(pred_us-MEASURED_US)/MEASURED_US:+6.1f}%)")
print(f"v2 (+ AIC GM->L1 load)   : {pred2:6.2f} us  "
      f"({100*(pred2-MEASURED_US)/MEASURED_US:+6.1f}%)")
print(f"Measured                 : {MEASURED_US:6.2f} us")
print(f"\n  aic_load {aic_load_us:.2f} + aic_mac {aic_mac_us:.2f} + "
      f"fixpipe {aic_fix_us:.2f} + aiv_vec {aiv_vec_us:.2f} = {pred2:.2f} us")
print()
print("Diagnosis -- which measured items does the model represent?")
items = [
    ("aic_mac (Cube MAC)",        0.752, "YES: AICCost MAC term"),
    ("aic_fixpipe",               2.236, "YES: AICCost fixpipe term"),
    ("aic_mte2 (GM->L1 load)",    3.990, "NO  <-- not modelled at all"),
    ("aic_mte1 (L1->L0)",         0.280, "NO"),
    ("aic_scalar",                0.973, "NO"),
    ("aiv_mte2",                  0.477, "YES: MTE2Cost"),
    ("aiv_vec (LeakyRelu)",       0.104, "YES: VECCost"),
    ("aiv_scalar",                0.938, "NO"),
]
for name, us, status in items:
    print(f"  {name:<26} {us:5.3f} us  {100*us/MEASURED_US:5.1f}%   {status}")
modelled = sum(us for _, us, s in items if s.startswith("YES"))
print(f"\n  covered by the model : {modelled:.3f} us ({100*modelled/MEASURED_US:.0f}%)")
print(f"  not covered          : {MEASURED_US-modelled:.3f} us "
      f"({100*(1-modelled/MEASURED_US):.0f}%)")
print()
print("Conclusion: the operator is dominated by per-pipe SCALAR work and the")
print("AIC-side GM->L1 load, neither of which the IR models. MAC is only 6%, so")
print("a MAC-centred model cannot predict this operator no matter how well it is")
print("calibrated. Fixing this needs an AIC-MTE2 (load-rate) and a scalar-op")
print("model, i.e. two more calibration campaigns.")
