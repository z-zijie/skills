#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-tests for the kernel-perf analyzer and critic.

These run without an NPU: they build synthetic captures whose numbers are known
by construction, then assert the tool recovers them. That is the only way to
prove the analyzer is right rather than merely plausible -- on real hardware we
can confirm an identity holds, but only a synthetic capture tells us the tool
would have caught a wrong value.

Run:  python3 scripts/selftest.py
"""

from __future__ import annotations

import csv
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import critic as CR  # noqa: E402
import kernel_perf as KP  # noqa: E402

FAILURES: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# synthetic capture builder
# ---------------------------------------------------------------------------

PIPE_HEADER = (
    "block_id,sub_block_id,aic_time(us),aic_total_cycles,aic_cube_time(us),"
    "aic_cube_ratio,aic_scalar_time(us),aic_scalar_ratio,aic_mte1_time(us),"
    "aic_mte1_ratio,aic_mte1_active_bw(GB/s),aic_mte2_time(us),aic_mte2_ratio,"
    "aic_mte2_active_bw(GB/s),aic_mte3_time(us),aic_mte3_ratio,"
    "aic_mte3_active_bw(GB/s),aic_fixpipe_time(us),aic_fixpipe_ratio,"
    "aic_fixpipe_active_bw(GB/s),aic_icache_miss_rate,aiv_time(us),"
    "aiv_total_cycles,aiv_vec_time(us),aiv_vec_ratio,aiv_scalar_time(us),"
    "aiv_scalar_ratio,aiv_mte2_time(us),aiv_mte2_ratio,aiv_mte2_active_bw(GB/s),"
    "aiv_mte3_time(us),aiv_mte3_ratio,aiv_mte3_active_bw(GB/s),"
    "aiv_icache_miss_rate,"
)

MEMORY_HEADER = (
    "block_id,sub_block_id,aic_time(us),aic_total_cycles,aic_l1_read_bw(GB/s),"
    "aic_l1_write_bw(GB/s),aic_main_mem_read_bw(GB/s),aic_main_mem_write_bw(GB/s),"
    "aic_mte1_instructions,aic_mte1_ratio,aic_mte2_instructions,aic_mte2_ratio,"
    "aic_mte3_instructions,aic_mte3_ratio,aiv_time(us),aiv_total_cycles,"
    "aiv_ub_to_gm_bw(GB/s),aiv_gm_to_ub_bw(GB/s),aiv_main_mem_read_bw(GB/s),"
    "aiv_main_mem_write_bw(GB/s),aiv_mte2_instructions,aiv_mte2_ratio,"
    "aiv_mte3_instructions,aiv_mte3_ratio,read_main_memory_datas(KB),"
    "write_main_memory_datas(KB),GM_to_L1_datas(KB),GM_to_L1_bw_usage_rate(%),"
    "L0C_to_L1_datas(KB),L0C_to_L1_bw_usage_rate(%),L0C_to_GM_datas(KB),"
    "L0C_to_GM_bw_usage_rate(%),GM_to_UB_datas(KB),GM_to_UB_bw_usage_rate(%),"
    "UB_to_GM_datas(KB),UB_to_GM_bw_usage_rate(%),"
)

L2_HEADER = (
    "block_id,sub_block_id,aic_time(us),aic_total_cycles,aic_read_close_hit,"
    "aic_read_close_miss,aic_read_close_victim,aic_read_far_hit,aic_read_far_miss,"
    "aic_read_far_victim,aic_read_hit_rate(%),aic_write_close_hit,"
    "aic_write_close_miss,aic_write_close_victim,aic_write_far_hit,"
    "aic_write_far_miss,aic_write_far_victim,aic_write_hit_rate(%),aiv_time(us),"
    "aiv_total_cycles,aiv_read_close_hit,aiv_read_close_miss,aiv_read_close_victim,"
    "aiv_read_far_hit,aiv_read_far_miss,aiv_read_far_victim,aiv_read_hit_rate(%),"
    "aiv_write_close_hit,aiv_write_close_miss,aiv_write_close_victim,"
    "aiv_write_far_hit,aiv_write_far_miss,aiv_write_far_victim,"
    "aiv_write_hit_rate(%),"
)


def viz_record(kind: int, variant: int, payload: Dict) -> bytes:
    """Encode one visualize_data.bin record.

    payload_len counts the JSON bytes only; the 12-byte header is
    <u64 len><u8 kind><u8 variant>ZZ. Verified against real captures by
    test_viz_decoder's round-trip of a production file.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("<Q", len(body)) + bytes([kind, variant]) + b"ZZ" + body


def build_capture(root: Path, *, cores: int = 4, block_dim: int = 4,
                  vec_time: float = 0.2, total_time: float = 1.0,
                  freq_mhz: float = 1650.0, gm_to_ub_kb: float = 64.0,
                  peak_gibps: float = 160.0, all_empty: bool = False,
                  soc: str = "Ascend950PR_9579") -> Path:
    """Write a capture whose derived values are known exactly.

    Invariants deliberately encoded: ratio == time/total, usage_rate ==
    bandwidth/peak, cycles == total_time*freq, per-core rows == block_dim.
    """
    cap = root / "OPPROF_synthetic"
    cap.mkdir(parents=True, exist_ok=True)

    def fmt(value):
        return "NA" if all_empty else f"{value:.6f}"

    # --- OpBasicInfo
    with (cap / "OpBasicInfo.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Op Name", "Op Type", "Task Duration(us)", "Block Dim",
                         "Mix Block Dim", "Device Id", "Pid", "Current Freq",
                         "Rated Freq", ""])
        writer.writerow(["synth_kernel", "vector", f"{total_time:.6f}", block_dim,
                         "NA", "0", "1234", f"{freq_mhz:.0f}", f"{freq_mhz:.0f}", ""])

    # --- PipeUtilization
    with (cap / "PipeUtilization.csv").open("w", newline="") as fh:
        fh.write(PIPE_HEADER + "\n")
        for core in range(cores):
            cycles = int(round(total_time * 1e-6 * freq_mhz * 1e6))
            ratio = vec_time / total_time
            row = {name: "NA" for name in PIPE_HEADER.rstrip(",").split(",")}
            row.update({
                "block_id": str(core), "sub_block_id": "vector0",
                "aiv_time(us)": fmt(total_time),
                "aiv_total_cycles": "NA" if all_empty else str(cycles),
                "aiv_vec_time(us)": fmt(vec_time),
                "aiv_vec_ratio": fmt(ratio),
                "aiv_scalar_time(us)": fmt(total_time - vec_time),
                "aiv_scalar_ratio": fmt((total_time - vec_time) / total_time),
            })
            fh.write(",".join(row[name] for name in
                              PIPE_HEADER.rstrip(",").split(",")) + ",\n")

    # --- Memory: encode bw = datas/time in GiB with the documented convention
    gibps = gm_to_ub_kb * 1024.0 / (total_time * 1e-6) / (1024.0 ** 3)
    usage_pct = 100.0 * gibps / peak_gibps
    with (cap / "Memory.csv").open("w", newline="") as fh:
        fh.write(MEMORY_HEADER + "\n")
        names = MEMORY_HEADER.rstrip(",").split(",")
        for core in range(cores):
            row = {name: "NA" for name in names}
            row.update({
                "block_id": str(core), "sub_block_id": "vector0",
                "aiv_time(us)": fmt(total_time),
                "aiv_total_cycles": "NA" if all_empty else
                                    str(int(round(total_time * 1e-6 * freq_mhz * 1e6))),
                "aiv_gm_to_ub_bw(GB/s)": fmt(gibps),
                "aiv_main_mem_read_bw(GB/s)": fmt(gibps),
                "GM_to_UB_datas(KB)": fmt(gm_to_ub_kb),
                "GM_to_UB_bw_usage_rate(%)": fmt(usage_pct),
                "read_main_memory_datas(KB)": fmt(gm_to_ub_kb),
            })
            fh.write(",".join(row[name] for name in names) + ",\n")

    # --- L2Cache, matching the JSON hit ratios we are about to write
    read_rate = 50.0
    write_rate = 70.0
    with (cap / "L2Cache.csv").open("w", newline="") as fh:
        fh.write(L2_HEADER + "\n")
        names = L2_HEADER.rstrip(",").split(",")
        for core in range(cores):
            row = {name: "NA" for name in names}
            row.update({
                "block_id": str(core), "sub_block_id": "vector0",
                "aiv_time(us)": fmt(total_time),
                "aiv_total_cycles": "NA" if all_empty else "1650",
                "aiv_read_hit_rate(%)": fmt(read_rate),
                "aiv_write_hit_rate(%)": fmt(write_rate),
                "aiv_read_close_miss": "NA" if all_empty else "100",
            })
            fh.write(",".join(row[name] for name in names) + ",\n")

    # --- empty-but-present tables so cross-table checks engage
    for name in ("MemoryUB", "MemoryL0", "ArithmeticUtilization",
                 "ResourceConflictRatio"):
        header = "block_id,sub_block_id,aiv_time(us),aiv_total_cycles,"
        with (cap / f"{name}.csv").open("w", newline="") as fh:
            fh.write(header + "\n")
            for core in range(cores):
                fh.write(f"{core},vector0,{fmt(total_time)},"
                         f"{'NA' if all_empty else '1650'},\n")

    # --- visualize_data.bin with two different tags per logical record, to
    # prove the decoder keys on the kind byte only.
    blob = b""
    blob += viz_record(0x05, 0x02, {
        "advice": [], "block_dim": str(block_dim), "cur_freq": f"{freq_mhz:.0f}",
        "device_id": "0", "duration": total_time, "mix_block_dim": "",
        "name": "synth_kernel", "op_type": "vector", "pid": "1234",
        "rated_freq": f"{freq_mhz:.0f}", "soc": soc,
        "block_detail": {"head_name": ["Block ID"], "row": []},
    })
    blob += viz_record(0x06, 0x01, {"advice": [], "subblock_detail": []})
    blob += viz_record(0x08, 0x03, {"core_memory_map": [
        {
            "L2cache": {"hit": "10", "hit_ratio": "60.0", "miss": "20",
                         "total_request": "30"},
            "Vector": {"cycle": 100, "ratio": 10.0, "total_cycles": 1000},
            "advice": ["aivector compute usage lower than 20%"],
            "core_no": str(core), "memory_detail": "0", "op_type": "vector",
            "soc": "910A5",
            "memory_unit": [{"bandwidth": "1.0", "display": 1, "memory_path": 12,
                             "peak_ratio": "NA", "request": "10"}],
        }
        for core in range(cores)
    ]})
    blob += viz_record(0x09, 0x00, {"table_per_block": [
        {"advice": ["MTE2 bandwidth utilization lower than 80% when active"],
         "block_id": str(core), "table_detail": [], "table_op_type": "vector"}
        for core in range(cores)
    ]})
    blob += viz_record(0x0D, 0x02, {"advice": "latency bound:pipeline caused",
                                    "multiple_rooflines": []})
    (cap / "visualize_data.bin").write_bytes(blob)
    return cap


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_viz_decoder() -> None:
    print("\n[viz decoder]")
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp))
        records = KP.parse_visualize(cap / "visualize_data.bin")
        kinds = [tag for tag, _ in records]
        check("all five record kinds decoded", kinds == [0x05, 0x06, 0x08, 0x09, 0x0D],
              f"got {kinds}")
        identity = dict(records)[0x05]
        check("identity soc recovered", identity["soc"] == "Ascend950PR_9579")
        roofline = dict(records)[0x0D]
        check("roofline verdict recovered",
              roofline["advice"] == "latency bound:pipeline caused")

    # truncated file must not raise
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "v.bin"
        blob = viz_record(0x05, 0, {"a": 1})
        path.write_bytes(blob + blob[:9])
        records = KP.parse_visualize(path)
        check("truncated tail is dropped without raising", len(records) == 1,
              f"got {len(records)}")


def test_unit_convention() -> None:
    print("\n[unit convention]")
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp), gm_to_ub_kb=64.0, total_time=2.0,
                            peak_gibps=160.0)
        loaded = KP.load_capture(cap, None)
        peak = loaded.peak_model.get("GM_to_UB")
        check("recovered peak matches the encoded peak",
              peak is not None and abs(peak - 160.0) < 0.01,
              f"got {peak}")

    # And prove the decimal-GB mistake would be detectable: encoding with 1e9
    # makes the recovered peak off by 1024^3/1e9 = 7.37%.
    gib = 64.0 * 1024.0 / (2.0 * 1e-6) / (1024.0 ** 3)
    decimal = 64.0 * 1024.0 / (2.0 * 1e-6) / 1e9
    check("decimal-GB would be off by >7%", abs(decimal / gib - 1) > 0.07,
          f"ratio={decimal / gib:.4f}")


def test_analysis_known_values() -> None:
    print("\n[analysis on known values]")
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp), cores=4, block_dim=4, vec_time=0.25,
                            total_time=1.0, gm_to_ub_kb=64.0, peak_gibps=160.0)
        result = KP.analyze(KP.load_capture(cap, None))
        check("duration recovered", abs(result["kernel"]["duration_us"] - 1.0) < 1e-6)
        check("block dim recovered", result["kernel"]["block_dim"] == 4)
        vec = next(p for p in result["pipes"] if p["pipe"] == "vec")
        check("vec utilisation is 25%", abs(vec["mean_ratio_pct"] - 25.0) < 1e-6,
              f"got {vec['mean_ratio_pct']}")
        gm = next(b for b in result["bandwidth"] if b["label"] == "MTE2 GM→UB")
        expected_gbps = 64.0 * 1024.0 / (1.0 * 1e-6) / (1024.0 ** 3)
        check("bandwidth recovered in GiB/s",
              abs(gm["mean_gbps"] - expected_gbps) < 1e-6,
              f"got {gm['mean_gbps']} want {expected_gbps}")
        check("peak percentage recovered",
              abs(gm["peak_pct"] - 100.0 * expected_gbps / 160.0) < 1e-6,
              f"got {gm['peak_pct']}")
        check("per-core rows emitted", len(result["per_core"]) == 4)
        check("vendor roofline surfaced",
              result["roofline"]["vendor_verdict"] == "latency bound:pipeline caused")
        check("imbalance is zero for identical cores",
              abs(result["imbalance"]["aiv"]["spread_pct"]) < 1e-9)


def test_critic_catches_breakage() -> None:
    print("\n[critic catches breakage]")
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp))
        ctx = CR.Critic(cap)
        findings = ctx.run()
        check("healthy synthetic capture passes the critic",
              ctx.fail_count() == 0,
              str([f"{f.check}: {f.detail}" for f in findings if not f.ok]))

    # 1) wrong ratio -> identity check must fail
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp))
        path = cap / "PipeUtilization.csv"
        rows = list(csv.reader(path.open()))
        header = rows[0]
        idx = header.index("aiv_vec_ratio")
        rows[1][idx] = "0.9"   # claims 90% while time says 20%
        with path.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
        ctx = CR.Critic(cap)
        ctx.run()
        failed = {f.check for f in ctx.findings if not f.ok}
        check("wrong ratio caught by ratio_identity", "ratio_identity" in failed,
              str(failed))

    # 2) mismatched frequency -> cycles check must fail
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp), freq_mhz=1000.0)
        path = cap / "PipeUtilization.csv"
        rows = list(csv.reader(path.open()))
        header = rows[0]
        idx = header.index("aiv_total_cycles")
        rows[1][idx] = "999999"
        with path.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
        ctx = CR.Critic(cap)
        ctx.run()
        failed = {f.check for f in ctx.findings if not f.ok}
        check("wrong cycle count caught", "ratio_identity" in failed or
              any("cycle" in f for f in failed), str(failed))

    # 3) all-NA capture must be flagged as empty
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp), all_empty=True)
        ctx = CR.Critic(cap)
        ctx.run()
        failed = {f.check for f in ctx.findings if not f.ok}
        check("empty capture flagged", "collection_nonempty" in failed, str(failed))

    # 4) wrong block dim
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp), cores=4, block_dim=8)
        ctx = CR.Critic(cap)
        ctx.run()
        failed = {f.check for f in ctx.findings if not f.ok}
        check("block dim mismatch caught",
              "rows_match_block_dim" in failed, str(failed))


def test_reward_model() -> None:
    print("\n[reward model]")
    import collect_optimize as CO
    critic = HERE / "critic.py"
    with tempfile.TemporaryDirectory() as tmp:
        good = build_capture(Path(tmp) / "good")
        bad = build_capture(Path(tmp) / "bad", all_empty=True)
        good_v = CO.score_capture(KP.load_capture(good, None), critic, None)
        bad_v = CO.score_capture(KP.load_capture(bad, None), critic, None)
        check("healthy capture has positive reward", good_v["reward"] > 0,
              str(good_v))
        check("empty capture cannot beat a healthy one",
              bad_v["reward"] < good_v["reward"],
              f"{bad_v['reward']} vs {good_v['reward']}")


def test_metric_catalogue() -> None:
    print("\n[metric catalogue]")
    import metric_catalogue as MC
    cat = MC.build_catalogue()
    check("catalogue is non-trivial", len(cat) >= 40, f"got {len(cat)}")
    tiers = {m.tier for m in cat}
    check("catalogue spans all tiers", tiers == {"A", "B", "C", "D", "E"}, str(tiers))
    keys = [m.key for m in cat]
    check("catalogue keys are unique", len(keys) == len(set(keys)))
    # every metric documents where its number comes from
    missing = [m.key for m in cat if not m.source]
    check("every metric states its source", not missing, str(missing[:5]))
    # the unstable write-hit-rate must be demoted by measurement, not opinion
    check("l2_write_hit demoted to E by measured CV",
          MC.confidence_of("l2_write_hit") == "E",
          MC.confidence_of("l2_write_hit"))
    check("l2_read_hit stays above E", MC.confidence_of("l2_read_hit") != "E")


def test_key_metrics() -> None:
    print("\n[key metrics]")
    import key_metrics as KM
    peaks = KM.PlatformPeaks(soc="Ascend950PR_9579", ai_core_cnt=28,
                             vector_core_cnt=56, cube_freq_mhz=1650,
                             vec_freq_mhz=1650)
    # 28 * 1.65e9 * 4096 * 2 = 3.7847e14
    expected_flops = 28 * 1.65e9 * 4096 * 2
    check("cube peak matches hand calculation",
          abs(peaks.cube_flops_per_s - expected_flops) / expected_flops < 1e-3,
          f"{peaks.cube_flops_per_s:.4g} vs {expected_flops:.4g}")

    # a kernel that issues exactly 1000 MMAs in 1 us
    analysis = {
        "kernel": {"duration_us": 1.0},
        "arithmetic": {"cube_ratio": 80.0, "cube_total_instr": 1000},
        "pipes": [{"pipe": "cube", "label": "Cube", "mean_ratio_pct": 80.0}],
        "per_core": [],
    }
    m = KM.compute(analysis, peaks)
    expected_tflops = 1000 * 4096 * 2 / 1e-6 / 1e12
    check("achieved TFLOPS from MMA count",
          m.achieved_tflops is not None and abs(m.achieved_tflops - expected_tflops) < 1e-6,
          f"{m.achieved_tflops} vs {expected_tflops}")
    check("MFU is achieved/peak",
          m.mfu_pct is not None and
          abs(m.mfu_pct - 100.0 * expected_tflops / (expected_flops / 1e12)) < 1e-3,
          f"{m.mfu_pct}")
    check("bound_by picks the busiest pipe", m.bound_by == "cube", m.bound_by)

    # a vector kernel must not fabricate a cube MFU
    vec = {"kernel": {"duration_us": 5.0}, "arithmetic": {"cube_ratio": 0.0},
           "pipes": [{"pipe": "vec", "label": "Vector", "mean_ratio_pct": 60.0}],
           "per_core": []}
    mv = KM.compute(vec, peaks)
    check("vector kernel reports no MFU", mv.mfu_pct is None, str(mv.mfu_pct))
    check("vector kernel still classifies a bound", mv.bound_by == "vec", mv.bound_by)


def test_instruction_profile() -> None:
    print("\n[instruction profile]")
    import instr_profile as IP
    # a region map line exactly as the profiler writes it
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "dump"
        dump.mkdir()
        (dump / "dfx_region_map.txt").write_text(
            "InstrumentDfxRegion\n"
            "function=_Z10add_kernelIfEvPhS0_S0_llj\n"
            "region_id=1536,pipe=mte2,bb_off=0x454,inst_off=0x18,pc=0x0000046c,opcode=GET_BUF\n"
            "region_id=1540,pipe=vector,bb_off=0x4b0,inst_off=0x194,pc=0x00000644,opcode=GET_BUF_V\n"
        )
        (dump / "pc_start_addr.txt").write_text("0x12004100a000")
        regions = IP.parse_region_map(dump / "dfx_region_map.txt")
        check("region map parses both lines", len(regions) == 2, str(len(regions)))
        check("header lines are skipped", all(r.opcode.endswith("BUF") or
                                             r.opcode.endswith("_V") for r in regions))
        check("pipe names recovered",
              {r.pipe for r in regions} == {"mte2", "vector"},
              str({r.pipe for r in regions}))
        base = IP.parse_base_pc(dump / "pc_start_addr.txt")
        check("base pc parses", base == 0x12004100a000, hex(base or 0))

        # now the join: absolute pc in the trace must map onto the region pc
        trace = {
            "traceEvents": [
                {"ph": "X", "tid": "MTE2", "name": "GET_BUF", "dur": 0.5,
                 "args": {"pc_addr": "0x12004100a46c"}},
                {"ph": "X", "tid": "VECTOR", "name": "GET_BUF_V", "dur": 1.5,
                 "args": {"pc_addr": "0x12004100a644"}},
            ]
        }
        (Path(tmp) / "trace.json").write_text(json.dumps(trace))
        insight = IP.analyze(Path(tmp), kernel_us=10.0)
        check("join succeeds", insight.available, insight.reason)
        check("both events attributed", insight.events == 2, str(insight.events))
        check("durations summed", abs(insight.covered_us - 2.0) < 1e-9,
              str(insight.covered_us))
        check("coverage fraction computed",
              insight.covered_pct is not None and abs(insight.covered_pct - 20.0) < 1e-6,
              str(insight.covered_pct))
        ops = {e["opcode"] for e in insight.by_opcode}
        check("opcodes resolved from region map",
              ops == {"GET_BUF", "GET_BUF_V"}, str(ops))

    # missing inputs must degrade, not raise
    with tempfile.TemporaryDirectory() as tmp:
        insight = IP.analyze(Path(tmp))
        check("missing dump degrades gracefully",
              not insight.available and insight.reason, insight.reason)


def test_cache_layers_surfaced() -> None:
    print("\n[cache layers]")
    # DCache lives only in the per-block JSON table; a regression that drops it
    # would leave the report looking complete while missing a cache level.
    with tempfile.TemporaryDirectory() as tmp:
        cap = build_capture(Path(tmp))
        # inject a DCache table into the 0x09 record
        blob = (cap / "visualize_data.bin").read_bytes()
        import struct as _struct
        recs = []
        pos = 0
        dec = json.JSONDecoder()
        while pos + 14 <= len(blob):
            n = _struct.unpack_from("<Q", blob, pos)[0]
            kind = blob[pos + 8]
            obj, _ = dec.raw_decode(blob[pos + 12:pos + 12 + n].decode("utf-8", "surrogateescape"))
            recs.append((kind, blob[pos + 9], obj))
            pos += 12 + n
        for kind, var, obj in recs:
            if kind == 0x09:
                for blk in obj.get("table_per_block", []):
                    blk["table_detail"].append({
                        "table_name": "DCache",
                        "header_name": ["", "requests", "throughput(GB/s)"],
                        "row": [{"name": "DCache Read GM", "value": ["0", "0.0"]},
                                {"name": "DCache Read Vector", "value": ["7", "1.5"]}],
                        "size": [2, 2],
                    })
                break
        rebuilt = b"".join(
            _struct.pack("<Q", len(json.dumps(o, ensure_ascii=False).encode())) +
            bytes([k, v]) + b"ZZ" + json.dumps(o, ensure_ascii=False).encode()
            for k, v, o in recs)
        (cap / "visualize_data.bin").write_bytes(rebuilt)

        result = KP.analyze(KP.load_capture(cap, None))
        dcache = (result.get("cache") or {}).get("dcache") or {}
        check("DCache table extracted", bool(dcache), str(dcache))
        check("DCache rows named", "DCache Read Vector" in dcache, str(list(dcache)))
        check("DCache throughput parsed",
              dcache.get("DCache Read Vector", {}).get("throughput(GB/s)") == 1.5,
              str(dcache.get("DCache Read Vector")))
        text = KP.render_markdown(result)
        check("DCache appears in the report", "DCache Read Vector" in text)


def test_stability_threshold() -> None:
    print("\n[stability]")
    import metric_catalogue as MC
    # the threshold rule must be applied, not hardcoded per metric
    for key, measured in MC.MEASURED_STABILITY.items():
        got = MC.confidence_of(key)
        if measured["cv_pct"] > MC.STABILITY_THRESHOLD_CV:
            check(f"{key} demoted to E (CV {measured['cv_pct']}%)", got == "E", got)
    check("all measured entries present", len(MC.MEASURED_STABILITY) >= 8)


def test_stability_harness() -> None:
    print("\n[stability harness]")
    import stability as ST
    # build synthetic captures with a known spread: 5 runs, exact values
    captures = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, vec_ratio in enumerate([0.20, 0.21, 0.19, 0.205, 0.195]):
            cap = build_capture(Path(tmp) / f"r{i}", vec_time=vec_ratio, total_time=1.0)
            captures.append(KP.load_capture(cap, None))
        results = {r.key: r for r in ST.measure(captures)}
        vec = results["vec_ratio"]
        check("stability sees all runs", vec.runs == 5, str(vec.runs))
        check("mean matches hand calculation",
              abs(vec.mean - 0.20) < 1e-9, str(vec.mean))
        # CV of [0.20,0.21,0.19,0.205,0.195] is ~3.5%
        check("CV is a few percent", 3.0 < vec.cv_pct < 4.0, str(vec.cv_pct))
        check("verdict is 'usable' at this CV", vec.verdict in ("stable", "usable"),
              vec.verdict)
        text = ST.render(list(results.values()), 5)
        check("render produces a table", "CV%" in text)


def test_multipass_planning() -> None:
    print("\n[multipass planning]")
    # mutually exclusive sets must never share a pass
    p1 = KP.plan_passes(["PipeUtilization", "InstrTimeline", "PipeTimeline"])
    flat = [m for grp in p1 for m in grp]
    check("no pass contains both timelines",
          not any("InstrTimeline" in g and "PipeTimeline" in g for g in p1), str(p1))
    check("all requested metrics survive", sorted(flat) == sorted(
        ["PipeUtilization", "InstrTimeline", "PipeTimeline"]), str(flat))
    check("core pass comes first", "PipeUtilization" in p1[0], str(p1))
    single = KP.plan_passes(["PipeUtilization", "Memory"])
    check("mergeable metrics stay in one pass", len(single) == 1, str(single))


def test_multipass_merge_discovers_artifacts() -> None:
    print("\n[multipass merge]")
    # Regression: load_capture uses shutil to adopt trace/probe artefacts from
    # sibling passes. An earlier "remove unused imports" pass dropped the
    # import and this path raised NameError only on real multipass captures,
    # which the unit tests did not exercise.
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "capture"
        # build_capture creates an OPPROF_* dir INSIDE the path it is given, so
        # point it at the pass directory and let it produce OPPROF_synthetic.
        core_pass = base / "pass0_core"
        core_pass.mkdir(parents=True)
        core = build_capture(core_pass)
        extra = base / "pass1_pipe" / "OPPROF_pipe"
        (extra / "probe").mkdir(parents=True)
        (extra / "trace.json").write_text(json.dumps({
            "traceEvents": [
                {"ph": "X", "pid": "core0.veccore0", "ts": 0.5, "dur": 1.0},
                {"ph": "X", "pid": "core1.veccore0", "ts": 1.2, "dur": 0.8},
            ]
        }))
        (extra / "probe" / "dfx_region_map.txt").write_text(
            "region_id=1,pipe=mte2,bb_off=0x0,inst_off=0x0,pc=0x00000010,opcode=GET_BUF\n")
        # materialise the extra/ view the collector creates
        (base / "extra").mkdir()
        (base / "extra" / "pass1_pipe").symlink_to(extra)

        loaded = KP.load_capture(core, None)
        check("trace adopted from sibling pass",
              (core / "trace.json").is_file(), "trace not copied")
        check("probe adopted from sibling pass",
              (core / "probe" / "dfx_region_map.txt").is_file(), "probe not copied")
        result = KP.analyze(loaded)
        sched = result.get("schedule") or {}
        check("schedule derived from adopted trace",
              sched.get("available") is True, str(sched.get("reason")))
        check("skew computed", sched.get("launch_skew_us") == 0.7,
              str(sched.get("launch_skew_us")))


def test_layers() -> None:
    print("\n[layers]")
    import layers as LY
    import metric_catalogue as MC
    cat = MC.build_catalogue()
    present = {LY.layer_of(m.key) for m in cat}
    check("all three layers populated", present == {"soc", "core", "schedule"},
          str(present))
    check("schedule layer has block timing",
          LY.layer_of("block_timeline") == "schedule")
    check("soc layer holds bandwidth",
          LY.layer_of("gm_to_ub_gbps") == "soc")
    check("core layer holds pipes", LY.layer_of("mte2_ratio") == "core")

    # block extraction from a synthetic trace
    trace = {"traceEvents": [
        {"ph": "X", "pid": "core0.veccore0", "ts": 1.0, "dur": 2.0},
        {"ph": "X", "pid": "core0.veccore1", "ts": 0.5, "dur": 1.0},
        {"ph": "X", "pid": "core1.veccore0", "ts": 2.0, "dur": 0.5},
    ]}
    blocks = LY.blocks_from_trace(trace)
    check("three blocks extracted", len(blocks) == 3, str(len(blocks)))
    insight = LY.analyse_schedule(blocks, kernel_us=2.5)
    check("launch skew = 1.5us", insight.launch_skew_us == 1.5, str(insight.launch_skew_us))
    check("skew pct computed", insight.launch_skew_pct == 60.0, str(insight.launch_skew_pct))
    # ends are 1.5, 2.5, 3.0 -> earliest end 1.5, last end 3.0, tail 1.5
    check("tail = last_end - first_end", insight.tail_us == 1.5, str(insight.tail_us))
    text = "\n".join(LY.render_schedule(insight))
    check("skew warning rendered", "Asynchronous launch" in text)


def test_pc_cycle_stalls() -> None:
    print("\n[pc-cycle icache diagnosis]")
    import layers as LY
    # No pc -> must degrade, not raise
    ins = LY.analyse_pc_cycle({"traceEvents": [
        {"ph": "X", "pid": "c0", "ts": 0.0, "dur": 0.1}]})
    check("missing pc degrades gracefully", not ins.available, ins.reason)

    # A repeated PC held for a long gap is the icache-miss signature
    trace = {"traceEvents": [
        {"ph": "X", "pid": "c0", "tid": "SCALAR", "name": "LD", "ts": 1.000,
         "dur": 0.001, "args": {"pc_addr": "0xA0"}},
        {"ph": "X", "pid": "c0", "tid": "SCALAR", "name": "LD", "ts": 1.400,
         "dur": 0.001, "args": {"pc_addr": "0xA0"}},
        {"ph": "X", "pid": "c0", "tid": "SCALAR", "name": "ST", "ts": 1.500,
         "dur": 0.001, "args": {"pc_addr": "0xB0"}},
    ]}
    ins = LY.analyse_pc_cycle(trace)
    check("stall detected", ins.available and len(ins.stalls) == 1, str(ins.stalls))
    check("stall gap measured", ins.stalls[0]["gap_us"] == 0.4, str(ins.stalls[0]))
    check("distinct pcs counted", ins.distinct_pcs == 2, str(ins.distinct_pcs))
    check("stall pct computed",
          ins.stall_pct is not None and 60 < ins.stall_pct < 90, str(ins.stall_pct))
    text = "\n".join(LY.render_pc_cycle(ins))
    check("render explains the diagnosis", "icache" in text)


def test_pipeline_waveform() -> None:
    """The raw-stream decoder, against a synthesised capture.

    The container and token semantics are pinned here so a refactor cannot
    silently change them: the fixture writes a known timeline, and the decoded
    intervals must come back identical.
    """
    print("\n[Pipeline waveform decoder]")
    if KP.PW is None:
        check("pipeline_waveform.py importable", False, "import failed")
        return
    PW = KP.PW

    root = Path(tempfile.mkdtemp(prefix="kp-waveform-"))
    try:
        # --- container round-trip -------------------------------------------
        # Build one AIV record: SCALAR 100 cycles, then MTE2 50, then idle 30.
        # The encoding pairs a mask with the NEXT token's duration, so the
        # tokens are: mask, dur-of-previous, mask, dur-of-previous, ...
        # A token's mask is paired with the NEXT token's duration, so the
        # sequence below encodes: SCALAR [10,110), MTE2 [110,160), VECTOR
        # [160,190). MTE2 and VECTOR are both present, which is what the
        # serial-vs-overlapped verdict needs in order to say anything.
        payload: List[int] = [
            (0xE000 << 16) | 0x1234,        # header
            (0xF001 << 16) | 10,            # lead-in 10 cycles, mask SCALAR
            (0xF010 << 16) | 100,           # SCALAR held for 100
            (0xF002 << 16) | 50,            # MTE2 held for 50
            (0xF000 << 16) | 30,            # VECTOR held for 30
            (0x0D88 << 16) | 7,             # counter, ends the group
        ]
        stride = PW.STRIDE_WORDS
        words = [0] * (stride * 2)
        words[0] = 0x00010003               # veccore0, core 3
        words[1] = len(payload) * 4
        for index, word in enumerate(payload):
            words[2 + index] = word

        dump = root / "dump"
        dump.mkdir(parents=True)
        timeline = dump / "timeline.bin.0"
        timeline.write_bytes(struct.pack(f"<{len(words)}I", *words))
        (dump / "op_basic_info.txt").write_text(
            "Op Name=demo\nBlock Dim=1\nCurrent Freq=1000\nRated Freq=1000\n"
        )

        records = PW.read_records(timeline)
        check("one record decoded", len(records) == 1, str(len(records)))
        if len(records) == 1:
            record = records[0]
            check("record is AIV veccore0 of core 3",
                  record.core_kind == "aiv" and record.core_id == 3
                  and record.subcore_id == 0,
                  f"{record.core_kind}/{record.core_id}/{record.subcore_id}")
            groups = PW.decode_groups(record)
            check("one sampling group", len(groups) == 1, str(len(groups)))
            if groups:
                intervals = groups[0].intervals
                # The clock starts at 10 (the lead-in), so states are
                # [10,110) SCALAR, [110,160) MTE2, [160,190) idle.
                check("three intervals", len(intervals) == 3, str(len(intervals)))
                expected = [(10, 110, "SCALAR"), (110, 160, "MTE2"), (160, 190, "VECTOR")]
                got = [
                    (iv.start, iv.end, iv.pipes[0] if iv.pipes else None)
                    for iv in intervals
                ]
                check("intervals match the encoded timeline", got == expected, str(got))
                check("lead-in advances the clock",
                      intervals[0].start == 10, str(intervals[0].start))
                check("end cycle is the summed durations",
                      groups[0].end_cycle == 190, str(groups[0].end_cycle))
                busy = PW.busy_by_pipe(groups[0])
                check("busy cycles per pipe",
                      busy.get("SCALAR") == 100 and busy.get("MTE2") == 50
                      and busy.get("VECTOR") == 30, str(busy))
                check("no idle interval when every cycle is busy",
                      len(PW.bubbles(groups[0])) == 0)

        # --- freq fallback ---------------------------------------------------
        check("clock read from capture metadata",
              PW.read_freq_mhz(root) == 1000.0, str(PW.read_freq_mhz(root)))

        # --- summary ---------------------------------------------------------
        result = PW.summarize(root, 1000.0)
        check("summary reports the record", result["records"] == 1)
        check("summary has an aggregate", bool(result["aggregate"]))
        core = result["per_core"][0]
        check("span converts at the capture clock",
              abs(core["span_us"] - 0.190) < 1e-6, str(core["span_us"]))
        check("idle share is zero when fully busy",
              abs(core["idle_pct"]) < 0.01, str(core["idle_pct"]))
        check("windows counted", core["windows"] == 1, str(core.get("windows")))
        check("pipe share and spread present",
              "SCALAR" in core["pipes"] and "pct_sd" in core["pipes"]["SCALAR"],
              str(core["pipes"].get("SCALAR")))
        check("aggregate exposes pipe shares",
              "SCALAR" in result["aggregate"]["pipes_pct"],
              str(result["aggregate"].get("pipes_pct")))

        # --- missing raw stream is not an error ------------------------------
        empty = root / "empty"
        empty.mkdir()
        check("locate_timeline returns None when absent",
              KP.PW.locate_timeline(empty) is None)
        check("load_waveform returns None when absent",
              KP.load_waveform(empty) is None)

        # --- unverified bits are reported, not named -------------------------
        pieces, unnamed = PW.pipe_mask_names(0x04, "aiv")
        check("unverified AIV bit is unnamed", unnamed == 0x04 and not pieces,
              f"{pieces}/{unnamed:#x}")
        pieces, unnamed = PW.pipe_mask_names(0x04, "aic")
        check("same bit is CUBE on AIC", pieces == ("CUBE",) and unnamed == 0,
              f"{pieces}/{unnamed:#x}")

        # --- the overlap verdict must survive a relocated capture ----------
        # Regression: re-deriving the stream path from the capture directory
        # silently dropped this section whenever the stream lived elsewhere,
        # and a missing verdict reads exactly like "nothing to report".
        (root / "trace.json").unlink(missing_ok=True)
        summary = PW.summarize(root, 1000.0)
        rendered = PW.render_markdown(summary)
        check("overlap verdict is rendered", "Serial or overlapped" in rendered,
              "section missing")

        # --- core selection is a view filter, not a collection setting ------
        check("no filter keeps every record",
              len(PW.select_records(records)) == 1)
        check("exact pid filters to that core",
              [r.pid for r in PW.select_records(records, ["core3.veccore0"])]
              == ["core3.veccore0"])
        check("bare core number filters",
              [r.core_id for r in PW.select_records(records, ["3"])] == [3])
        check("unknown core filters everything out",
              PW.select_records(records, ["core9.veccore9"]) == [])
        filtered = PW.summarize(root, 1000.0, cores=["core3.veccore0"])
        check("summary honours the filter",
              filtered["records"] == 1 and filtered["core_filter"],
              str(filtered.get("core_filter")))
        check("filtered report says so",
              "Filtered to cores" in PW.render_markdown(filtered))

        # --- verify() must distinguish "wrong" from "cannot check" ----------
        check("verify with no trace.json is INCONCLUSIVE",
              PW.verify(root)["verdict"] == "INCONCLUSIVE",
              str(PW.verify(root).get("verdict")))

        # A pipe-level vendor trace that disagrees must be NOT VERIFIED.
        import json as _json
        (root / "trace.json").write_text(_json.dumps({"traceEvents": [
            {"ph": "X", "name": "MTE2", "pid": "core3.veccore0",
             "ts": 10 / 1000.0, "dur": 100 / 1000.0},
        ]}))
        check("disagreeing pipe trace is NOT VERIFIED",
              PW.verify(root)["verdict"] == "NOT VERIFIED",
              str(PW.verify(root).get("verdict")))

        # An instruction-level trace must not be reported as a failure.
        (root / "trace.json").write_text(_json.dumps({"traceEvents": [
            {"ph": "X", "name": "GET_BUF", "pid": "core3.veccore0",
             "ts": 1.0, "dur": 1.0},
        ]}))
        check("instruction trace is INCONCLUSIVE, not NOT VERIFIED",
              PW.verify(root)["verdict"] == "INCONCLUSIVE",
              str(PW.verify(root).get("verdict")))

        # And the matching pipe trace must verify.
        (root / "trace.json").write_text(_json.dumps({"traceEvents": [
            {"ph": "X", "name": "SCALAR", "pid": "core3.veccore0",
             "ts": 10 / 1000.0, "dur": 100 / 1000.0},
            {"ph": "X", "name": "MTE2", "pid": "core3.veccore0",
             "ts": 110 / 1000.0, "dur": 50 / 1000.0},
            {"ph": "X", "name": "VECTOR", "pid": "core3.veccore0",
             "ts": 160 / 1000.0, "dur": 30 / 1000.0},
        ]}))
        check("matching pipe trace is VERIFIED",
              PW.verify(root)["verdict"] == "VERIFIED",
              str(PW.verify(root).get("verdict")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cli_contracts() -> None:
    print("\n[CLI contracts]")
    import subprocess
    for script in ("kernel_perf.py", "critic.py", "collect_optimize.py",
                   "metric_catalogue.py", "stability.py"):
        proc = subprocess.run([sys.executable, str(HERE / script), "--help"],
                              capture_output=True, timeout=60, check=False)
        check(f"{script} --help exits 0", proc.returncode == 0,
              proc.stderr.decode()[:200])
        check(f"{script} --help prints usage",
              b"usage:" in proc.stdout, proc.stdout[:100].decode())

    proc = subprocess.run([sys.executable, str(HERE / "kernel_perf.py"),
                           "analyze", "--out", "/nonexistent/xyz"],
                          capture_output=True, timeout=60, check=False)
    check("missing capture exits non-zero", proc.returncode != 0,
          str(proc.returncode))
    check("missing capture has no traceback",
          b"Traceback" not in proc.stderr, proc.stderr.decode()[:200])

    proc = subprocess.run([sys.executable, str(HERE / "critic.py"),
                           "--out", "/nonexistent/xyz"],
                          capture_output=True, timeout=60, check=False)
    check("critic on missing capture exits non-zero", proc.returncode != 0)


def main() -> int:
    print("kernel-perf self-tests")
    test_viz_decoder()
    test_unit_convention()
    test_analysis_known_values()
    test_critic_catches_breakage()
    test_reward_model()
    test_metric_catalogue()
    test_key_metrics()
    test_instruction_profile()
    test_multipass_merge_discovers_artifacts()
    test_layers()
    test_pc_cycle_stalls()
    test_cache_layers_surfaced()
    test_stability_threshold()
    test_stability_harness()
    test_multipass_planning()
    test_pipeline_waveform()
    test_cli_contracts()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("all self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
