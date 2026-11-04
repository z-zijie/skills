#!/usr/bin/env python3
"""Joint fit of the AIV pipe costs across all three measurement modes.

Fitting each mode separately gives inconsistent, sometimes unphysical numbers
(mode1's intercept came out negative). The three modes observe the SAME four
physical quantities in different combinations, so fit them jointly:

    t_setup + MTE2 + K*MTE3      = mode1(K)   one load,  K stores
    t_setup + 2*MTE2 + K*VEC     = mode2(K)   two loads, K vector passes
    t_setup + K*(MTE2+MTE3+VEC)  = mode3(K)   K x (load, vec, store)

Solving all of them together is 4 unknowns against 10 measurements, and it is
the only way to keep the pipeline prediction consistent with the single-pipe
ones -- which is exactly what failed before.

Usage: python3 joint_fit.py m1b.csv [--emit]
"""
import csv, statistics as st, sys
import numpy as np

MODES = {
    1: lambda K: (0, 1, K, 0),   # (const, MTE2, MTE3, VEC) coefficients
    2: lambda K: (0, 2, 0, K),
    3: lambda K: (0, K, K, K),
}


def load(path):
    by = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["launch"] == "1":
                continue
            k = (int(r["mode"]), int(r["iters"]), int(r["elems"]), int(r["blocks"]))
            by.setdefault(k, []).append(float(r["cyc"]))
    return {k: st.median(v) for k, v in by.items() if len(v) >= 3}


def fit(pts):
    A, y = [], []
    for (mode, K, _N, _B), c in sorted(pts.items()):
        c2, m2, m3, v = MODES[mode](K)
        A.append([1.0, m2, m3, v])
        y.append(c)
    A = np.asarray(A); y = np.asarray(y)
    th, *_ = np.linalg.lstsq(A, y, rcond=None)
    P = A @ th
    rel = np.abs(P - y) / y
    loo = []
    for i in range(len(y)):
        m = np.ones(len(y), bool); m[i] = False
        t2, *_ = np.linalg.lstsq(A[m], y[m], rcond=None)
        loo.append(abs(A[i] @ t2 - y[i]) / y[i])
    return th, rel, np.array(loo), A, y, P


def main():
    pts = load(sys.argv[1])
    want_b = int(sys.argv[sys.argv.index("--blocks") + 1]) if "--blocks" in sys.argv else None
    if want_b:
        pts = {k: v for k, v in pts.items() if k[3] == want_b}
    th, rel, loo, A, y, P = fit(pts)
    t, m2, m3, v = th
    print(f"joint fit over {len(y)} measurements (B=56, K=1/2/4/8, 3 modes)")
    print(f"  t_setup = {t:>9.0f} cyc")
    print(f"  MTE2    = {m2:>9.0f} cyc per 100KB load")
    print(f"  MTE3    = {m3:>9.0f} cyc per 100KB store")
    print(f"  VEC     = {v:>9.0f} cyc per 25600-elem pass")
    print(f"\n  in-sample MAPE={100*rel.mean():.2f}%  median={100*np.median(rel):.2f}%  max={100*rel.max():.1f}%")
    print(f"  LOO       MAPE={100*loo.mean():.2f}%  median={100*np.median(loo):.2f}%  max={100*loo.max():.1f}%")
    print(f"\n{'mode':>5} {'K':>2} {'meas':>10} {'pred':>10} {'err%':>7}")
    keys = sorted(pts)
    for (mode, K, _N, _B), p, yy in zip(keys, P, y):
        print(f"{mode:>5} {K:>2} {yy:>10.0f} {p:>10.0f} {100*(p-yy)/yy:>+6.1f}%")
    if "--emit" in sys.argv:
        print(f"\n  MTE3 per-core per-byte @B=56: {m3/56/102400:.6f}")
        print(f"  VEC  per-core per-elem @B=56: {v/56/25600:.6f}")


if __name__ == "__main__":
    main()
