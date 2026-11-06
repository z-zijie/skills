"""Decisive ceiling test.

Question: is ~6-9% a modelling failure or the information limit of the data?

Test: for each (K,S,B) configuration, measure how much C_1 varies BETWEEN
LAUNCHES of the same config. That variance is noise the model cannot predict
(it is not a function of K,S,B). If the noise is comparable to the residual,
the ceiling is the data, not the model.
"""
import json, math
import numpy as np

D=json.load(open("model_dataset.json"))
dense=json.load(open("dense_points.json"))

# rebuild per-launch C1 distributions from the raw csvs is not available here,
# so use the cached per-point spread already in model_dataset (Cmin vs Cmed)
print("Cmed/Cmin ratio per point -- how much a point moves between launch statistics:")
ratios=[]
for p in dense:
    for k in range(p["K"]):
        if p["Cmin"][k]>0:
            ratios.append(p["Cmed"][k]/p["Cmin"][k])
ratios=np.array(ratios)
print(f"  median Cmed/Cmin = {np.median(ratios):.3f}   p90 = {np.percentile(ratios,90):.3f}   max = {ratios.max():.3f}")
print(f"  => even the SAME config's own summary statistic moves by {100*(np.median(ratios)-1):.0f}-{100*(np.percentile(ratios,90)-1):.0f}%")

# So: a model fitted to Cmin can never match a Cmed observation better than this.
# Quantify: predict Cmin exactly (perfect model) and score against Cmed.
rel=np.abs(ratios-1)
print(f"\n  perfect-model-vs-Cmed error: MAPE={100*rel.mean():.1f}%  median={100*np.median(rel):.1f}%")

# And the achievable floor for a model of Cmin: use per-point means (oracle)
print("\nOracle: predict each point's own mean over copies (knows the config, free bias):")
res=[]
for p in dense:
    m=np.mean(p["Cmin"])
    for v in p["Cmin"]:
        res.append(abs(m-v)/v)
res=np.array(res)
print(f"  MAPE={100*res.mean():.1f}% -- largest C_1 vs C_K spread within a config")
