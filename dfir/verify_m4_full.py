#!/usr/bin/env python3
"""M4 full-operator check: build the IR flow and let the estimator (not a
hand-written sum) produce the number.

Every earlier version of verify_m4 computed the prediction inline with local
arithmetic. That is not testing the framework, it is testing a spreadsheet. This
script authors the operator as an IR flow and asks estimate() for the answer,
so the same path a user would take is exercised.
"""
import sys
sys.path.insert(0, ".")
from ir import parse
from estimate import estimate, MODE_SERIAL
from costs import CostModel

M, K, N = 512, 128, 128
singleCoreM = 256
cores_aic = M // singleCoreM
cores_aiv = cores_aic * 2
MEASURED_US = 11.977

# The operator as an IR flow: one Cube matmul per Cube core (with its own
# GM->L1 load and Fixpipe), then a LeakyRelu per Vector core.
flow = parse({
    "name": "matmul_leakyrelu", "cores": cores_aiv,
    "nodes": [
        {"id": "load", "kind": "copy", "pipe": "MTE1",
         "bytes": (singleCoreM * K + K * N) * 2},
        {"id": "mm", "kind": "compute", "pipe": "CUBE", "copies": 1,
         "deps": ["load"]},
        {"id": "relu", "kind": "compute", "pipe": "VEC",
         "elem": singleCoreM * N, "deps": ["mm"]},
    ]})

m = CostModel()
for mode in (MODE_SERIAL,):
    r = estimate(flow, mode=mode)
    us = r.cycles / m.clock.ghz / 1000.0
    print(f"IR flow through estimate({mode}):")
    print(f"  {us:6.2f} us   measured {MEASURED_US:6.2f} us   "
          f"error {100*(us-MEASURED_US)/MEASURED_US:+6.1f}%")
    print(f"  bottleneck = {r.bottleneck}")
    print(f"  per-pipe us = { {k: round(v,2) for k,v in r.to_dict()['per_pipe_us'].items()} }")
    print(f"  warnings = {r.warnings or 'none'}")
