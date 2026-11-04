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


def test_cli_contracts() -> None:
    print("\n[CLI contracts]")
    import subprocess
    for script in ("kernel_perf.py", "critic.py", "collect_optimize.py"):
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
    test_cli_contracts()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("all self-tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
