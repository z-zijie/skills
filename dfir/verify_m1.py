"""M1 end-to-end: does the IR predict the measured pipeline (mode 3) to <5%?"""
import csv, statistics as st, sys
sys.path.insert(0, "dfir")
from ir import parse
from estimate import estimate

by = {}
for r in csv.DictReader(open("m1b.csv")):
    if r["launch"] == "1":
        continue
    by.setdefault((int(r["mode"]), int(r["iters"]), int(r["elems"]), int(r["blocks"])), []).append(float(r["cyc"]))

def med(k):
    v = by.get(k)
    return st.median(v) if v else None

print("mode-3 pipeline: K x (MTE2 100KB load, VEC add 25600, MTE3 100KB store)")
print(f"{'K':>2} {'B':>3} {'meas':>10} {'IR':>10} {'err%':>7}")
errs = []
for K in (1, 4, 8):
    for B in (56,):
        m = med((3, K, 25600, B))
        if not m:
            continue
        flow = parse({"name": "pipe", "cores": B, "nodes": [
            {"id": f"in{i}", "kind": "copy", "pipe": "MTE2", "bytes": 102400, "deps": [f"o{i-1}"] if i else []}
            for i in range(K)] + [
            {"id": f"v{i}", "kind": "compute", "pipe": "VEC", "elem": 25600, "deps": [f"in{i}"]}
            for i in range(K)] + [
            {"id": f"o{i}", "kind": "copy", "pipe": "MTE3", "bytes": 102400, "deps": [f"v{i}"]}
            for i in range(K)]})
        r = estimate(flow)
        e = 100 * (r.cycles - m) / m
        errs.append(abs(e))
        print(f"{K:>2} {B:>3} {m:>10.0f} {r.cycles:>10.0f} {e:>+6.1f}%")
if errs:
    print(f"\npipeline MAPE={sum(errs)/len(errs):.1f}% n={len(errs)}")
