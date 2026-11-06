#!/usr/bin/env python3
"""Recalibrate MTE2Cost from measured hardware counters, with LOO-CV.

Usage:  python3 calibrate.py <bsweep_hw.csv> [--emit]

Fits   cycles = B*( t_setup + S*r1*(1+(B/k1)^p1) + (K-1)*S*r2*(1+(B/k2)^p2) )
by grid search on the two nonlinear pairs and linear least squares on the three
linear coefficients, then reports LOO-CV so the reported accuracy is honest.

`--emit` prints the dataclass constants to paste into costs.py.
"""
import csv, statistics as st, sys
from typing import List, Tuple

def load(path):
    by = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if int(r["launch"]) == 1:
                continue
            k = (int(r["K"]), int(r["S"]), int(r["B"]))
            by.setdefault(k, []).append(float(r["cycles"]))
    return [(k[0], k[1], k[2], st.median(v)) for k, v in by.items() if len(v) >= 3]


def design(pts, k1, p1, k2, p2):
    A = []
    for (K, S, B, _) in pts:
        r1 = 1.0 + (B / k1) ** p1
        r2 = 1.0 + (B / k2) ** p2
        A.append([B, B * S * r1, B * (K - 1) * S * r2])
    return A


def lstsq3(A, y):
    import numpy as np
    A = np.asarray(A, float); y = np.asarray(y, float)
    th, *_ = np.linalg.lstsq(A, y, rcond=None)
    return th


def fit(pts):
    import numpy as np
    best = None
    for k1 in np.arange(12, 60, 2.0):
        for p1 in np.arange(0.8, 3.5, 0.15):
            for k2 in np.arange(12, 60, 2.0):
                for p2 in np.arange(0.8, 3.5, 0.15):
                    A = design(pts, k1, p1, k2, p2)
                    y = [p[3] for p in pts]
                    th = lstsq3(A, y)
                    e = float(((np.asarray(A) @ th - np.asarray(y)) ** 2).mean())
                    if best is None or e < best[0]:
                        best = (e, k1, p1, k2, p2, th)
    return best


def main():
    path = sys.argv[1]
    pts = load(path)
    e, k1, p1, k2, p2, th = fit(pts)
    import numpy as np
    A = np.asarray(design(pts, k1, p1, k2, p2)); y = np.asarray([p[3] for p in pts])
    P = A @ th
    rel = np.abs(P - y) / y
    loo = []
    for i in range(len(y)):
        m = np.ones(len(y), bool); m[i] = False
        t2 = lst3(A[m], y[m]) if False else lstsq3(A[m].tolist(), y[m].tolist())
        loo.append(abs(A[i] @ t2 - y[i]) / y[i])
    print(f"points: {len(y)}")
    print(f"cycles = B*{th[0]:.1f} + B*S*{th[1]:.6f}*(1+(B/{k1:.0f})^{p1:.2f})"
          f" + B*(K-1)*S*{th[2]:.6f}*(1+(B/{k2:.0f})^{p2:.2f})")
    print(f"in-sample MAPE={100*rel.mean():.2f}% median={100*np.median(rel):.2f}% max={100*rel.max():.1f}%")
    print(f"LOO       MAPE={100*np.mean(loo):.2f}% median={100*np.median(loo):.2f}% max={100*max(loo):.1f}%")
    print(f"within 5%: in-sample {100*(rel<=0.05).mean():.0f}%  LOO {100*(np.array(loo)<=0.05).mean():.0f}%")
    if "--emit" in sys.argv:
        print(f"\n    t_setup: float = {th[0]:.1f}")
        print(f"    r1: float = {th[1]:.6f}")
        print(f"    k1: float = {k1:.1f}")
        print(f"    p1: float = {p1:.2f}")
        print(f"    r2: float = {th[2]:.6f}")
        print(f"    k2: float = {k2:.1f}")
        print(f"    p2: float = {p2:.2f}")


def lst3(A, y):
    return lstsq3(A, y)


if __name__ == "__main__":
    main()
