"""Pipeline check THROUGH the estimator (streaming context active)."""
import csv, statistics as st, sys
sys.path.insert(0, "dfir")
from ir import parse
from estimate import estimate

by = {}
for r in csv.DictReader(open("m1b.csv")):
    if r["launch"] == "1": continue
    k = (int(r["mode"]), int(r["iters"]), int(r["elems"]), int(r["blocks"]))
    by.setdefault(k, []).append(float(r["cyc"]))
def med(k):
    v = by.get(k); return st.median(v) if v else None

def flow_for(K, B):
    nodes, prev = [], None
    for i in range(K):
        nodes.append({"id": f"in{i}", "kind": "copy", "pipe": "MTE2",
                      "bytes": 102400, "deps": ([prev] if prev else [])})
        nodes.append({"id": f"v{i}", "kind": "compute", "pipe": "VEC",
                      "elem": 25600, "deps": [f"in{i}"]})
        nodes.append({"id": f"o{i}", "kind": "copy", "pipe": "MTE3",
                      "bytes": 102400, "deps": [f"v{i}"]})
        prev = f"o{i}"
    return parse({"name": "pipe", "cores": B, "nodes": nodes})

print(f"{'K':>2} {'meas':>10} {'IR':>10} {'err%':>7}")
errs = []
for K in (1, 4, 8):
    m = med((3, K, 25600, 56))
    if not m: continue
    r = estimate(flow_for(K, 56))
    e = 100 * (r.cycles - m) / m
    errs.append(abs(e))
    print(f"{K:>2} {m:>10.0f} {r.cycles:>10.0f} {e:>+6.1f}%")
print(f"\npipeline MAPE={sum(errs)/len(errs):.1f}%")
