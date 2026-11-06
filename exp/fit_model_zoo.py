"""Final answer: combine dense grid, report the best honest model and the
information ceiling, all with LOO-CV so no number is optimistic."""
import json, math
import numpy as np

D=json.load(open("model_dataset.json"))
dense=json.load(open("dense_points.json"))

pts=[]
for p in dense:
    for k,v in enumerate(p["Cmin"]): pts.append((p["K"],p["S"],p["B"],k+1,v))
for p in D["train"]+D["valid"]:
    for k,v in enumerate(p["Cmin"]): pts.append((p["K"],p["S"],p["B"],k+1,v))
seen=set(); uniq=[]
for t in pts:
    key=(t[0],t[1],t[2],t[3])
    if key in seen: continue
    seen.add(key); uniq.append(t)
pts=uniq
print(f"{len(pts)} samples, {len({(a,b,c) for a,b,c,_,_ in pts})} configs")

def design(X, alpha=0.30):
    A=[]
    for (K,S,B,k,v) in X:
        kB=k*B; kBS=kB*S
        A.append([1.0, kB*S, kB/K, (K*B*S)**alpha, k*k, (k/K)**2])
    return np.array(A)

Y=np.array([t[4] for t in pts]); A=design(pts)
th,_,_,_=np.linalg.lstsq(A,Y,rcond=None)
rel=np.abs(A@th-Y)/Y
print(f"in-sample: MAPE={100*rel.mean():.2f}% median={100*np.median(rel):.2f}%")

# LOO-CV (honest)
errs=[]
for i in range(len(Y)):
    m=np.ones(len(Y),bool); m[i]=False
    t2,_,_,_=np.linalg.lstsq(A[m],Y[m],rcond=None)
    errs.append(abs(A[i]@t2-Y[i])/Y[i])
errs=np.array(errs)*100
print(f"LOO-CV  : MAPE={errs.mean():.2f}% median={np.median(errs):.2f}% p90={np.percentile(errs,90):.2f}% within5={(errs<=5).mean()*100:.0f}%")

# information ceiling from the data itself
rat=[]
for p in dense:
    for k in range(p["K"]):
        if p["Cmin"][k]>0: rat.append(p["Cmed"][k]/p["Cmin"][k])
rat=np.array(rat)
print(f"\nINFORMATION CEILING (same config, launch-statistic spread):")
print(f"  a PERFECT model scored the same way still shows MAPE={100*np.abs(rat-1).mean():.1f}%, median={100*np.median(np.abs(rat-1)):.1f}%")

json.dump(dict(theta=th.tolist(), loo_mape=float(errs.mean()), loo_median=float(np.median(errs)),
               ceiling_mape=float(100*np.abs(rat-1).mean()), n=len(Y),
               configs=len({(a,b,c) for a,b,c,_,_ in pts})), open("model_final.json","w"), indent=1)
print("\nwrote model_final.json")
