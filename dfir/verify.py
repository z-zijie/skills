"""End-to-end IR verification, with the topology-breakout region reported
separately instead of being blended into one number."""
import csv, statistics as st, sys
sys.path.insert(0, "dfir")
from ir import parse
from estimate import estimate

BREAKOUT = {(2, 32768, 52), (2, 32768, 56)}   # measured resource threshold

by = {}
for r in csv.DictReader(open("bsweep_hw.csv")):
    if int(r["launch"]) == 1:
        continue
    by.setdefault((int(r["K"]), int(r["S"]), int(r["B"])), []).append(float(r["cycles"]))
meas = {k: st.median(v) for k, v in by.items() if len(v) >= 3}

def run(skip):
    errs = []
    for (K, S, B), c in sorted(meas.items()):
        if skip and (K, S, B) in BREAKOUT:
            continue
        flow = parse({"name": "mte2", "cores": B, "nodes": [
            {"id": "c", "kind": "copy", "pipe": "MTE2", "bytes": S, "copies": K}]})
        r = estimate(flow)
        errs.append(abs(100 * (r.cycles - c) / c))
    errs.sort()
    return errs

e = run(True)
print(f"in-domain  n={len(e)} MAPE={st.mean(e):.2f}% median={st.median(e):.2f}% "
      f"max={max(e):.1f}% within5={100*sum(1 for x in e if x<=5)/len(e):.0f}%")
e2 = run(False)
print(f"incl. 2 breakout pts: n={len(e2)} MAPE={st.mean(e2):.2f}% max={max(e2):.1f}%")
print("\nbreakout region (excluded, flagged as unmodelled resource threshold):")
for k in sorted(BREAKOUT):
    flow = parse({"name":"x","cores":k[2],"nodes":[{"id":"c","kind":"copy","pipe":"MTE2","bytes":k[1],"copies":k[0]}]})
    r = estimate(flow)
    print(f"  K={k[0]} S={k[1]//1024}KB B={k[2]}: meas={meas[k]:.0f} est={r.cycles:.0f} err={100*(r.cycles-meas[k])/meas[k]:+.1f}%")
