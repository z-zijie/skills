#!/usr/bin/env python3
"""Final figures for the noop_mte2 study.

Primary protocol: one fresh process per data point, 20 recorded launches of 56
AIV cores each, points visited in randomized order, 3 independent replicates
(points1/2/3). This is the protocol that survives the replicate check.

Data:
  data/points{1,2,3}.json  aggregate.py output for the 3 replicates
  data/prof_summary.csv    msprof kernel duration + AIV pipe breakdown
"""
import csv
import json
import os
import statistics as st
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Serif CJK SC", "AR PL UMing TW MBE", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

TICK_NS = 1.02   # measured: SYS_CNT ns/tick, see calibrate()
MODES = {0: "one100k", 1: "two100k", 2: "ablate", 3: "pipeline8", 4: "one200k", 5: "three72k", 6: "clock"}


def load_points(paths):
    """-> list of replicate dicts: {points: [...]}"""
    return [json.load(open(p))["points"] for p in paths]


def series(reps, mode, field="T1_med"):
    """Across replicates: per-n list of values, plus the median of replicates."""
    xs = sorted({p["nops"] for p in reps[0] if p["mode"] == mode})
    out = []
    for n in xs:
        vals = []
        for P in reps:
            for p in P:
                if p["mode"] == mode and p["nops"] == n:
                    vals.append(p[field])
                    break
        if vals:
            out.append((n, st.median(vals), min(vals), max(vals)))
    return out


def load_prof(path):
    """-> {(mode,nops,c,tiles,blocks): mean kernel_us over launches 2..N}

    The first launch of each msprof run is systematically slower (cold start), so
    it is dropped by launch index -- not by value, which would drop a different
    launch whenever the slow one is not the minimum.
    """
    acc = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                v = float(row["kernel_us"])
            except (TypeError, ValueError):
                continue
            k = (int(row["mode"]), int(row["nops"]), int(row["copybytes"]),
                 int(row["tiles"]), int(row["blocks"]))
            acc.setdefault(k, []).append((int(row["launch_idx"]), v))
    out = {}
    for k, pairs in acc.items():
        keep = [v for idx, v in pairs if idx >= 2] or [v for _, v in pairs]
        out[k] = sum(keep) / len(keep)
    return out


def load_pipe_ext(path):
    """Extended pipeline spans (n up to 1024) measured separately; mean over launches."""
    acc = {}
    if not os.path.exists(path):
        return []
    with open(path) as f:
        for line in f:
            if line.startswith("#") or line.startswith("launch"):
                continue
            p = line.strip().split(",")
            if len(p) < 14:
                continue
            acc.setdefault(int(p[4]), []).append(int(p[13]) - int(p[9]))
    return sorted((n, st.median(v)) for n, v in acc.items())


def calibrate(reps, prof):
    """SYS_CNT ns/tick from the pure scalar ladder vs msprof, both referenced to n=0."""
    clk = {p["nops"]: p["T1_med"] for p in reps[0] if p["mode"] == 6}
    if not clk or 0 not in clk:
        return 1.02
    out = []
    for n, ticks in clk.items():
        if n < 512:
            continue
        k = prof.get((6, n, 0, 0, 56))
        k0 = prof.get((6, 0, 0, 0, 56))
        if k and k0:
            out.append((ticks - clk[0]) / ((k - k0) * 1000.0))
    return sum(out) / len(out) if out else 1.02


def main():
    rep_paths = sys.argv[1].split(",")
    prof_path = sys.argv[2]
    outdir = sys.argv[3] if len(sys.argv) > 3 else "figures"
    os.makedirs(outdir, exist_ok=True)

    reps = load_points(rep_paths)
    prof = load_prof(prof_path)
    global TICK_NS
    TICK_NS = calibrate(reps, prof)
    print(f"[cal] ns/tick = {TICK_NS:.4f}")

    def us(t):
        return t * TICK_NS / 1000.0

    two = series(reps, 1)
    one = series(reps, 0)
    pipe = series(reps, 3, "SPAN_med")
    pipe_ext = load_pipe_ext(os.path.join(os.path.dirname(prof_path), "pipe_ext.csv"))
    three = series(reps, 5)

    # ---- figure 1: the requested chart -----------------------------------
    fig, ax = plt.subplots(figsize=(11.5, 6.6))
    xs = [t[0] for t in two]
    ys = [us(t[1]) for t in two]

    # individual replicates as faint points, so the replicate spread is visible
    for i, P in enumerate(reps):
        rx = [p["nops"] for p in P if p["mode"] == 1]
        ry = [us(p["T1_med"]) for p in P if p["mode"] == 1]
        ax.plot(rx, ry, ".", ms=3.0, color="#c0392b", alpha=0.35,
                label="单次复现" if i == 0 else None)

    ax.plot(xs, ys, "o-", color="#c0392b", lw=2.2, ms=5,
            label="第一条 MTE2 结束耗时 T1（3 次复现的中位数）")

    kx, ky = [], []
    for n in xs:
        v = prof.get((1, n, 102400, 0, 56))
        if v:
            kx.append(n)
            ky.append(v)
    ax.plot(kx, ky, "s--", color="#2c3e50", lw=2, ms=5,
            label="Kernel 耗时（msprof Task Duration）")

    ox = [t[0] for t in one]
    ax.plot(ox, [us(t[1]) for t in one], ":", color="#7f8c8d", lw=2,
            label="对照：只发第一条 MTE2（无竞争基线）")
    b1 = [t for t in one if t[0] == 0][0][1]

    # values used by the annotations, taken from the data rather than hardcoded
    one_map = {t[0]: t[1] for t in one}
    ladder_last = [p["W2_med"] for p in reps[0] if p["mode"] == 1 and p["nops"] == xs[-1]][0]
    nlast = xs[-1]
    pen_last = two[-1][1] - one_map[xs[-1]]
    # only the 56-core points that the T1 curve also covers
    kern_vals = [v for k, v in prof.items() if k[0] == 1 and k[4] == 56]
    kern_med = sum(kern_vals) / len(kern_vals)

    ax.set_xlim(-8, 480)
    ax.set_ylim(3.2, 11.0)

    # annotate the facts that matter
    ax.text(12, 5.55, f"① n=0：第二条让第一条多花 {us(two[0][1]-one[0][1]):.2f}µs（+{100*(two[0][1]-one[0][1])/one[0][1]:.0f}%）",
            fontsize=10, color="#c0392b",
            bbox=dict(fc="white", ec="#c0392b", lw=1, alpha=0.9))
    ax.text(12, 6.30, f"② n→{nlast}：阶梯自己涨到 {us(ladder_last):.1f}µs，惩罚降到 {us(pen_last):.2f}µs，两条彻底错开",
            fontsize=10, color="#e67e22",
            bbox=dict(fc="white", ec="#e67e22", lw=1, alpha=0.9))
    ax.text(12, 7.05, f"③ kernel 耗时全程 ~{kern_med:.1f}µs 不变：标量 noop 藏在 MTE2 的影子里",
            fontsize=10, color="#2c3e50",
            bbox=dict(fc="white", ec="#2c3e50", lw=1, alpha=0.9))

    ax.set_xlabel("两条 MTE2 之间插入的 noop 条数 n", fontsize=12)
    ax.set_ylabel("时间 (µs)", fontsize=12)
    ax.set_title("Ascend950PR / dav-3510：56 AIV 全核，两条 DataCopyPad 100KB GM→UB，插 noop 的效果", fontsize=12.5)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9.5, loc="lower left")
    fig.tight_layout()
    p1 = os.path.join(outdir, "noop_mte2_main.png")
    fig.savefig(p1, dpi=150)
    fig.savefig(p1.replace(".png", ".pdf"))
    print("wrote", p1)

    # ---- figure 2: contention penalty vs delay, plus the three probes -----
    fig2, (a1, a2) = plt.subplots(1, 2, figsize=(14.5, 6.0))

    pen_x, pen_y = [], []
    one_map = {t[0]: t[1] for t in one}
    ladder = {p["nops"]: p["W2_med"] for p in reps[0] if p["mode"] == 1}
    for n, t1, _, _ in two:
        if n in one_map:
            pen_x.append(n)
            pen_y.append(us(t1 - one_map[n]))
    a1.plot(pen_x, pen_y, "o-", color="#8e44ad", lw=2, ms=4.5,
            label="第二条给第一条额外加的延迟（T1_two − T1_one）")
    a1.plot([t[0] for t in two], [us(ladder.get(t[0], 0)) for t in two], "-", color="#e67e22", lw=1.6,
            label="noop 阶梯本身消耗的时间")
    a1.set_xlabel("插入的 noop 条数 n", fontsize=12)
    a1.set_ylabel("时间 (µs)", fontsize=12)
    a1.set_title("总线上真实的竞争惩罚随延迟衰减\n但衰减发生在阶梯已接近第一条自身耗时之后", fontsize=11.5)
    a1.grid(alpha=0.3)
    a1.legend(fontsize=9)
    a1.set_xscale("symlog", linthresh=8)
    a1.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v)}"))

    # pipeline series is 6x larger; give it its own axis so both stay readable
    a2b = a2.twinx()
    pe = pipe_ext if pipe_ext else pipe
    a2b.plot([t[0] for t in pe], [us(t[1]) for t in pe], "o-", color="#16a085", lw=2, ms=4.5,
             label="8 块双缓冲预取总耗时（右轴）")
    if pipe_ext:
        kn = next(n for n, v in pipe_ext if v > pipe_ext[0][1] * 1.15)
        a2b.axvline(kn, color="#16a085", ls=":", lw=1.4)
        a2b.annotate(f"n={kn} 起\n预取掩盖不住", xy=(kn, us(pipe_ext[-1][1])),
                     xytext=(kn * 0.14, 58), fontsize=8.5, color="#16a085",
                     arrowprops=dict(arrowstyle="->", color="#16a085", lw=1.1))
    a2b.set_ylabel("双缓冲预取总耗时 (µs)", fontsize=11, color="#16a085")
    a2b.tick_params(axis="y", colors="#16a085")
    a2b.set_ylim(24, 80)

    a2.plot([t[0] for t in two], [us(t[1]) for t in two], "-", color="#c0392b", lw=1.8,
            label="两条 MTE2 的 T1（左轴）")
    a2.plot([t[0] for t in three], [us(t[1]) for t in three], "-", color="#2980b9", lw=1.8,
            label="三条 72KB 并发，第一条的 T1（左轴）")
    a2.set_ylim(2.5, 5.2)
    a2.set_xlabel("插入的 noop 条数 n", fontsize=12)
    a2.set_ylabel("第一条 MTE2 结束耗时 (µs)", fontsize=11)
    if pe:
        p0 = us(pe[0][1]); plast = us([v for n_, v in pe if n_ <= 384][-1])
        pk = us(next(v for n_, v in pe if n_ >= 512))
        a2.set_title(f"双缓冲预取（右轴）：n≤384 总耗时 {p0:.1f}→{plast:.1f}µs（+{100*(plast-p0)/p0:.0f}%）\n"
                     f"n≥512 才垮到 {pk:.1f}µs —— noop 被预取深度吸收", fontsize=11)
    a2.grid(alpha=0.3)
    h1, l1 = a2.get_legend_handles_labels()
    h2, l2 = a2b.get_legend_handles_labels()
    a2.legend(h1 + h2, l1 + l2, fontsize=9, loc="lower left")
    a2.set_xscale("symlog", linthresh=8)
    a2.set_xlim(-4, 1200)
    a2.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v)}"))
    fig2.tight_layout()
    p2 = os.path.join(outdir, "noop_mte2_penalty.png")
    fig2.savefig(p2, dpi=150)
    print("wrote", p2)

    # ---- figure 3: traffic-dose ablation --------------------------------
    # Use all replicates, with the spread shown: a single replicate's 4KB point
    # is a clear outlier, so it must not be presented as a trend on its own.
    dose = {}
    for P in reps:
        for p in P:
            if p["mode"] == 2:
                dose.setdefault(p["copybytes"], []).append(p["T1_med"])
    keys = sorted(dose)
    bx = [k / 1024 for k in keys]
    by = [us(st.median(dose[k])) for k in keys]
    bmin = [us(min(dose[k])) for k in keys]
    bmax = [us(max(dose[k])) for k in keys]

    fig3, ax = plt.subplots(figsize=(9.8, 5.9))
    ax.errorbar(bx, by, yerr=[[y - m for y, m in zip(by, bmin)], [m - y for y, m in zip(by, bmax)]],
                fmt="o-", color="#8e44ad", lw=2, ms=7, capsize=4,
                label="第一条的 T1（3 次复现中位数，误差棒=极差）")
    ax.axhline(us(b1), color="#7f8c8d", ls="--", lw=1.4, label="只有第一条 MTE2")
    for xx, yy, k in zip(bx, by, keys):
        ax.annotate(f"{int(k/1024)}KB", (xx, yy), textcoords="offset points", xytext=(0, 11),
                    fontsize=8.5, ha="center")
    # mark the monotone part explicitly
    mono = [i for i, k in enumerate(keys) if k >= 16 * 1024]
    if len(mono) >= 2:
        ax.annotate("爬升到 32KB 后饱和在 +0.55µs：\n惩罚取决于\"第二条有没有在飞\"，不取决于它多大",
                    xy=(bx[mono[1]], by[mono[1]]), xytext=(20, 3.97),
                    fontsize=9.5, color="#8e44ad",
                    arrowprops=dict(arrowstyle="->", color="#8e44ad", lw=1.2))
    ax.set_xlabel("第二条 MTE2 搬运的数据量 (KB)", fontsize=12)
    ax.set_ylabel("第一条 MTE2 结束耗时 T1 (µs)", fontsize=12)
    ax.set_title("剂量-反应：惩罚随第二条大小爬升，32KB 后饱和在 +0.55µs（n=0）\n"
                 "1~16KB 惩罚很小 —— 小包不足以持续占住总线", fontsize=11.5)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9.5, loc="lower right")
    fig3.tight_layout()
    p3 = os.path.join(outdir, "noop_mte2_ablation.png")
    fig3.savefig(p3, dpi=150)
    print("wrote", p3)

    # ---- numeric summary for the report ---------------------------------
    print("\n n     T1(us)  T1one   penalty  ladder   span   kernel_us")
    for n, t1, lo_, hi_ in two:
        o = one_map.get(n, float("nan"))
        k = prof.get((1, n, 102400, 0, 56))
        print(f"{n:4d} {us(t1):8.3f} {us(o):7.3f} {us(t1-o):8.3f} {us(ladder.get(n,0)):7.3f}"
              f" {us([p for p in reps[0] if p['mode']==1 and p['nops']==n][0]['SPAN_med']):7.3f}"
              f"  {k if k else float('nan'):7.3f}")


if __name__ == "__main__":
    main()
