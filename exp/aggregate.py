#!/usr/bin/env python3
"""Aggregate the noop_mte2 sweep CSV into per-point statistics.

CSV columns:
  launch,idx,point,mode,nops,copybytes,tiles,core,t_begin,t_issue1,t_end1,t_issue2,t_end2

Derived, in SYS_CNT ticks (see CALIBRATION in the report for ticks->ns):
  T1   = t_end1 - t_begin        copy1 issue -> copy1 complete (data arrived in UB)
  W2   = t_issue2 - t_issue1     the delay the nop ladder actually produced
  SPAN = t_end2 - t_begin        whole per-core sequence, ~ per-core kernel wall time

Statistics: the 56 cores of one launch share one bus, so they are not independent.
For each point we take the mean across the 56 cores *per launch*, then the median
and stdev across the R launches. p10/p90 are pooled over all core samples and are
reported only to show the per-core spread.

Usage: aggregate.py <csv> [--json out.json]
"""
import json
import statistics as st
import sys
from collections import defaultdict

MODE_NAMES = {
    0: "one100k",
    1: "two100k",
    2: "ablate",
    3: "pipeline8",
    4: "one200k",
    5: "three72k",
    6: "clock",
}


def load(path):
    rows = []
    meta = {}
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                parts = line.split()
                meta = {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}
                continue
            if line.startswith("launch"):
                continue
            p = line.strip().split(",")
            if len(p) < 14:
                continue
            rows.append(
                dict(
                    launch=int(p[0]),
                    idx=int(p[1]),
                    point=int(p[2]),
                    mode=int(p[3]),
                    nops=int(p[4]),
                    copybytes=int(p[5]),
                    tiles=int(p[6]),
                    blocks=int(p[7]),
                    core=int(p[8]),
                    t_begin=int(p[9]),
                    t_issue1=int(p[10]),
                    t_end1=int(p[11]),
                    t_issue2=int(p[12]),
                    t_end2=int(p[13]),
                )
            )
    return rows, meta


def pct(vals, q):
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def summarize(rows):
    by_point = defaultdict(list)
    for r in rows:
        by_point[r["point"]].append(r)

    out = []
    for point in sorted(by_point):
        rs = by_point[point]
        launches = defaultdict(list)
        for r in rs:
            launches[(r["idx"], r["launch"])].append(r)

        t1_l, span_l, w2_l, tail_l = [], [], [], []
        for _, lrs in launches.items():
            t1_l.append(st.mean(x["t_end1"] - x["t_begin"] for x in lrs))
            span_l.append(st.mean(x["t_end2"] - x["t_begin"] for x in lrs))
            w2_l.append(st.mean(x["t_issue2"] - x["t_issue1"] for x in lrs))
            tail_l.append(st.mean(x["t_end2"] - x["t_end1"] for x in lrs))

        t1_all = [r["t_end1"] - r["t_begin"] for r in rs]
        span_all = [r["t_end2"] - r["t_begin"] for r in rs]

        out.append(
            dict(
                point=point,
                mode=rs[0]["mode"],
                mode_name=MODE_NAMES.get(rs[0]["mode"], str(rs[0]["mode"])),
                nops=rs[0]["nops"],
                copybytes=rs[0]["copybytes"],
                tiles=rs[0]["tiles"],
                blocks=rs[0]["blocks"],
                launches=len(launches),
                samples=len(rs),
                T1_med=st.median(t1_l),
                T1_mean=st.mean(t1_l),
                T1_std=st.pstdev(t1_l) if len(t1_l) > 1 else 0.0,
                T1_p10=pct(t1_all, 0.10),
                T1_p90=pct(t1_all, 0.90),
                W2_med=st.median(w2_l),
                SPAN_med=st.median(span_l),
                SPAN_mean=st.mean(span_l),
                SPAN_std=st.pstdev(span_l) if len(span_l) > 1 else 0.0,
                SPAN_all_med=st.median(span_all),
                TAIL_med=st.median(tail_l),
            )
        )
    return out


def main():
    path = sys.argv[1]
    rows, meta = load(path)
    summ = summarize(rows)
    print(f"# file={path} rows={len(rows)} meta={meta}")
    print(f"{'mode':>10} {'nops':>5} {'c2B':>7} {'K':>2} {'blk':>4} {'lan':>4} | {'T1med':>8} {'T1std':>6} "
          f"{'p10':>6} {'p90':>6} | {'W2med':>7} | {'SPANmed':>8} {'SPANstd':>7} {'TAILmed':>7}")
    for s in summ:
        print(f"{s['mode_name']:>10} {s['nops']:>5} {s['copybytes']:>7} {s['tiles']:>2} {s.get('blocks',0):>4} {s['launches']:>4} | "
              f"{s['T1_med']:>8.0f} {s['T1_std']:>6.0f} {s['T1_p10']:>6.0f} {s['T1_p90']:>6.0f} | "
              f"{s['W2_med']:>7.0f} | {s['SPAN_med']:>8.0f} {s['SPAN_std']:>7.0f} {s['TAIL_med']:>7.0f}")
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        with open(out, "w") as f:
            json.dump({"meta": meta, "points": summ}, f, indent=1)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
