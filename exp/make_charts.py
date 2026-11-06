#!/usr/bin/env python3
"""SVG charts for the MTE2 report, drawn from the measured data.

Inline SVG so the HTML stays a single self-contained file.
"""
import csv, statistics as st
from collections import defaultdict

def load(path):
    acc = defaultdict(lambda: defaultdict(list))
    with open(path) as f:
        for r in csv.DictReader(f):
            K,S,B = int(r["K"]),int(r["S"]),int(r["B"]); t0=int(r["t_begin"])
            acc[(K,S,B)][r["launch"]].append([(int(r[f"t_done{k}"])-t0)/1000.0 for k in range(K)])
    out={}
    for key,lau in acc.items():
        K=key[0]
        per=[[st.median(row[k] for row in rows) for k in range(K)] for rows in lau.values()]
        out[key]={"min":[min(p[k] for p in per) for k in range(K)],
                  "med":[st.median(p[k] for p in per) for k in range(K)]}
    return out

D=load("data/m2_train.csv"); V=load("data/m2_valid.csv"); BS=load("data/bsweep.csv")

W,H,ML,MR,MT,MB=640,300,58,18,16,44
def sx(v,lo,hi,w=W-ML-MR): return ML+(v-lo)/(hi-lo)*w if hi>lo else ML
def sy(v,lo,hi,h=H-MT-MB): return MT+h-(v-lo)/(hi-lo)*h if hi>lo else MT+h

def axes(xlo,xhi,ylo,yhi,xt,yt,xlab,ylab,logx=False):
    import math
    g=[f'<line x1="{ML}" y1="{MT+H-MT-MB}" x2="{W-MR}" y2="{MT+H-MT-MB}" stroke="#d1d5db"/>',
       f'<line x1="{ML}" y1="{MT}" x2="{ML}" y2="{MT+H-MT-MB}" stroke="#d1d5db"/>']
    for v,lab in xt:
        x=sx(v,xlo,xhi)
        g.append(f'<line x1="{x:.1f}" y1="{MT+H-MT-MB}" x2="{x:.1f}" y2="{MT+H-MT-MB+4}" stroke="#9ca3af"/>')
        g.append(f'<text x="{x:.1f}" y="{MT+H-MT-MB+18}" font-size="10" fill="#6b7280" text-anchor="middle">{lab}</text>')
    for v,lab in yt:
        y=sy(v,ylo,yhi)
        g.append(f'<line x1="{ML-4}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}" stroke="#f3f4f6"/>')
        g.append(f'<text x="{ML-8}" y="{y+3.5:.1f}" font-size="10" fill="#6b7280" text-anchor="end">{lab}</text>')
    g.append(f'<text x="{(ML+W-MR)/2}" y="{H-6}" font-size="10.5" fill="#374151" text-anchor="middle">{xlab}</text>')
    g.append(f'<text x="12" y="{MT+(H-MT-MB)/2}" font-size="10.5" fill="#374151" text-anchor="middle" transform="rotate(-90 12 {MT+(H-MT-MB)/2})">{ylab}</text>')
    return g

def poly(pts,color,dash=""):
    d=" ".join(f"{x:.1f},{y:.1f}" for x,y in pts)
    da=f' stroke-dasharray="{dash}"' if dash else ""
    return f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"{da}/>'

# ---- chart 1: C1 vs K -------------------------------------------------
series=[(16384,56,"S=16KB B=56","#c0392b"),(32768,56,"S=32KB B=56","#2c3e50"),(8192,56,"S=8KB B=56","#16a085")]
ks=sorted({K for (K,s,b) in D})
xlo,xhi=min(ks),max(ks)
ylo,yhi=0,2.2
c=['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" role="img">'%(W,H)]
c+=axes(xlo,xhi,ylo,yhi,[(k,str(k)) for k in ks],
        [(v,f"{v:.1f}") for v in [0,0.5,1.0,1.5,2.0]],"K（同一核内的 copy 条数）","C₁（第一条完成耗时, µs）")
for S,B,lab,col in series:
    pts=[(sx(K,xlo,xhi), sy(D[(K,S,B)]["min"][0],ylo,yhi)) for K in ks if (K,S,B) in D]
    c.append(poly(pts,col))
    for x,y in pts: c.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{col}"/>')
for i,(S,B,lab,col) in enumerate(series):
    c.append(f'<line x1="{W-MR-140}" y1="{MT+12+i*16}" x2="{W-MR-122}" y2="{MT+12+i*16}" stroke="{col}" stroke-width="2"/>')
    c.append(f'<text x="{W-MR-116}" y="{MT+15+i*16}" font-size="10" fill="#374151">{lab}</text>')
c.append("</svg>")
chart_c1="".join(c)

# ---- chart 2: C1 vs cores --------------------------------------------
Bs=sorted({k[2] for k in BS if k[0]==1})
y=BS
xlo,xhi=1,max(Bs); ylo,yhi=0,4.0
c=['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" role="img">'%(W,H)]
c+=axes(xlo,xhi,ylo,yhi,[(b,str(b)) for b in [1,7,14,21,28,36,44,56]],
        [(v,f"{v:.0f}") for v in [0,1,2,3,4]],"B（参与核数）","C₁（单条 100KB, µs）")
c.append(f'<line x1="{sx(28,xlo,xhi):.1f}" y1="{MT}" x2="{sx(28,xlo,xhi):.1f}" y2="{MT+H-MT-MB}" stroke="#e5e7eb" stroke-dasharray="4 3"/>')
c.append(f'<text x="{sx(28,xlo,xhi)+5:.1f}" y="{MT+14}" font-size="9.5" fill="#9ca3af">B≈28 饱和拐点</text>')
pts=[(sx(b,xlo,xhi), sy(BS[(1,102400,b)]["min"][0],ylo,yhi)) for b in Bs]
c.append(poly(pts,"#c0392b"))
for x,y2 in pts: c.append(f'<circle cx="{x:.1f}" cy="{y2:.1f}" r="2.6" fill="#c0392b"/>')
c.append("</svg>")
chart_core="".join(c)

# ---- chart 3: predicted vs measured (held-out) ------------------------
a,b=0.842,0.5305e-6
pts=[]
for (K,S,B),d in V.items():
    for k in range(1,K+1):
        pts.append((a+b*k*B*S, d["min"][k-1]))
lo,hi=0,8
c=['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" role="img">'%(W,H)]
c+=axes(lo,hi,lo,hi,[(v,str(v)) for v in range(0,9,2)],
        [(v,str(v)) for v in range(0,9,2)],"模型预测 (µs)","实测 (µs)")
c.append(f'<line x1="{sx(lo,lo,hi):.1f}" y1="{sy(lo,lo,hi):.1f}" x2="{sx(hi,lo,hi):.1f}" y2="{sy(hi,lo,hi):.1f}" stroke="#d1d5db" stroke-dasharray="4 3"/>')
for pv,mv in pts:
    inside=abs(pv-mv)/mv<=0.05
    c.append(f'<circle cx="{sx(pv,lo,hi):.1f}" cy="{sy(mv,lo,hi):.1f}" r="3.2" fill="{"#15803d" if inside else "#c0392b"}" fill-opacity="0.75"/>')
c.append(f'<circle cx="{W-MR-130}" cy="{MT+13}" r="3.2" fill="#15803d" fill-opacity="0.75"/><text x="{W-MR-120}" y="{MT+16}" font-size="10" fill="#374151">±5% 内</text>')
c.append(f'<circle cx="{W-MR-130}" cy="{MT+29}" r="3.2" fill="#c0392b" fill-opacity="0.75"/><text x="{W-MR-120}" y="{MT+32}" font-size="10" fill="#374151">超出 ±5%</text>')
c.append("</svg>")
chart_fit="".join(c)

open("data/charts.json","w").write(__import__("json").dumps(
    dict(c1=chart_c1, core=chart_core, fit=chart_fit)))
print("wrote data/charts.json", len(chart_c1)+len(chart_core)+len(chart_fit), "bytes")
