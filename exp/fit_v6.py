#!/usr/bin/env python3
"""MTE2 model v6 -- the structure the data actually shows.

Two facts an affine C_k = L + k*g cannot express, both visible in the clean
(min-over-launch) curves:

  F1  C_1 grows with K.  Same S=16KB, B=56:
          K=1 -> 0.67us   K=2 -> 1.05   K=4 -> 1.33   K=8 -> 1.41
      The first copy is slowed by the copies queued behind it. This is the
      out-of-order packet interleaving the study is about: a core that has more
      copies outstanding sees its own first copy's packets interleave with them.

  F2  The marginal cost of a copy shrinks after the queue is full:
          S=16KB B=56 deltas: 0.51 0.49 0.52 0.55 0.56 0.39 0.16
      The bus drains as the last packets complete.

Model: a shared bus that serves the outstanding stream, plus a startup term for
the first copy that depends on how many copies are queued:

    Q        = K * B * S                        bytes queued in this launch
    C_k      = L + S/R + k*Q/(K*R) - D*(k/K)^2
             = L + S/R + k*B*S/R - D*(k/K)^2

and F1 via a term that depends on the queue depth seen by copy 1:

    C_k      = L + beta*(K*B*S)^p + k*B*S/R - D*(k/K)^2

beta*(KBS)^p captures "the first completion waits for the pipe to fill, and a
deeper queue means more interleaving".

Parameters: L, beta, p, R, D.
"""
import csv, json, math, statistics as st
from collections import defaultdict

def load(path, est="min"):
    acc = defaultdict(lambda: defaultdict(list))
    with open(path) as f:
        for r in csv.DictReader(f):
            K,S,B = int(r["K"]), int(r["S"]), int(r["B"]); t0 = int(r["t_begin"])
            acc[(K,S,B)][r["launch"]].append(
                [(int(r[f"t_done{k}"]) - t0)/1000.0 for k in range(K)])
    out = {}
    for key, lau in acc.items():
        K = key[0]
        per = [[st.median(row[k] for row in rows) for k in range(K)] for rows in lau.values()]
        pick = min if est == "min" else st.median
        out[key] = {"C": [pick(p[k] for p in per) for k in range(K)]}
    return out

def predict(t, K, S, B):
    L, logb, pw, logR, D = t
    beta = math.exp(logb); R = math.exp(logR)
    fill = beta * (K*B*S)**pw
    return [max(L + fill + (k*B*S)/R - D*(k/K)**2, 1e-3) for k in range(1, K+1)]

def sse(t, data):
    return sum((predict(t,K,S,B)[k]-d["C"][k])**2
               for (K,S,B),d in data.items() for k in range(K))

def nm(x0, data, iters=30000, step=0.2):
    n=len(x0); simp=[list(x0)]
    for i in range(n):
        q=list(x0); q[i]=q[i]+(abs(q[i])*step if q[i] else step); simp.append(q)
    val=[sse(p,data) for p in simp]
    for _ in range(iters):
        o=sorted(range(n+1),key=lambda i:val[i]); simp=[simp[i] for i in o]; val=[val[i] for i in o]
        if abs(val[-1]-val[0])<=1e-15*(abs(val[0])+1e-15): break
        cen=[sum(p[i] for p in simp[:-1])/n for i in range(n)]
        xr=[cen[i]+(cen[i]-simp[-1][i]) for i in range(n)]; fr=sse(xr,data)
        if fr<val[0]:
            xe=[cen[i]+2*(cen[i]-simp[-1][i]) for i in range(n)]; fe=sse(xe,data)
            simp[-1],val[-1]=(xe,fe) if fe<fr else (xr,fr)
        elif fr<val[-2]: simp[-1],val[-1]=xr,fr
        else:
            xc=[cen[i]+.5*(simp[-1][i]-cen[i]) for i in range(n)]; fc=sse(xc,data)
            if fc<val[-1]: simp[-1],val[-1]=xc,fc
            else:
                for i in range(1,n+1):
                    simp[i]=[simp[0][j]+.5*(simp[i][j]-simp[0][j]) for j in range(n)]
                    val[i]=sse(simp[i],data)
    b=min(range(n+1),key=lambda i:val[i]); return simp[b],val[b]

def fit(data):
    best=None
    for b0 in (1e-4,1e-3,1e-2):
        for pw in (0.3,0.5,0.8):
            for R in (1.0e6,2.0e6):
                for D in (0.0,0.5):
                    t=[0.5,math.log(b0),pw,math.log(R),D]
                    t,s=nm(t,data,iters=6000)
                    if best is None or s<best[1]: best=(t,s)
    t,s=best
    for step in (0.08,0.03,0.01):
        t2,s2=nm(t,data,iters=15000,step=step)
        if s2<s: t,s=t2,s2
    return t,s

def metrics(t,data):
    rel,pts=[],[]
    for (K,S,B),d in sorted(data.items()):
        pr=predict(t,K,S,B)
        e=[abs(pr[k]-d["C"][k])/d["C"][k] for k in range(K)]
        rel+=e; pts.append(st.mean(e))
    rel.sort()
    return dict(n=len(rel),mape=100*st.mean(rel),med=100*st.median(rel),
                p90=100*rel[int(.9*(len(rel)-1))],mx=100*rel[-1],
                point=100*st.mean(pts),point_max=100*max(pts),
                w5=100*sum(1 for r in rel if r<=.05)/len(rel))

def main():
    tr=load("data/m2_train.csv"); va=load("data/m2_valid.csv")
    t,s=fit(tr)
    L,logb,pw,logR,D=t
    print(f"L={L:.4f} beta={math.exp(logb):.3e} p={pw:.3f} R={math.exp(logR)/1000:.0f} GB/s D={D:.3f}")
    for tag,d in (("train",tr),("valid",va)):
        m=metrics(t,d)
        print(f"{tag}: MAPE={m['mape']:5.2f}% med={m['med']:5.2f}% p90={m['p90']:5.2f}% "
              f"max={m['mx']:5.1f}% point-MAPE={m['point']:5.2f}% <=5%={m['w5']:3.0f}%")
    json.dump(dict(params=dict(L=L,beta=math.exp(logb),p=pw,R=math.exp(logR),D=D),
                   train=metrics(t,tr),valid=metrics(t,va)),
              open("data/model_v6.json","w"),indent=1)

if __name__=="__main__": main()
