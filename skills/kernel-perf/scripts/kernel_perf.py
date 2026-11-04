#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ascend kernel hardware-level performance analyzer.

Data source, in order of authority:

1. ``msopprof`` CSV files (PipeUtilization / ArithmeticUtilization / Memory /
   MemoryUB / MemoryL0 / L2Cache / ResourceConflictRatio / OpBasicInfo).
   These are the vendor's own published metric dump.
2. ``visualize_data.bin`` -- the private binary that backs ``msopprof``'s HTML
   report. Container format reverse engineered: a sequence of records
   ``<u64 payload_len><4-byte tag><payload>`` whose payload starts with the
   ASCII marker ``ZZ`` followed by one JSON object. Record tags observed on
   CANN 9.3.0 / Ascend950PR: 0502 (identity+block list), 0601 (AIV sub-block),
   0703 (AIC sub-block), 0800 (per-core memory map), 0902 (per-block tables).
   It carries data the CSV does not publish: per-core L2 hit/miss split by
   access path, iCache hit ratio, the ``wait cycles`` column of each pipe, the
   eight memory-path bandwidths, Roofline verdict and vendor advice.
   Record kinds observed: 0x05 identity, 0x06 AIV sub-block, 0x07 AIC
   sub-block, 0x08 per-core memory map, 0x09 per-block tables, 0x0C per-core
   throughput detail, 0x0D roofline verdict (present only when the Roofline
   metric set was collected).
3. ``platform_config/<soc>.ini`` -- hardware peak constants (frequencies,
   buffer sizes, link rates) used to turn raw counters into "% of peak".

The tool never invents a number: every derived value in the report is traceable
to one CSV field, one JSON field of visualize_data.bin, or one ini key.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field

# Sibling modules. Imported defensively so the tool still runs if someone
# copies only kernel_perf.py -- the catalogue and key metrics are enrichments,
# not prerequisites for reading a capture.
try:
    import metric_catalogue as MC
except ImportError:  # pragma: no cover
    MC = None
try:
    import key_metrics as KM
except ImportError:  # pragma: no cover
    KM = None
try:
    import instr_profile as IP
except ImportError:  # pragma: no cover
    IP = None
try:
    import layers as LY
except ImportError:  # pragma: no cover
    LY = None
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "1.0.0"

# Metric groups we always request. Each is an independent msopprof collection
# pass merged into a single OPPROF_* directory; the CSV set below is the union.
#
# Roofline is included because it costs one extra pass and adds the only source
# of the vendor's own verdict (record 0x0D). collect_optimize.py selected this
# set over the plain 7-group set on Ascend950PR: 15/15 critic checks passed with
# strictly more coverage. Do NOT grow this list past ~8 groups: an 11-group
# request returns success while writing an all-NA capture (see dav3510-notes).
DEFAULT_METRICS = [
    "PipeUtilization",
    "ArithmeticUtilization",
    "Memory",
    "MemoryUB",
    "MemoryL0",
    "L2Cache",
    "ResourceConflictRatio",
    "Roofline",
]

# Metric sets that must be collected in their OWN msopprof invocation.
#
# Measured on dav-3510 / CANN 9.3.0:
#   * PipeTimeline and InstrTimeline are mutually exclusive -- passing both is
#     rejected outright ("cannot be specified together").
#   * InstrTimeline combined with the 8 core groups does not error; it hangs
#     the collection instead, which is worse. So it gets its own pass.
#   * Source / PCSampling require the kernel to be rebuilt with -g and produce
#     a large visualize_data.bin; keeping them separate stops a missing debug
#     build from costing the whole collection.
#
# The collector therefore runs one pass per group and merges the results.
MULTIPASS_GROUPS = {
    "core": DEFAULT_METRICS,
    "instr": ["InstrTimeline"],
    "pipe": ["PipeTimeline"],
    "source": ["Source"],
}

NA = "NA"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    """Parse a CSV cell. Returns None for blank/NA/non-numeric."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text == NA or text.lower() in ("nan", "none", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fmt(value: Optional[float], unit: str = "", digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}{unit}"


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    kept = [v for v in values if v is not None]
    if not kept:
        return None
    return sum(kept) / len(kept)


def _spread(values: Iterable[Optional[float]]) -> Optional[float]:
    """(max-min)/mean in percent -- the kernel-load-imbalance measure."""
    kept = [v for v in values if v is not None]
    if len(kept) < 2:
        return None
    mean = sum(kept) / len(kept)
    if mean == 0:
        return None
    return 100.0 * (max(kept) - min(kept)) / mean


# ---------------------------------------------------------------------------
# CANN environment discovery
# ---------------------------------------------------------------------------

CANN_ROOTS = [
    "/home/developer/Ascend/cann-9.3.0",
    "/usr/local/Ascend/ascend-toolkit/latest",
    "/usr/local/Ascend/ascend-toolkit",
]


def find_cann_home() -> Optional[Path]:
    """Locate a CANN install with msopprof and set_env.sh.

    Checks ASCEND_HOME_PATH first (the wrapper may already have exported it),
    then the well-known install roots. A candidate only counts if it has both
    set_env.sh and tools/msopprof/bin/msopprof -- otherwise the caller would
    fail much later with a confusing error.
    """
    candidates: List[Path] = []
    for key in ("ASCEND_HOME_PATH", "ASCEND_OPP_PATH"):
        raw = os.environ.get(key)
        if raw:
            path = Path(raw)
            if key == "ASCEND_OPP_PATH":
                path = path.parent
            candidates.append(path)
    candidates.extend(Path(p) for p in CANN_ROOTS)

    # /home/developer/Ascend/cann-* -- pick the highest version dir
    developer = Path("/home/developer/Ascend")
    if developer.is_dir():
        found = sorted(
            (p for p in developer.iterdir() if p.name.startswith("cann-")),
            reverse=True,
        )
        candidates.extend(found)

    for path in candidates:
        try:
            if (path / "set_env.sh").is_file() and (
                path / "tools" / "msopprof" / "bin" / "msopprof"
            ).is_file():
                return path
        except OSError:
            continue
    return None


def cann_env(home: Path) -> Dict[str, str]:
    """Build an environment with CANN's set_env.sh applied.

    The login shell deliberately does not load CANN, so every subprocess that
    needs msopprof/bisheng gets its environment from here. We parse the sourced
    environment once instead of sourcing the script per call.
    """
    env = dict(os.environ)
    env["ASCEND_HOME_PATH"] = str(home)
    set_env = home / "set_env.sh"
    script = f"set -a; . '{set_env}' >/dev/null 2>&1; env -0"
    try:
        out = subprocess.run(
            ["bash", "-c", script], capture_output=True, timeout=120, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        out = b""
    if out:
        for chunk in out.split(b"\0"):
            if not chunk or b"=" not in chunk:
                continue
            key, _, value = chunk.partition(b"=")
            try:
                env[key.decode()] = value.decode()
            except UnicodeDecodeError:
                continue
    # set_env.sh sometimes only sets PATH inside the sourced shell; be explicit.
    subdirs = [home / "tools" / "msopprof" / "bin", home / "x86_64-linux" / "bin"]
    path_parts = [str(p) for p in subdirs if p.is_dir()]
    if path_parts:
        env["PATH"] = os.pathsep.join(path_parts + [env.get("PATH", "")])
    return env


# ---------------------------------------------------------------------------
# platform constants
# ---------------------------------------------------------------------------

@dataclass
class Platform:
    """Hardware peak constants parsed from platform_config/<soc>.ini."""

    soc: str
    raw: Dict[str, str] = field(default_factory=dict)

    def get_int(self, key: str) -> Optional[int]:
        value = self.raw.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def get_float(self, key: str) -> Optional[float]:
        value = self.raw.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    # -- convenience accessors, all None when the ini lacks the key ---------
    @property
    def cube_freq_mhz(self) -> Optional[float]:
        return self.get_float("cube_freq")

    @property
    def vec_freq_mhz(self) -> Optional[float]:
        return self.get_float("vec_freq")

    @property
    def ai_core_cnt(self) -> Optional[int]:
        return self.get_int("ai_core_cnt")

    @property
    def vector_core_cnt(self) -> Optional[int]:
        return self.get_int("vector_core_cnt")

    @property
    def cube_core_cnt(self) -> Optional[int]:
        return self.get_int("cube_core_cnt")

    @property
    def l1_size(self) -> Optional[int]:
        return self.get_int("l1_size")

    @property
    def ub_size(self) -> Optional[int]:
        return self.get_int("ub_size")

    @property
    def l0a_size(self) -> Optional[int]:
        return self.get_int("l0_a_size")

    @property
    def l0b_size(self) -> Optional[int]:
        return self.get_int("l0_b_size")

    @property
    def l0c_size(self) -> Optional[int]:
        return self.get_int("l0_c_size")

    @property
    def l2_size(self) -> Optional[int]:
        return self.get_int("l2_size")

    @property
    def ddr_rate(self) -> Optional[float]:
        """GB/s class link rate that applies to a core type."""
        return self.get_float("ddr_rate")

    @property
    def l2_rate(self) -> Optional[float]:
        return self.get_float("l2_rate")

    @property
    def io_die_num(self) -> Optional[int]:
        """Number of dies. 2 on Ascend950PR -- cross-die traffic exists and
        goes over SIO, so this changes how bandwidth numbers should be read."""
        return self.get_int("io_die_num")


def load_platform_for_soc(soc: str, home: Optional[Path]) -> Optional[Platform]:
    """Find platform_config/<soc>.ini for e.g. 'Ascend950PR_9579'."""
    if not soc:
        return None
    dirs: List[Path] = []
    if home is not None:
        dirs.append(home / "x86_64-linux" / "data" / "platform_config")
        dirs.append(home / "aarch64-linux" / "data" / "platform_config")
        dirs.append(home / "compiler" / "data" / "platform_config")
    for base in dirs:
        ini = base / f"{soc}.ini"
        if ini.is_file():
            return Platform(soc=soc, raw=parse_ini(ini))
    return None


def parse_ini(path: Path) -> Dict[str, str]:
    """Flatten an ini into key->value. Section names are kept as keys too."""
    values: Dict[str, str] = {}
    section = ""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    if section:
        values["__last_section__"] = section
    return values


# ---------------------------------------------------------------------------
# msopprof wrapper
# ---------------------------------------------------------------------------

@dataclass
class Capture:
    """One OPPROF_* directory plus everything we parsed out of it."""

    root: Path
    op_name: str = ""
    op_type: str = ""
    duration_us: Optional[float] = None
    block_dim: Optional[int] = None
    mix_block_dim: Optional[int] = None
    device_id: Optional[int] = None
    pid: Optional[int] = None
    cur_freq: Optional[float] = None
    rated_freq: Optional[float] = None
    soc: str = ""

    tables: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)
    viz: Dict[str, Any] = field(default_factory=dict)
    viz_cores: List[Dict[str, Any]] = field(default_factory=list)
    viz_blocks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    platform: Optional[Platform] = None
    peak_model: Dict[str, Optional[float]] = field(default_factory=dict)
    peak_source: Dict[str, str] = field(default_factory=dict)
    platform_reference: Dict[str, Optional[float]] = field(default_factory=dict)
    advice: List[str] = field(default_factory=list)
    block_advice: Dict[str, List[str]] = field(default_factory=dict)
    roofline: str = ""
    roofline_vendor: str = ""
    op_detail: List[Dict[str, Any]] = field(default_factory=list)
    timeline: Optional[Dict[str, Any]] = None
    trace_path: Optional[Path] = None

    def per_core(self, table: str) -> List[Dict[str, Any]]:
        """Rows of one CSV table with numeric fields parsed."""
        out = []
        for row in self.tables.get(table, []):
            entry: Dict[str, Any] = dict(row)
            entry["__num__"] = {k: _num(v) for k, v in row.items()}
            out.append(entry)
        return out

    def column(self, table: str, field_name: str) -> List[Optional[float]]:
        return [_num(r.get(field_name)) for r in self.tables.get(table, [])]

    def first(self, table: str, field_name: str) -> Optional[float]:
        values = self.column(table, field_name)
        return values[0] if values else None


def csv_rows(path: Path) -> List[Dict[str, str]]:
    """Read a profiler CSV, tolerating trailing empty columns."""
    try:
        with path.open(newline="", errors="replace") as handle:
            reader = csv.DictReader(handle)
            rows = []
            for row in reader:
                cleaned = {k.strip(): (v if v is not None else "") for k, v in row.items() if k is not None}
                cleaned.pop("", None)
                rows.append(cleaned)
            return rows
    except OSError:
        return []


def parse_op_basic_info(path: Path, capture: Capture) -> None:
    rows = csv_rows(path)
    if not rows:
        return
    row = rows[0]
    capture.op_name = (row.get("Op Name") or "").strip()
    capture.op_type = (row.get("Op Type") or "").strip()
    capture.duration_us = _num(row.get("Task Duration(us)"))
    block_dim = _num(row.get("Block Dim"))
    capture.block_dim = int(block_dim) if block_dim is not None else None
    mix = _num(row.get("Mix Block Dim"))
    capture.mix_block_dim = int(mix) if mix is not None else None
    device = _num(row.get("Device Id"))
    capture.device_id = int(device) if device is not None else None
    pid = _num(row.get("Pid"))
    capture.pid = int(pid) if pid is not None else None
    capture.cur_freq = _num(row.get("Current Freq"))
    capture.rated_freq = _num(row.get("Rated Freq"))


# --- visualize_data.bin ----------------------------------------------------

# The second tag byte is NOT part of the record identity: the same logical
# record appears with different values across captures (e.g. identity is
# 05/00 for a vector kernel and 05/00 for a cube kernel while per-block tables
# are 09/02 or 09/00). Only the first byte names the record.
VIZ_TAG_NAMES = {
    0x05: "identity",
    0x06: "aiv_subblock",
    0x07: "aic_subblock",
    0x08: "core_memory_map",
    0x09: "per_block_tables",
    0x0C: "op_detail",
    0x0D: "roofline",
}


def parse_visualize(path: Path) -> List[Tuple[int, Dict[str, Any]]]:
    """Decode visualize_data.bin into its JSON records.

     Layout (reverse engineered, stable across the CANN 9.x builds we tested):
        repeat:
            u64 payload_len          little endian, excludes this header
            u8  kind                 observed 05/06/07/08/09/0C/0D
            u8  variant              varies per capture, NOT part of identity
            b'ZZ'                    fixed magic
            payload_len - 2 bytes    one UTF-8 JSON object, NUL padded
    A malformed record stops parsing -- we return what was decoded so far
    rather than raising, because the earlier records are still useful.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    records: List[Tuple[int, Dict[str, Any]]] = []
    decoder = json.JSONDecoder()
    pos = 0
    total = len(data)
    while pos + 14 <= total:
        try:
            payload_len = struct.unpack_from("<Q", data, pos)[0]
        except struct.error:
            break
        if payload_len < 3 or pos + 12 + payload_len > total:
            break
        kind = data[pos + 8]
        magic = data[pos + 10:pos + 12]
        if magic != b"ZZ":
            break
        payload = data[pos + 12:pos + 12 + payload_len]
        try:
            obj, _ = decoder.raw_decode(payload.decode("utf-8", "surrogateescape"))
        except (UnicodeDecodeError, ValueError):
            break
        tag = kind
        records.append((tag, obj))
        pos += 12 + payload_len
    return records


# --- bandwidth peak derivation -------------------------------------------

# Memory.csv pairs a data-volume column with a "% of peak" column. Both describe
# the same transfer, so peak = (bytes/time) / (pct/100). This lets us recover the
# exact denominator msopprof divides by, instead of hardcoding a datasheet value.
#
# Unit convention, verified numerically to 1e-6 on Ascend950PR: the *_datas(KB)
# columns are KiB (1024 B) and the *(GB/s) columns are GiB/s (1024^3 B/s), i.e.
#   datas_KiB * 1024 / time_s / 1024**3  ==  published GB/s
# Using 1e9 instead reproduces the published bandwidth only to within 4.9%, so a
# decimal-GB assumption would silently corrupt every peak percentage.
BW_PEAK_PAIRS = [
    ("GM_to_UB_datas(KB)", "GM_to_UB_bw_usage_rate(%)", "GM_to_UB"),
    ("UB_to_GM_datas(KB)", "UB_to_GM_bw_usage_rate(%)", "UB_to_GM"),
    ("GM_to_L1_datas(KB)", "GM_to_L1_bw_usage_rate(%)", "GM_to_L1"),
    ("L0C_to_L1_datas(KB)", "L0C_to_L1_bw_usage_rate(%)", "L0C_to_L1"),
    ("L0C_to_GM_datas(KB)", "L0C_to_GM_bw_usage_rate(%)", "L0C_to_GM"),
]


def derive_bw_peaks(rows: List[Dict[str, str]]) -> Dict[str, List[float]]:
    """Recover the peak GB/s msopprof used, per memory path.

    Uses every core row that has both a non-zero volume and a non-zero
    percentage. Identical implied peaks across cores mean the derivation is
    self-consistent; spread means the source data is noisy and we keep all
    samples so the caller can see that.
    """
    peaks: Dict[str, List[float]] = {}
    for row in rows:
        time_us = _num(row.get("aiv_time(us)")) or _num(row.get("aic_time(us)"))
        if not time_us:
            continue
        for data_key, pct_key, name in BW_PEAK_PAIRS:
            volume_kb = _num(row.get(data_key))
            pct = _num(row.get(pct_key))
            if not volume_kb or not pct or pct <= 0:
                continue
            gibps = volume_kb * 1024.0 / (time_us * 1e-6) / (1024.0 ** 3)
            peaks.setdefault(name, []).append(gibps / (pct / 100.0))
    return peaks


# ---------------------------------------------------------------------------
# capture loading
# ---------------------------------------------------------------------------

def find_capture_root(path: Path) -> Optional[Path]:
    """Given a dir, find the OPPROF_* (or PROF_*) capture inside it."""
    if not path.exists():
        return None
    if path.is_dir() and (path / "OpBasicInfo.csv").is_file():
        return path
    if path.is_dir():
        for pattern in ("OPPROF_*", "PROF_*"):
            matches = sorted(p for p in path.glob(pattern) if p.is_dir())
            if matches:
                return matches[-1]
        # one level deeper (msopprof sometimes nests by device)
        for sub in sorted(p for p in path.iterdir() if p.is_dir()):
            for pattern in ("OPPROF_*", "PROF_*"):
                matches = sorted(p for p in sub.glob(pattern) if p.is_dir())
                if matches:
                    return matches[-1]
    return None


def load_capture(path: Path, home: Optional[Path] = None) -> Capture:
    root = find_capture_root(path)
    if root is None:
        raise FileNotFoundError(f"no OPPROF_*/PROF_* capture under {path}")

    capture = Capture(root=root)

    basic = root / "OpBasicInfo.csv"
    if basic.is_file():
        parse_op_basic_info(basic, capture)

    for table in (
        "PipeUtilization", "ArithmeticUtilization", "Memory", "MemoryUB",
        "MemoryL0", "L2Cache", "ResourceConflictRatio",
    ):
        csv_path = root / f"{table}.csv"
        if csv_path.is_file():
            capture.tables[table] = csv_rows(csv_path)
        else:
            nested = list(root.glob(f"device*/*/{table}.csv"))
            if nested:
                capture.tables[table] = csv_rows(nested[0])

    viz_path = root / "visualize_data.bin"
    if viz_path.is_file():
        records = parse_visualize(viz_path)
        for tag, obj in records:
            name = VIZ_TAG_NAMES.get(tag, f"tag_{tag:04x}")
            if name == "identity":
                capture.viz = obj
                capture.soc = str(obj.get("soc") or capture.soc)
                capture.advice.extend(obj.get("advice") or [])
                if capture.duration_us is None:
                    capture.duration_us = _num(obj.get("duration"))
                if capture.block_dim is None:
                    bd = _num(obj.get("block_dim"))
                    capture.block_dim = int(bd) if bd is not None else None
                if capture.cur_freq is None:
                    capture.cur_freq = _num(obj.get("cur_freq"))
                if capture.rated_freq is None:
                    capture.rated_freq = _num(obj.get("rated_freq"))
                if capture.pid is None:
                    capture.pid = int(_num(obj.get("pid")) or 0) or None
                if capture.device_id is None:
                    dev = _num(obj.get("device_id"))
                    capture.device_id = int(dev) if dev is not None else None
                if not capture.op_name:
                    capture.op_name = str(obj.get("name") or "")
                if not capture.op_type:
                    capture.op_type = str(obj.get("op_type") or "")
            elif name == "core_memory_map":
                capture.viz_cores = obj.get("core_memory_map") or []
            elif name == "per_block_tables":
                for block in obj.get("table_per_block") or []:
                    block_id = str(block.get("block_id", ""))
                    capture.viz_blocks[block_id] = block
                    if block.get("advice"):
                        capture.block_advice[block_id] = list(block["advice"])
            elif name in ("aiv_subblock", "aic_subblock"):
                capture.viz.setdefault("subblock", []).extend(
                    obj.get("subblock_detail") or []
                )
                if obj.get("advice"):
                    capture.advice.extend(obj["advice"])
            elif name == "op_detail":
                capture.op_detail = obj.get("op_detail") or []
            elif name == "roofline":
                # 0x0D carries the vendor's Roofline verdict as a plain string,
                # e.g. "latency bound:pipeline caused". It exists only when the
                # Roofline metric set was collected.
                capture.roofline_vendor = str(obj.get("advice") or "")
                capture.viz.setdefault("rooflines", []).extend(
                    obj.get("multiple_rooflines") or []
                )

    # Roofline verdict: the dedicated 0x0D record is authoritative; the advice
    # scan is only a fallback for older captures that lack it.
    if capture.roofline_vendor:
        capture.roofline = capture.roofline_vendor
    else:
        for text in capture.advice:
            if "bound" in str(text).lower():
                capture.roofline = str(text)
                break

    # Multi-pass collections keep instruction/timeline artefacts in sibling
    # passes (capture/extra/<pass>/). Adopt them when the core pass has none, so
    # a single `analyze` call sees everything that was collected.
    extra_candidates = [root.parent / "extra", root.parent.parent / "extra"]
    for extra_root in extra_candidates:
        if not extra_root.is_dir():
            continue
        # Two layouts occur: extra/<pass>/OPPROF_*/trace.json (what the
        # collector writes) and extra/<pass> -> OPPROF_* directly (symlinked).
        # Match both rather than assuming one.
        if not (root / "probe").is_dir():
            for pattern in ("*/OPPROF_*/probe", "*/probe"):
                for probe in sorted(extra_root.glob(pattern)):
                    try:
                        shutil.copytree(probe, root / "probe", dirs_exist_ok=True)
                        break
                    except OSError:
                        continue
                if (root / "probe").is_dir():
                    break
        if not (root / "trace.json").is_file():
            for pattern in ("*/OPPROF_*/trace.json", "*/trace.json"):
                for trace_file in sorted(extra_root.glob(pattern)):
                    try:
                        shutil.copy2(trace_file, root / "trace.json")
                        break
                    except OSError:
                        continue
                if (root / "trace.json").is_file():
                    break
        if (root / "trace.json").is_file() or (root / "probe").is_dir():
            break

    trace = root / "trace.json"
    if trace.is_file():
        capture.trace_path = trace
        try:
            capture.timeline = json.loads(trace.read_text(errors="replace"))
        except (OSError, ValueError):
            capture.timeline = None

    capture.platform = load_platform_for_soc(capture.soc, home)
    if not capture.soc and capture.platform is not None:
        capture.soc = capture.platform.soc

    # Peak model: prefer values recovered from the capture itself (they are the
    # ones msopprof actually divided by); fall back to ini link rates.
    peaks = derive_bw_peaks(capture.tables.get("Memory", []))
    for name, samples in peaks.items():
        if samples:
            capture.peak_model[name] = sum(samples) / len(samples)
            capture.peak_source[name] = "capture (bw_usage_rate)"
    # The ini link rates ([AICoreMemoryRates]/[VectorCoreMemoryRates]) are NOT
    # usable as peak denominators: ddr_rate=16/31, l2_rate=100 are unitless
    # class rates, not the GB/s a single core can move. Dividing a measured
    # GB/s by them produces nonsense percentages, so ini values are surfaced
    # separately as reference only and never used to compute peak_pct.
    if capture.platform is not None:
        capture.platform_reference = {
            "ddr_rate (AICore)": capture.platform.get_float("ddr_rate"),
            "l2_rate": capture.platform.l2_rate,
            "l1_to_ub_rate": capture.platform.get_float("l1_to_ub_rate"),
            "ub_to_ddr_rate": capture.platform.get_float("ub_to_ddr_rate"),
            "cube_freq_mhz": capture.platform.cube_freq_mhz,
            "vec_freq_mhz": capture.platform.vec_freq_mhz,
        }
    return capture


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------

PIPE_INFO = {
    "aic_cube": ("cube", "Cube 计算"),
    "aic_scalar": ("aic_scalar", "AIC scalar 标量"),
    "aic_mte1": ("mte1", "MTE1 (L1->L0)"),
    "aic_mte2": ("mte2", "MTE2 (GM->L1)"),
    "aic_mte3": ("mte3", "MTE3 (L1/UB->GM)"),
    "aic_fixpipe": ("fixpipe", "FixPipe (L0C->UB/GM)"),
    "aiv_vec": ("vec", "Vector 计算"),
    "aiv_scalar": ("aiv_scalar", "AIV scalar 标量"),
    "aiv_mte2": ("aiv_mte2", "AIV MTE2 (GM->UB)"),
    "aiv_mte3": ("aiv_mte3", "AIV MTE3 (UB->GM)"),
}

CORE_PREFIX = {"aic": "aic", "aiv": "aiv"}


def core_type_of(row: Dict[str, Any]) -> str:
    """Which core type this CSV row describes (aic / aiv)."""
    numbers = row.get("__num__") or {}
    if numbers.get("aic_time(us)") is not None:
        return "aic"
    return "aiv"


def pipe_breakdown(capture: Capture) -> List[Dict[str, Any]]:
    """Aggregate per-pipe busy time across all cores.

    Pipe busy time is a duration, so summing across cores gives core-us of
    work, and dividing by (cores * mean core time) gives the average
    utilisation of that pipe over the kernel.
    """
    rows = capture.per_core("PipeUtilization")
    out: List[Dict[str, Any]] = []
    for prefix, (key, label) in PIPE_INFO.items():
        times = [_num(r.get(f"{prefix}_time(us)")) for r in rows]
        ratios = [_num(r.get(f"{prefix}_ratio")) for r in rows]
        kept_ratio = [v for v in ratios if v is not None]
        if not kept_ratio:
            continue
        out.append({
            "pipe": key,
            "label": label,
            "field": prefix,
            "mean_ratio_pct": 100.0 * _mean(kept_ratio),
            "total_time_us": _mean(times) * len([t for t in times if t is not None])
            if any(t is not None for t in times) else None,
            "cores_active": sum(1 for v in kept_ratio if v > 0),
        })
    out.sort(key=lambda e: e["mean_ratio_pct"], reverse=True)
    return out


def bandwidth_breakdown(capture: Capture) -> List[Dict[str, Any]]:
    """Every bandwidth column, with % of the recovered hardware peak."""
    rows = capture.per_core("Memory")
    ub_rows = capture.per_core("MemoryUB")
    l0_rows = capture.per_core("MemoryL0")
    specs: List[Tuple[str, str, str, Optional[str]]] = [
        # (label, table, field, peak key)
        ("MTE2 GM→UB", "Memory", "aiv_gm_to_ub_bw(GB/s)", "GM_to_UB"),
        ("MTE2 GM→L1", "Memory", "aic_mte2_active_bw(GB/s)", None),
        ("MTE3 UB→GM", "Memory", "aiv_ub_to_gm_bw(GB/s)", "UB_to_GM"),
        ("主存读", "Memory", "aiv_main_mem_read_bw(GB/s)", None),
        ("主存写", "Memory", "aiv_main_mem_write_bw(GB/s)", None),
        ("L1 读", "Memory", "aic_l1_read_bw(GB/s)", None),
        ("L1 写", "Memory", "aic_l1_write_bw(GB/s)", None),
        ("UB 读 (vector)", "MemoryUB", "aiv_ub_read_bw_vector(GB/s)", None),
        ("UB 写 (vector)", "MemoryUB", "aiv_ub_write_bw_vector(GB/s)", None),
        ("UB 读 (GM)", "MemoryUB", "aiv_ub_read_bw_gm(GB/s)", None),
        ("UB 写 (GM)", "MemoryUB", "aiv_ub_write_bw_gm(GB/s)", None),
        ("L0A 读", "MemoryL0", "aic_l0a_read_bw(GB/s)", None),
        ("L0A 写", "MemoryL0", "aic_l0a_write_bw(GB/s)", None),
        ("L0B 读", "MemoryL0", "aic_l0b_read_bw(GB/s)", None),
        ("L0B 写", "MemoryL0", "aic_l0b_write_bw(GB/s)", None),
        ("L0C 读", "MemoryL0", "aic_l0c_read_bw_cube(GB/s)", None),
        ("L0C 写", "MemoryL0", "aic_l0c_write_bw_cube(GB/s)", None),
    ]
    table_map = {"Memory": rows, "MemoryUB": ub_rows, "MemoryL0": l0_rows}
    out: List[Dict[str, Any]] = []
    for label, table, field_name, peak_key in specs:
        values = [_num(r.get(field_name)) for r in table_map[table]]
        kept = [v for v in values if v is not None]
        if not kept:
            continue
        peak = capture.peak_model.get(peak_key) if peak_key else None
        entry = {
            "label": label,
            "field": field_name,
            "table": table,
            "mean_gbps": _mean(kept),
            "max_gbps": max(kept),
            "peak_gbps": peak,
            "peak_pct": (100.0 * _mean(kept) / peak) if peak else None,
        }
        out.append(entry)

    # Explicit "% of peak" columns published by msopprof itself.
    for data_key, pct_key, name in BW_PEAK_PAIRS:
        values = [_num(r.get(pct_key)) for r in rows]
        kept = [v for v in values if v is not None]
        if kept:
            out.append({
                "label": f"{name} (vendor %)",
                "field": pct_key,
                "table": "Memory",
                "mean_gbps": _mean([_num(r.get(data_key)) and None for r in rows]),
                "max_gbps": None,
                "peak_gbps": capture.peak_model.get(name),
                "peak_pct": _mean(kept),
            })
    return out


def cache_stats(capture: Capture) -> Dict[str, Any]:
    """L2 + iCache behaviour, CSV first then per-core JSON enrichment."""
    rows = capture.per_core("L2Cache")
    result: Dict[str, Any] = {"available": bool(rows)}
    if rows:
        for prefix in ("aiv", "aic"):
            read_rate = [_num(r.get(f"{prefix}_read_hit_rate(%)")) for r in rows]
            write_rate = [_num(r.get(f"{prefix}_write_hit_rate(%)")) for r in rows]
            kept_r = [v for v in read_rate if v is not None]
            kept_w = [v for v in write_rate if v is not None]
            if not kept_r and not kept_w:
                continue
            result[prefix] = {
                "read_hit_pct": _mean(kept_r),
                "write_hit_pct": _mean(kept_w),
                "read_close_hit": sum(
                    v for v in (_num(r.get(f"{prefix}_read_close_hit")) for r in rows) if v
                ),
                "read_close_miss": sum(
                    v for v in (_num(r.get(f"{prefix}_read_close_miss")) for r in rows) if v
                ),
                "read_victim": sum(
                    v for v in (_num(r.get(f"{prefix}_read_close_victim")) for r in rows) if v
                ),
                "read_far_hit": sum(
                    v for v in (_num(r.get(f"{prefix}_read_far_hit")) for r in rows) if v
                ),
                "read_far_miss": sum(
                    v for v in (_num(r.get(f"{prefix}_read_far_miss")) for r in rows) if v
                ),
                "write_close_hit": sum(
                    v for v in (_num(r.get(f"{prefix}_write_close_hit")) for r in rows) if v
                ),
                "write_close_miss": sum(
                    v for v in (_num(r.get(f"{prefix}_write_close_miss")) for r in rows) if v
                ),
            }
    # iCache exists only in visualize_data.bin's per-block Cache table.
    icache = []
    l2_total = []
    for block in capture.viz_blocks.values():
        for table in block.get("table_detail") or []:
            if table.get("table_name") != "Cache":
                continue
            for row in table.get("row") or []:
                name = row.get("name")
                values = row.get("value") or []
                if name == "iCache Total" and len(values) >= 4:
                    rate = _num(values[3])
                    if rate is not None:
                        icache.append(rate)
                if name == "L2 Cache Total" and len(values) >= 4:
                    rate = _num(values[3])
                    if rate is not None:
                        l2_total.append(rate)
    # DCache is a separate cache level from L2 and lives only in the per-block
    # 'DCache' table. It was previously decoded but never surfaced, which is the
    # kind of silent omission that makes a report look complete while missing a
    # whole level of the memory hierarchy.
    dcache: Dict[str, Dict[str, float]] = {}
    for block in capture.viz_blocks.values():
        for table in block.get("table_detail") or []:
            if table.get("table_name") != "DCache":
                continue
            header = table.get("header_name") or []
            for row in table.get("row") or []:
                values = row.get("value") or []
                name = str(row.get("name", "?"))
                entry = dcache.setdefault(name, {})
                # header is ["", "requests", "throughput(GB/s)"] while value is
                # [requests, throughput]. The leading blank header names the row
                # label column, so header index i maps to value i-1.
                for index, column in enumerate(header):
                    if index == 0:
                        continue
                    value_index = index - 1
                    if value_index >= len(values):
                        continue
                    parsed = _num(values[value_index])
                    if parsed is not None:
                        entry.setdefault(column, []).append(parsed)
    if dcache:
        result["dcache"] = {
            name: {column: _mean(vals) for column, vals in cols.items()}
            for name, cols in dcache.items()
        }

    if icache:
        result["icache_hit_pct"] = _mean(icache)
        result["icache_miss_pct"] = 100.0 - _mean(icache)
    if l2_total:
        result["l2_hit_pct_from_tables"] = _mean(l2_total)
    return result


def conflict_stats(capture: Capture) -> Dict[str, Any]:
    """Bank conflicts and pipe wait ratios (why the pipeline stalls)."""
    rows = capture.per_core("ResourceConflictRatio")
    out: Dict[str, Any] = {"available": bool(rows)}
    if not rows:
        return out
    fields = {
        "vec_stu_cflt": "aiv_vec_stu_cflt_ratio",
        "vec_ldu_cflt": "aiv_vec_ldu_cflt_ratio",
        "vec_sfu_cflt": "aiv_vec_sfu_cflt_ratio",
        "vec_wait": "aiv_vec_wait_ratio",
        "vec_mte2_wait": "aiv_mte2_wait_ratio",
        "vec_mte3_wait": "aiv_mte3_wait_ratio",
        "cube_wait": "aic_cube_wait_ratio",
        "aic_mte1_wait": "aic_mte1_wait_ratio",
        "aic_mte2_wait": "aic_mte2_wait_ratio",
        "aic_mte3_wait": "aic_mte3_wait_ratio",
    }
    for key, column in fields.items():
        value = _mean([_num(r.get(column)) for r in rows])
        if value is not None:
            out[key] = 100.0 * value
    # "wait cycles" per pipe, published only in the visualize tables.
    pipe_wait: Dict[str, float] = {}
    for block in capture.viz_blocks.values():
        for table in block.get("table_detail") or []:
            if table.get("table_name") != "Pipe":
                continue
            header = table.get("header_name") or []
            try:
                wait_idx = header.index("wait cycles")
            except ValueError:
                continue
            for row in table.get("row") or []:
                values = row.get("value") or []
                if len(values) <= wait_idx:
                    continue
                wait = _num(values[wait_idx])
                if wait is not None:
                    pipe_wait[row.get("name", "?")] = pipe_wait.get(row.get("name", "?"), 0.0) + wait
    if pipe_wait:
        out["pipe_wait_cycles_total"] = pipe_wait
    return out


def memory_paths(capture: Capture) -> List[Dict[str, Any]]:
    """The eight memory paths from visualize_data.bin, named via known ids."""
    if not capture.viz_cores:
        return []
    # memory_path ids observed on Ascend950PR; unknown ids are surfaced as-is
    path_names = {
        0: "GM→服务器(远)",
        1: "GM→本卡",
        12: "GM→UB (MTE2)",
        13: "UB→GM (MTE3)",
        14: "UB→L2 (vector 读)",
        15: "L2→UB (vector 写)",
        48: "L0A/B 通路",
        52: "L0C 通路",
        54: "FixPipe 通路",
    }
    first = capture.viz_cores[0]
    out = []
    for unit in first.get("memory_unit") or []:
        path_id = unit.get("memory_path")
        try:
            path_id = int(path_id)
        except (TypeError, ValueError):
            path_id = None
        out.append({
            "path_id": path_id,
            "name": path_names.get(path_id, f"path {path_id}"),
            "bandwidth_gbps": _num(unit.get("bandwidth")),
            "requests": _num(unit.get("request")),
            "peak_pct": _num(unit.get("peak_ratio")),
        })
    return out


def imbalance(capture: Capture) -> Dict[str, Any]:
    """Per-core time spread -- the tiling-quality signal."""
    rows = capture.per_core("PipeUtilization")
    times = [_num(r.get("aiv_time(us)")) for r in rows]
    aic_times = [_num(r.get("aic_time(us)")) for r in rows]
    kept = [t for t in times if t is not None]
    aic_kept = [t for t in aic_times if t is not None]
    out: Dict[str, Any] = {}
    if kept:
        out["aiv"] = {
            "cores": len(kept),
            "min_us": min(kept),
            "max_us": max(kept),
            "mean_us": _mean(kept),
            "spread_pct": _spread(kept),
        }
    if aic_kept:
        out["aic"] = {
            "cores": len(aic_kept),
            "min_us": min(aic_kept),
            "max_us": max(aic_kept),
            "mean_us": _mean(aic_kept),
            "spread_pct": _spread(aic_kept),
        }
    return out


def arithmetic_stats(capture: Capture) -> Dict[str, Any]:
    rows = capture.per_core("ArithmeticUtilization")
    out: Dict[str, Any] = {}
    if not rows:
        return out
    for key, column in (
        ("cube_ratio", "aic_cube_ratio"),
        ("cube_fp_ratio", "aic_cube_fp_ratio"),
        ("cube_int_ratio", "aic_cube_int_ratio"),
        ("vec_ratio", "aiv_vec_ratio"),
        ("vec_vf_ratio", "aiv_vec_vf_ratio"),
        ("vec_sfu_ratio", "aiv_vec_sfu_ratio"),
        ("vec_simt_vf_ratio", "aiv_vec_simt_vf_ratio"),
    ):
        value = _mean([_num(r.get(column)) for r in rows])
        if value is not None:
            out[key] = 100.0 * value
    cube_instr = sum(
        v for v in (_num(r.get("aic_cube_total_instr_number")) for r in rows) if v
    )
    if cube_instr:
        out["cube_total_instr"] = cube_instr
    return out


def roofline_classify(capture: Capture, pipes: List[Dict[str, Any]],
                      arith: Dict[str, Any], bw: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Attribute the kernel to a roofline class from measured occupancy.

    Order matters: a pipe at >50% average occupancy is the limiter; with no
    pipe above 50% the kernel is latency/bubble bound. The vendor's own
    verdict string (when the Roofline metric set was collected) is reported
    alongside, never silently overriding our derivation.
    """
    verdict: Dict[str, Any] = {
        "vendor_verdict": capture.roofline or None,
        "dominant_pipe": None,
        "dominant_pct": None,
        "class": "unknown",
    }
    if pipes and pipes[0]["mean_ratio_pct"] > 0:
        top = pipes[0]
        verdict["dominant_pipe"] = top["pipe"]
        verdict["dominant_pct"] = top["mean_ratio_pct"]
        if top["mean_ratio_pct"] >= 50.0:
            if top["pipe"] == "cube":
                verdict["class"] = "cube bound"
            elif top["pipe"] == "vec":
                verdict["class"] = "vector bound"
            elif top["pipe"] in ("mte1", "mte2", "mte3", "aiv_mte2", "aiv_mte3"):
                verdict["class"] = "memory bound"
            else:
                verdict["class"] = "scalar bound"
        else:
            verdict["class"] = "latency bound (pipeline bubbles)"
    elif capture.tables.get("PipeUtilization"):
        # Per-core rows exist but not one pipe published a numeric ratio: the
        # collection produced no execution data (a too-large metric combination
        # can make replay collect nothing). This is an empty capture, not an
        # idle kernel, so say so instead of reporting a fake verdict.
        verdict["class"] = "no data (all pipe counters empty)"
    peak_pcts = [e.get("peak_pct") for e in bw if e.get("peak_pct") is not None]
    if peak_pcts:
        verdict["max_bandwidth_peak_pct"] = max(peak_pcts)
    if arith.get("cube_ratio") and arith["cube_ratio"] > 0 and verdict["class"] == "unknown":
        verdict["class"] = "cube bound"
    return verdict


def analyze(capture: Capture) -> Dict[str, Any]:
    pipes = pipe_breakdown(capture)
    bw = bandwidth_breakdown(capture)
    arith = arithmetic_stats(capture)
    result = {
        "tool_version": VERSION,
        "capture_root": str(capture.root),
        "kernel": {
            "name": capture.op_name,
            "type": capture.op_type,
            "duration_us": capture.duration_us,
            "block_dim": capture.block_dim,
            "mix_block_dim": capture.mix_block_dim,
            "device_id": capture.device_id,
            "pid": capture.pid,
            "current_freq_mhz": capture.cur_freq,
            "rated_freq_mhz": capture.rated_freq,
            "soc": capture.soc,
        },
        "platform": {
            "soc": capture.platform.soc if capture.platform else capture.soc,
            "cube_freq_mhz": capture.platform.cube_freq_mhz if capture.platform else None,
            "vec_freq_mhz": capture.platform.vec_freq_mhz if capture.platform else None,
            "ai_core_cnt": capture.platform.ai_core_cnt if capture.platform else None,
            "vector_core_cnt": capture.platform.vector_core_cnt if capture.platform else None,
            "cube_core_cnt": capture.platform.cube_core_cnt if capture.platform else None,
            "ub_size": capture.platform.ub_size if capture.platform else None,
            "l1_size": capture.platform.l1_size if capture.platform else None,
            "l0c_size": capture.platform.l0c_size if capture.platform else None,
            "l2_size": capture.platform.l2_size if capture.platform else None,
        },
        "peak_model_gbps": capture.peak_model,
        "peak_source": capture.peak_source,
        "platform_reference": capture.platform_reference,
        "pipes": pipes,
        "bandwidth": bw,
        "arithmetic": arith,
        "cache": cache_stats(capture),
        "conflicts": conflict_stats(capture),
        "memory_paths": memory_paths(capture),
        "imbalance": imbalance(capture),
        "advice": list(dict.fromkeys(capture.advice)),
        "block_advice": capture.block_advice,
        "roofline": roofline_classify(capture, pipes, arith, bw),
    }
    result["per_core"] = build_per_core(capture)
    result["timeline"] = summarize_timeline(capture)
    result["subblocks"] = summarize_subblocks(capture)
    result["op_detail"] = summarize_op_detail(capture)

    # Enrichment: theoretical-peak metrics and instruction-level attribution.
    if KM is not None:
        peaks = KM.platform_peaks(capture.platform, capture.soc)
        # platform_config may be unavailable (analysis run off-device, or an
        # SoC whose ini is missing). Fall back to what the capture itself tells
        # us -- the measured clock and the block dim -- and record that the
        # peak is then an underestimate of the whole chip.
        fallback = False
        # Block Dim is the number of cores THIS kernel launched, not the chip's
        # core count. Using it as the chip peak is only sound when the kernel
        # saturates the device; otherwise it understates the peak and inflates
        # MFU. We therefore apply it per core type: a vector kernel's block dim
        # says nothing about how many cube cores exist, so cube stays unknown.
        if not peaks.vector_core_cnt and capture.block_dim and \
                (capture.op_type or "").lower() != "cube":
            peaks.vector_core_cnt = capture.block_dim
            fallback = True
        if not peaks.ai_core_cnt and capture.block_dim and \
                (capture.op_type or "").lower() == "cube":
            peaks.ai_core_cnt = capture.block_dim
            fallback = True
        if not peaks.cube_freq_mhz and capture.cur_freq:
            peaks.cube_freq_mhz = capture.cur_freq
            fallback = True
        if not peaks.vec_freq_mhz and capture.cur_freq:
            peaks.vec_freq_mhz = capture.cur_freq
            fallback = True
        result["platform_peaks"] = peaks.as_dict()
        result["platform_peaks"]["fallback_from_capture"] = fallback
        result["key_metrics"] = KM.compute(result, peaks).as_dict()
    if IP is not None:
        insight = IP.analyze(capture.root, kernel_us=capture.duration_us)
        result["instruction_level"] = insight.as_dict()
    if MC is not None:
        result["confidence"] = {
            m.key: MC.confidence_of(m.key) for m in MC.build_catalogue()
        }
    if LY is not None:
        blocks = []
        if capture.timeline:
            blocks = LY.blocks_from_trace(capture.timeline, capture.duration_us)
        schedule = LY.analyse_schedule(blocks, capture.duration_us)
        result["schedule"] = schedule.as_dict()
        die_num = capture.platform.io_die_num if capture.platform else None
        result["soc_paths"] = LY.analyse_paths(
            capture.viz_cores, die_num).as_dict()
        result["pc_cycle"] = LY.analyse_pc_cycle(
            capture.timeline, capture.duration_us).as_dict()
        result["layers"] = {
            m.key: LY.layer_of(m.key)
            for m in (MC.build_catalogue() if MC is not None else [])
        }
    return result


def build_per_core(capture: Capture) -> List[Dict[str, Any]]:
    """One row per core: time, dominant pipe, bandwidth, cache."""
    rows = capture.per_core("PipeUtilization")
    ub_rows = {str(r.get("block_id")): r for r in capture.per_core("MemoryUB")}
    mem_rows = {str(r.get("block_id")): r for r in capture.per_core("Memory")}
    l2_rows = {str(r.get("block_id")): r for r in capture.per_core("L2Cache")}
    out: List[Dict[str, Any]] = []
    for row in rows:
        block_id = str(row.get("block_id", ""))
        numbers = row.get("__num__") or {}
        time_us = numbers.get("aiv_time(us)") or numbers.get("aic_time(us)")
        pipes = {}
        for prefix, (key, _label) in PIPE_INFO.items():
            ratio = numbers.get(f"{prefix}_ratio")
            if ratio is not None:
                pipes[key] = 100.0 * ratio
        dominant = max(pipes.items(), key=lambda kv: kv[1]) if pipes else (None, None)
        entry: Dict[str, Any] = {
            "block_id": block_id,
            "sub_block": row.get("sub_block_id", ""),
            "core_type": core_type_of(row),
            "time_us": time_us,
            "cycles": numbers.get("aiv_total_cycles") or numbers.get("aic_total_cycles"),
            "pipes_pct": pipes,
            "dominant_pipe": dominant[0],
            "dominant_pct": dominant[1],
            "advice": capture.block_advice.get(block_id, []),
        }
        mem = mem_rows.get(block_id)
        if mem:
            mem_num = mem.get("__num__") or {}
            entry["gm_to_ub_gbps"] = mem_num.get("aiv_gm_to_ub_bw(GB/s)")
            entry["ub_to_gm_gbps"] = mem_num.get("aiv_ub_to_gm_bw(GB/s)")
            entry["gm_read_kb"] = mem_num.get("read_main_memory_datas(KB)")
            entry["gm_write_kb"] = mem_num.get("write_main_memory_datas(KB)")
        ub = ub_rows.get(block_id)
        if ub:
            ub_num = ub.get("__num__") or {}
            entry["ub_read_vector_gbps"] = ub_num.get("aiv_ub_read_bw_vector(GB/s)")
            entry["ub_write_vector_gbps"] = ub_num.get("aiv_ub_write_bw_vector(GB/s)")
        l2 = l2_rows.get(block_id)
        if l2:
            l2_num = l2.get("__num__") or {}
            entry["l2_read_hit_pct"] = l2_num.get("aiv_read_hit_rate(%)")
            entry["l2_write_hit_pct"] = l2_num.get("aiv_write_hit_rate(%)")
            entry["l2_read_miss"] = l2_num.get("aiv_read_close_miss")
        viz_core = None
        for core in capture.viz_cores:
            if str(core.get("core_no")) == block_id:
                viz_core = core
                break
        if viz_core:
            l2cache = viz_core.get("L2cache") or {}
            entry["l2_hit_pct_viz"] = _num(l2cache.get("hit_ratio"))
            entry["l2_total_requests"] = _num(l2cache.get("total_request"))
            vec = viz_core.get("Vector") or {}
            entry["vector_cycles"] = vec.get("cycle")
            entry["vector_total_cycles"] = vec.get("total_cycles")
        out.append(entry)
    return out


def summarize_subblocks(capture: Capture) -> List[Dict[str, Any]]:
    """Per-sub-block activity from the 0x06/0x07 records.

    A "sub block" is a sub-core (vector0/vector1 on an AIV, cube0 on an AIC).
    The records publish the same metric name with different `unit` values, and
    only the cycle-unit entries carry an origin_value, so we normalise to:
    cycles (raw counter) plus the value/unit as reported.
    """
    entries = capture.viz.get("subblock") or []
    grouped: Dict[Tuple[str, str, Any], List[Dict[str, Any]]] = {}
    for entry in entries:
        key = (str(entry.get("name")), str(entry.get("block_type")),
               entry.get("unit"))
        grouped.setdefault(key, []).append(entry)
    out: List[Dict[str, Any]] = []
    for (name, block_type, unit), group in grouped.items():
        values = [_num(e.get("value")) for e in group]
        kept = [v for v in values if v is not None]
        origins = [_num(e.get("origin_value")) for e in group]
        core_ids = [str(e.get("block_id")) for e in group]
        out.append({
            "name": name,
            "block_type": block_type,
            "unit": unit,
            "cores": len(group),
            "mean_value": _mean(kept),
            "max_value": max(kept) if kept else None,
            "origin_value": next((o for o in origins if o is not None), None),
            "core_ids": core_ids,
        })
    return out


def summarize_op_detail(capture: Capture) -> Optional[Dict[str, Any]]:
    """Per-core throughput/L2 detail from the 0x0C record (Occupancy metric)."""
    if not capture.op_detail:
        return None
    rows: List[Dict[str, Any]] = []
    for core in capture.op_detail:
        core_id = core.get("core_id")
        for sub in core.get("core_detail") or []:
            rows.append({
                "core_id": core_id,
                "subcore_id": sub.get("subcore_id"),
                "subcore_type": sub.get("subcore_type"),
                "cycles": _num(sub.get("cycles")),
                "throughput": _num(sub.get("throughput")),
                "l2_hit_rate_pct": _num(sub.get("L2cache_hit_rate")),
            })
    if not rows:
        return None
    busy = [r for r in rows if r["cycles"]]
    return {
        "rows": rows,
        "cores": len({r["core_id"] for r in rows}),
        "cores_with_activity": len({r["core_id"] for r in busy}),
        "max_cycles": max((r["cycles"] for r in busy), default=None),
    }


def summarize_timeline(capture: Capture) -> Optional[Dict[str, Any]]:
    """Pipe-busy totals from trace.json when PipeTimeline/InstrTimeline ran."""
    if not capture.timeline:
        return None
    events = capture.timeline.get("traceEvents") or []
    if not events:
        return None
    per_core: Dict[str, Dict[str, float]] = {}
    for event in events:
        if event.get("ph") != "X":
            continue
        pid = str(event.get("pid", "?"))
        tid = str(event.get("tid", "?"))
        duration = _num(event.get("dur"))
        if duration is None:
            continue
        per_core.setdefault(pid, {})
        per_core[pid][tid] = per_core[pid].get(tid, 0.0) + duration
    return {
        "source": str(capture.trace_path),
        "profiling_type": capture.timeline.get("profilingType"),
        "event_count": len(events),
        "per_core_pipe_us": {
            core: {pipe: round(value, 4) for pipe, value in pipes.items()}
            for core, pipes in per_core.items()
        },
    }


# ---------------------------------------------------------------------------
# report rendering
# ---------------------------------------------------------------------------

def render_markdown(result: Dict[str, Any]) -> str:
    kernel = result["kernel"]
    lines: List[str] = []
    add = lines.append

    add(f"# Kernel 性能报告 — {kernel['name'] or '?'}")
    add("")
    add(f"- SoC: `{kernel['soc'] or '?'}`  |  Capture: `{result['capture_root']}`")
    add(f"- 工具版本: {result['tool_version']}")
    add("")

    add("## 1. 结论")
    add("")
    add(f"- 耗时 **{_fmt(kernel['duration_us'], ' us', 3)}**，Block Dim **{kernel['block_dim'] or '?'}**，"
        f"频率 {_fmt(kernel['current_freq_mhz'], ' MHz', 0)}"
        f"（额定 {_fmt(kernel['rated_freq_mhz'], ' MHz', 0)}）")
    roofline = result["roofline"]
    if str(roofline["class"]).startswith("no data"):
        add("")
        add("> **采集无有效数据**：所有核的全部流水计数为空或 0。这表示本次采集没有真正"
            "执行到 kernel（例如 metric 组合触发 replay 失败），不代表 kernel 空闲。"
            "请减少 `--metrics` 的组数或改用 `--replay-mode=application` 重采。")
        add("")
    add(f"- Roofline 归属：**{roofline['class']}**"
        + (f"（主导流水 {roofline['dominant_pipe']} {_fmt(roofline['dominant_pct'], '%')}）"
           if roofline.get("dominant_pipe") else ""))
    if roofline.get("vendor_verdict"):
        add(f"- 厂商判定：`{roofline['vendor_verdict']}`")
    if roofline.get("max_bandwidth_peak_pct") is not None:
        add(f"- 最高带宽峰值占比：**{roofline['max_bandwidth_peak_pct']:.1f}%**")
    add("")

    if result["advice"]:
        add("厂商建议：")
        add("")
        for text in result["advice"]:
            add(f"- {text}")
        add("")

    key = result.get("key_metrics")
    if key and KM is not None:
        peaks = KM.PlatformPeaks(soc=(result.get("platform") or {}).get("soc") or "")
        pk = result.get("platform_peaks") or {}
        peaks.ai_core_cnt = pk.get("ai_core_cnt")
        peaks.vector_core_cnt = pk.get("vector_core_cnt")
        peaks.cube_freq_mhz = pk.get("cube_freq_mhz")
        peaks.vec_freq_mhz = pk.get("vec_freq_mhz")
        metrics = KM.KeyMetrics(
            mfu_pct=key.get("mfu_pct"), mfu_note=key.get("mfu_note") or "",
            mac_usage_pct=key.get("mac_usage_pct"), mac_note=key.get("mac_note") or "",
            achieved_tflops=key.get("achieved_tflops"),
            achieved_tmacs=key.get("achieved_tmacs"),
            arithmetic_intensity=key.get("arithmetic_intensity"),
            intensity_note=key.get("intensity_note") or "",
            bound_by=key.get("bound_by") or "", bound_note=key.get("bound_note") or "",
        )
        lines.extend(KM.render(metrics, peaks))
        lines.append("")

    add("## 3. 流水占用")
    add("")
    add("| 流水 | 平均占用 | 活跃核数 |")
    add("|---|---:|---:|")
    for pipe in result["pipes"]:
        add(f"| {pipe['label']} | {_fmt(pipe['mean_ratio_pct'], '%')} | {pipe['cores_active']} |")
    add("")
    add("> 占用率 = 该流水忙时间 / 该核总时间，跨核取平均。>50% 的流水即瓶颈候选。")
    add("")

    add("## 4. 带宽与峰值占比")
    add("")
    peak_model = result["peak_model_gbps"]
    if peak_model:
        add("峰值分母（由本次采集的 `*_bw_usage_rate(%)` 反解，非 datasheet 值）：")
        add("")
        for name, value in sorted(peak_model.items()):
            add(f"- `{name}`: {_fmt(value, ' GB/s')} — {result['peak_source'].get(name, '?')}")
        add("")
    reference = {k: v for k, v in (result.get("platform_reference") or {}).items() if v is not None}
    if reference:
        add("platform_config 参考值（仅供交叉校验，**未**用作峰值分母）：")
        add("")
        for name, value in sorted(reference.items()):
            add(f"- `{name}`: {value}")
        add("")
    add("| 通道 | 平均 GB/s | 峰值占比 |")
    add("|---|---:|---:|")
    for entry in result["bandwidth"]:
        if entry.get("mean_gbps") is None and entry.get("peak_pct") is None:
            continue
        add(f"| {entry['label']} | {_fmt(entry.get('mean_gbps'))} | "
            f"{_fmt(entry.get('peak_pct'), '%')} |")
    add("")

    cache = result["cache"]
    if cache.get("aiv") or cache.get("aic") or cache.get("icache_hit_pct") is not None:
        add("## 5. Cache 行为")
        add("")
        add("| 指标 | 值 |")
        add("|---|---:|")
        for prefix, label in (("aiv", "AIV"), ("aic", "AIC")):
            block = cache.get(prefix)
            if not block:
                continue
            add(f"| {label} L2 读命中率 | {_fmt(block.get('read_hit_pct'), '%')} |")
            add(f"| {label} L2 写命中率 | {_fmt(block.get('write_hit_pct'), '%')} |")
            add(f"| {label} L2 读 miss 次数 | {int(block.get('read_close_miss') or 0)} |")
        if cache.get("icache_hit_pct") is not None:
            add(f"| iCache 命中率 | {_fmt(cache['icache_hit_pct'], '%')} |")
            add(f"| iCache miss 率 | {_fmt(cache['icache_miss_pct'], '%')} |")
        if cache.get("l2_hit_pct_from_tables") is not None:
            add(f"| L2 总命中率（逐核表） | {_fmt(cache['l2_hit_pct_from_tables'], '%')} |")
        dcache = cache.get("dcache") or {}
        for row_name, columns in dcache.items():
            requests = columns.get("requests")
            throughput = columns.get("throughput(GB/s)")
            add(f"| {row_name} | {int(requests) if requests is not None else '-'} 请求"
                + (f", {_fmt(throughput)} GB/s" if throughput is not None else "") + " |")
        add("")
        if not dcache:
            add("> DCache 明细不可用（本次采集的 metric 组未产出该表）。"
                "Outstanding/inflight 请求数在本芯片任何 metric 组下均无输出通路。")
            add("")

    conflicts = result["conflicts"]
    if conflicts.get("available"):
        add("## 6. 冲突与等待")
        add("")
        add("| 指标 | 平均占比 |")
        add("|---|---:|")
        labels = {
            "vec_stu_cflt": "store bank 冲突",
            "vec_ldu_cflt": "load bank 冲突",
            "vec_sfu_cflt": "SFU 冲突",
            "vec_wait": "Vector 等待",
            "vec_mte2_wait": "MTE2 等待",
            "vec_mte3_wait": "MTE3 等待",
            "cube_wait": "Cube 等待",
            "aic_mte1_wait": "MTE1 等待",
            "aic_mte2_wait": "AIC MTE2 等待",
            "aic_mte3_wait": "AIC MTE3 等待",
        }
        for key, label in labels.items():
            if key in conflicts:
                add(f"| {label} | {_fmt(conflicts[key], '%')} |")
        pipe_wait = conflicts.get("pipe_wait_cycles_total") or {}
        if pipe_wait:
            add("")
            add("各流水累计 wait cycles（全部核求和）：")
            add("")
            for name, cycles in sorted(pipe_wait.items(), key=lambda kv: -kv[1]):
                add(f"- {name}: {int(cycles)} cycles")
        add("")

    schedule = result.get("schedule")
    if LY is not None and schedule is not None:
        add("## 7. block 时间轴（调度层）")
        add("")
        insight = LY.ScheduleInsight(
            available=schedule.get("available", False),
            reason=schedule.get("reason", ""),
            blocks=schedule.get("blocks", 0),
            source=schedule.get("source", ""),
            first_start_us=schedule.get("first_start_us"),
            last_start_us=schedule.get("last_start_us"),
            last_end_us=schedule.get("last_end_us"),
            launch_skew_us=schedule.get("launch_skew_us"),
            launch_skew_pct=schedule.get("launch_skew_pct"),
            tail_us=schedule.get("tail_us"),
            tail_pct=schedule.get("tail_pct"),
            min_duration_us=schedule.get("min_duration_us"),
            max_duration_us=schedule.get("max_duration_us"),
            duration_spread_pct=schedule.get("duration_spread_pct"),
            slowest_blocks=schedule.get("slowest_blocks") or [],
            latest_blocks=schedule.get("latest_blocks") or [],
        )
        lines.extend(LY.render_schedule(insight))

    imbalance_data = result["imbalance"]
    if imbalance_data:
        add("## 8. 核间负载")
        add("")
        for core_type, block in imbalance_data.items():
            add(f"- {core_type.upper()}: {block['cores']} 核，"
                f"耗时 {block['min_us']:.3f}–{block['max_us']:.3f} us，"
                f"均值 {block['mean_us']:.3f} us，**离散度 {_fmt(block['spread_pct'], '%')}**")
        add("")

    soc_paths = result.get("soc_paths")
    if LY is not None and soc_paths and soc_paths.get("available"):
        add("## 9. 内存通路与跨 DIE 流量")
        add("")
        sp = LY.SocPaths(
            available=soc_paths.get("available", False),
            reason=soc_paths.get("reason", ""),
            die_count=soc_paths.get("die_count"),
            paths=soc_paths.get("paths") or [],
            cross_die_gbps=soc_paths.get("cross_die_gbps", 0.0),
            on_die_gbps=soc_paths.get("on_die_gbps", 0.0),
            cross_die_pct=soc_paths.get("cross_die_pct"),
            cross_die_requests=soc_paths.get("cross_die_requests", 0),
        )
        lines.extend(LY.render_paths(sp))

    paths = result["memory_paths"]
    if paths:
        add("### 9.1 逐通路明细（旧视图）")
        add("")
        add("| 通路 | 带宽 GB/s | 请求数 |")
        add("|---|---:|---:|")
        for entry in paths:
            add(f"| {entry['name']} | {_fmt(entry.get('bandwidth_gbps'))} | "
                f"{int(entry.get('requests') or 0)} |")
        add("")

    confidence = result.get("confidence") or {}
    if MC is not None and confidence:
        add("## 10. 置信度分级")
        add("")
        add("每个指标的置信度按「它是怎么来的」划分，并用 5 次重复采集的变异系数校准。"
            "引用数字前先看这一栏。")
        add("")
        add("| 级别 | 含义 | 本报告条目数 |")
        add("|---|---|---:|")
        catalogue = MC.build_catalogue()
        for tier in MC.TIER_ORDER:
            count = sum(1 for m in catalogue if MC.confidence_of(m.key) == tier)
            if count:
                add(f"| {tier} | {MC.TIER_DESCRIPTION[tier]} | {count} |")
        add("")
        unstable = [m for m in catalogue if MC.confidence_of(m.key) == "E"]
        if unstable:
            add("**本报告中 E 级（不建议单独引用）的指标**：")
            add("")
            for m in unstable:
                measured = MC.stability_for(m.key)
                detail = (f"；实测 CV {measured['cv_pct']:.1f}%"
                          if measured else "")
                add(f"- {m.label} — {m.caveat or m.note}{detail}")
            add("")
        add("完整目录（含每个指标的产出口径）：`python3 scripts/metric_catalogue.py`")
        add("")

    add("## 11. 逐核明细")
    add("")
    add("| core | 类型 | 时间 us | 主导流水 | 占比 | GM→UB GB/s | L2 读命中 |")
    add("|---:|---|---:|---|---:|---:|---:|")
    for core in result["per_core"]:
        add(f"| {core['block_id']} | {core['core_type']} | "
            f"{_fmt(core.get('time_us'), '', 4)} | {core.get('dominant_pipe') or '-'} | "
            f"{_fmt(core.get('dominant_pct'), '%')} | "
            f"{_fmt(core.get('gm_to_ub_gbps'))} | "
            f"{_fmt(core.get('l2_read_hit_pct'), '%')} |")
    add("")

    instruction = result.get("instruction_level")
    if IP is not None and instruction is not None:
        insight = IP.InstrInsight(
            available=instruction.get("available", False),
            reason=instruction.get("reason", ""),
            base_pc=None,
            regions=instruction.get("regions", 0),
            events=instruction.get("events", 0),
            covered_us=instruction.get("covered_us", 0.0) or 0.0,
            kernel_us=instruction.get("kernel_us"),
            covered_pct=instruction.get("covered_pct"),
            by_opcode=instruction.get("by_opcode") or [],
            by_pipe=instruction.get("by_pipe") or [],
            hottest=instruction.get("hottest") or [],
            unmatched_events=instruction.get("unmatched_events", 0),
        )
        lines.extend(IP.render(insight))

        pc_cycle = result.get("pc_cycle")
        if LY is not None and pc_cycle is not None:
            add("### 12.1 PC-vs-cycle（icache miss 诊断）")
            add("")
            pcins = LY.PcCycleInsight(
                available=pc_cycle.get("available", False),
                reason=pc_cycle.get("reason", ""),
                events=pc_cycle.get("events", 0),
                distinct_pcs=pc_cycle.get("distinct_pcs", 0),
                span_us=pc_cycle.get("span_us"),
                stalls=pc_cycle.get("stalls") or [],
                total_stall_us=pc_cycle.get("total_stall_us", 0.0),
                stall_pct=pc_cycle.get("stall_pct"),
                threshold_us=pc_cycle.get("threshold_us", 0.0),
            )
            lines.extend(LY.render_pc_cycle(pcins))

    subblocks = result.get("subblocks") or []
    if subblocks:
        add("## 13. 子核活动")
        add("")
        add("| 指标 | 类型 | unit | 核数 | 均值 | 最大值 | 原始计数 |")
        add("|---|---|---:|---:|---:|---:|---:|")
        for entry in subblocks:
            add(f"| {entry['name']} | {entry['block_type']} | {entry['unit']} | "
                f"{entry['cores']} | {_fmt(entry['mean_value'])} | "
                f"{_fmt(entry['max_value'])} | "
                f"{'-' if entry['origin_value'] is None else int(entry['origin_value'])} |")
        add("")

    op_detail = result.get("op_detail")
    if op_detail:
        add("## 14. 逐核吞吐（Occupancy）")
        add("")
        add(f"- {op_detail['cores']} 个核，其中 {op_detail['cores_with_activity']} 个有活动；"
            f"最大 cycles {op_detail['max_cycles']}")
        add("")
        add("| core | subcore | 类型 | cycles | 吞吐 | L2 命中 |")
        add("|---:|---:|---|---:|---:|---:|")
        for row in op_detail["rows"]:
            add(f"| {row['core_id']} | {row['subcore_id']} | {row['subcore_type']} | "
                f"{'-' if row['cycles'] is None else int(row['cycles'])} | "
                f"{_fmt(row['throughput'])} | {_fmt(row['l2_hit_rate_pct'], '%')} |")
        add("")

    timeline = result.get("timeline")
    if timeline:
        add("## 15. 时间线事件")
        add("")
        add(f"- 事件数 {timeline['event_count']}，来源 `{timeline['source']}`")
        add("")
        add("| core | 流水 | 累计时间 us |")
        add("|---|---|---:|")
        for core, pipes in timeline["per_core_pipe_us"].items():
            for pipe, value in sorted(pipes.items(), key=lambda kv: -kv[1]):
                add(f"| {core} | {pipe} | {value:.4f} |")
        add("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# capture runner
# ---------------------------------------------------------------------------

def check_device_idle(home: Path) -> Tuple[bool, str]:
    """Warn when another process is on the NPU: counters get noisy."""
    try:
        out = subprocess.run(
            ["npu-smi", "info"], capture_output=True, timeout=60, check=False
        ).stdout.decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return True, "npu-smi 不可用，未检查设备占用"
    processes = [line for line in out.splitlines() if re.search(r"\|\s*\d+\s*\|\s*\d+\s*\|", line)]
    # npu-smi prints a process row as "| 0 | <pid> | <name> | <mem> |"
    busy = [line for line in out.splitlines() if re.match(r"\|\s*\d+\s*\|\s*\d+\s*\|", line.strip())]
    if busy:
        return False, f"设备上有 {len(busy)} 个其他进程，采集数据可能抖动"
    return True, "设备空闲"


def run_msopprof(home: Path, app: List[str], out_dir: Path,
                 metrics: List[str], warmup: int, launch_count: int,
                 kernel: Optional[str], replay_mode: str,
                 timeout: int,
                 instr_pipes: Optional[str] = None) -> Tuple[int, str, Optional[Path]]:
    """Run one msopprof collection. Returns (rc, log, capture_dir)."""
    env = cann_env(home)
    binary = home / "tools" / "msopprof" / "bin" / "msopprof"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary),
        f"--warm-up={warmup}",
        f"--launch-count={launch_count}",
        f"--replay-mode={replay_mode}",
        f"--aic-metrics={','.join(metrics)}",
        f"--output={out_dir}",
    ]
    if "InstrTimeline" in metrics and instr_pipes:
        cmd.append(f"--instr-timeline-pipe={instr_pipes}")
    if kernel:
        # msopprof matches --kernel-name as a GLOB against the mangled kernel
        # name, not as a substring: passing "add_kernel" filters everything out
        # because the real name is "_Z10add_kernelIfEvPhS0_S0_llj". We always
        # wrap the user's pattern in '*' so a bare name behaves as a substring
        # match, which is what callers expect.
        pattern = kernel if any(ch in kernel for ch in "*?[") else f"*{kernel}*"
        cmd.append(f"--kernel-name={pattern}")
    cmd.extend(app)
    started = time.time()
    try:
        completed = subprocess.run(
            cmd, capture_output=True, timeout=timeout, env=env, check=False
        )
        log = (completed.stdout + completed.stderr).decode(errors="replace")
        rc = completed.returncode
    except subprocess.TimeoutExpired as exc:
        log = (exc.stdout or b"").decode(errors="replace") + "\n[TIMEOUT]"
        rc = 124
    elapsed = time.time() - started
    log += f"\n[harness] msopprof rc={rc} elapsed={elapsed:.1f}s\n"
    captures = sorted(
        (p for p in out_dir.glob("OPPROF_*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    newest = captures[-1] if captures else None
    if newest is not None:
        preserve_probe_artifacts(newest)
    if newest is not None and (newest / "OpBasicInfo.csv").is_file():
        return rc, log, newest
    return rc, log, None


def preserve_probe_artifacts(capture_root: Path) -> None:
    """Copy instrumentation files that msopprof deletes during its own parse.

    ``dfx_region_map.txt`` (pc -> pipe/opcode) and ``pc_start_addr.txt`` (the
    absolute base) are written during collection but removed by a later parsing
    pass, so by the time a user inspects the capture they are often gone and
    instruction-level attribution becomes impossible. We copy them into a
    stable ``probe/`` directory while they still exist.

    This is a workaround for the profiler's cleanup, not a feature of the
    format: if the files survive, the copy is skipped.
    """
    try:
        dumps = [capture_root / "dump"]
        dumps.extend(sorted(capture_root.glob("device*/*/*/dump")))
        for dump in dumps:
            if not dump.is_dir():
                continue
            wanted = [dump / "dfx_region_map.txt", dump / "pc_start_addr.txt",
                      dump / "op_basic_info.txt"]
            existing = [f for f in wanted if f.is_file()]
            if not existing:
                continue
            probe = capture_root / "probe"
            probe.mkdir(exist_ok=True)
            for source in existing:
                target = probe / source.name
                if not target.is_file():
                    target.write_bytes(source.read_bytes())
    except OSError:
        # Archival is best-effort: losing it degrades the instruction-level
        # section to "unavailable", which the report states plainly.
        pass


def retry_without_filter(home: Path, app: List[str], out_dir: Path,
                         metrics: List[str], args: argparse.Namespace
                         ) -> Tuple[str, Optional[Path]]:
    """Recollect with no kernel filter, for kernel-name discovery."""
    retry_dir = out_dir.parent / "discovery"
    rc, log, root = run_msopprof(
        home=home, app=app, out_dir=retry_dir, metrics=metrics,
        warmup=args.warmup, launch_count=1, kernel=None,
        replay_mode=args.replay_mode, timeout=args.timeout,
    )
    return log, root


def discover_kernels(capture_root: Path) -> List[str]:
    """Kernel names present in a capture, from the per-kernel subdirectories."""
    names: List[str] = []
    for child in sorted(capture_root.iterdir()):
        if child.is_dir() and child.name != "dump":
            names.append(child.name)
    if not names:
        basic = capture_root / "OpBasicInfo.csv"
        if basic.is_file():
            for row in csv_rows(basic):
                name = (row.get("Op Name") or "").strip()
                if name:
                    names.append(name)
    return names


# Metric groups that cannot share one collection pass.
EXCLUSIVE_SETS = [["InstrTimeline"], ["PipeTimeline"], ["Source"], ["PCSampling"]]


def plan_passes(metrics: List[str]) -> List[List[str]]:
    """Split a requested metric list into passes that can coexist.

    Anything in an exclusive set gets its own pass; everything else travels
    together. Order is preserved so the caller can rely on the core pass
    running first (it is the one whose CSV drives the report).
    """
    exclusive = {m for group in EXCLUSIVE_SETS for m in group}
    core = [m for m in metrics if m not in exclusive]
    passes: List[List[str]] = []
    if core:
        passes.append(core)
    for group in EXCLUSIVE_SETS:
        wanted = [m for m in group if m in metrics]
        if wanted:
            passes.append(wanted)
    return passes or [metrics]


def run_multipass(home: Path, app: List[str], capture_dir: Path,
                  passes: List[List[str]], args: argparse.Namespace
                  ) -> Tuple[Optional[Path], str]:
    """Run one collection per pass and merge into a single readable capture.

    msopprof writes one OPPROF_* per invocation. We collect each pass into its
    own directory, then build a merged view in ``capture_dir`` that points at
    the *core* pass for the CSVs (that is what the analyzer reads) while making
    the instruction/timeline artefacts of the other passes discoverable under
    ``capture/extra/<pass>/``.
    """
    logs: List[str] = []
    core_root: Optional[Path] = None
    for index, metrics in enumerate(passes):
        label = "core" if index == 0 and metrics == passes[0] and "PipeUtilization" in metrics else metrics[0].lower()
        target = capture_dir / f"pass{index}_{label}"
        print(f"[info]   第 {index + 1} 趟: {metrics}", file=sys.stderr)
        rc, log, root = run_msopprof(
            home=home, app=app, out_dir=target, metrics=metrics,
            warmup=args.warmup, launch_count=args.launch_count,
            kernel=args.kernel, replay_mode=args.replay_mode,
            timeout=args.timeout,
            instr_pipes=getattr(args, "instr_pipes", None),
        )
        logs.append(f"=== pass{index} {metrics} rc={rc} ===\n{log}")
        if "PipeUtilization" in metrics and root is not None and core_root is None:
            core_root = root
        if root is None:
            continue
        preserve_probe_artifacts(root)

    if core_root is None:
        # No core pass succeeded; fall back to any pass so the caller still
        # gets whatever was collected rather than a bare failure.
        candidates = sorted(capture_dir.glob("pass*/OPPROF_*"))
        core_root = candidates[0] if candidates else None

    if core_root is not None:
        # Expose the core pass where the analyzer expects it, and park the rest
        # beside it so instruction-level analysis can still find them.
        link = capture_dir / "OPPROF_merged"
        try:
            if link.exists() or link.is_symlink():
                if link.is_symlink() or link.is_file():
                    link.unlink()
                else:
                    shutil.rmtree(link)
            link.symlink_to(core_root.resolve())
        except OSError:
            pass
        extra = capture_dir / "extra"
        extra.mkdir(exist_ok=True)
        for child in sorted(capture_dir.glob("pass*")):
            if child == core_root.parent:
                continue
            marker = extra / child.name
            try:
                if not marker.exists():
                    marker.symlink_to(child.resolve())
            except OSError:
                continue
    return core_root, "\n".join(logs)


def command_profile(args: argparse.Namespace) -> int:
    home = find_cann_home()
    if home is None:
        print("错误: 找不到 CANN 安装（需要 set_env.sh 与 tools/msopprof/bin/msopprof）",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.analyze_only:
        ok, message = check_device_idle(home)
        if not ok:
            print(f"[warn] {message}", file=sys.stderr)

        app_cmd = [str(Path(args.app).resolve())] + list(args.app_args or [])
        binary = Path(args.app).resolve()
        if not binary.is_file():
            # allow "python3 script.py" style invocations
            app_cmd = list(args.app.split()) + list(args.app_args or [])
        capture_dir = out_dir / "capture"
        metrics = list(DEFAULT_METRICS)
        if args.with_timeline:
            metrics.append("BasicInfo")
        if args.metrics:
            metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
        if getattr(args, "with_instructions", False) and "InstrTimeline" not in metrics:
            metrics.append("InstrTimeline")

        # Split the requested metrics into the passes that can actually coexist.
        passes = plan_passes(metrics)
        rc, log, capture_root = 0, "", None
        if len(passes) <= 1:
            print(f"[info] 采集 {metrics} → {capture_dir}", file=sys.stderr)
            rc, log, capture_root = run_msopprof(
                home=home, app=app_cmd, out_dir=capture_dir, metrics=metrics,
                warmup=args.warmup, launch_count=args.launch_count,
                kernel=args.kernel, replay_mode=args.replay_mode,
                timeout=args.timeout,
            )
        else:
            print(f"[info] 分 {len(passes)} 趟采集（这些 metric 集合无法合并）→ {capture_dir}",
                  file=sys.stderr)
            capture_root, log = run_multipass(
                home, app_cmd, capture_dir, passes, args
            )
            rc = 0 if capture_root is not None else 1
        (out_dir / "collect.log").write_text(log, errors="replace")
        if capture_root is None and args.kernel and not args.no_kernel_retry:
            # The kernel filter matched nothing (wrong name, or the program did
            # not launch that kernel). Recollect without the filter so we can
            # tell the caller which kernels the app actually ran, instead of
            # just reporting "no data".
            print("[warn] 指定的 kernel 未匹配到，重新采集以列出实际运行的 kernel…",
                  file=sys.stderr)
            retry_log, retry_root = retry_without_filter(
                home, app_cmd, capture_dir, metrics, args
            )
            log += "\n[harness] retry without --kernel-name (discovery)\n" + retry_log
            (out_dir / "collect.log").write_text(log, errors="replace")
            if retry_root is not None:
                names = discover_kernels(retry_root)
                print("错误: 未找到匹配 %r 的 kernel。本次运行实际采集到的 kernel：" % args.kernel,
                      file=sys.stderr)
                for name in names:
                    print(f"  - {name}", file=sys.stderr)
                print(f"（数据保留在 {retry_root}）", file=sys.stderr)
                return 1
        if capture_root is None:
            print("错误: msopprof 未产出 OPPROF_* 采集目录。日志尾部：", file=sys.stderr)
            print("\n".join(log.splitlines()[-20:]), file=sys.stderr)
            return 1
        print(f"[info] 采集完成: {capture_root}", file=sys.stderr)

    capture_path = out_dir / "capture"
    if not any(capture_path.glob("OPPROF_*")):
        capture_path = out_dir
    capture = load_capture(capture_path, home)
    result = analyze(capture)

    metrics_path = out_dir / "metrics.json"
    metrics_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    report_path = out_dir / "report.md"
    report_path.write_text(render_markdown(result))

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(result))
    print(f"\n[info] 报告: {report_path}\n[info] 全量指标: {metrics_path}",
          file=sys.stderr)
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    home = find_cann_home()
    out_dir = Path(args.out).resolve()
    capture = load_capture(out_dir, home)
    result = analyze(capture)
    (out_dir / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    report = render_markdown(result)
    (out_dir / "report.md").write_text(report)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(report)
    return 0


def command_diff(args: argparse.Namespace) -> int:
    """Compare two capture directories field by field."""
    home = find_cann_home()
    a = analyze(load_capture(Path(args.left), home))
    b = analyze(load_capture(Path(args.right), home))

    lines = ["# Kernel capture 对比", ""]
    da = a["kernel"]["duration_us"]
    db = b["kernel"]["duration_us"]
    if da and db:
        lines.append(f"- 耗时: {da:.3f} us → {db:.3f} us "
                     f"({(db - da) / da * 100:+.1f}%)")
    lines.append(f"- Block Dim: {a['kernel']['block_dim']} → {b['kernel']['block_dim']}")
    lines.append("")
    lines.append("| 流水 | 左 % | 右 % | Δ |")
    lines.append("|---|---:|---:|---:|")
    # Iterate the UNION of both sides: a kernel that switches from AIV to AIC
    # (e.g. vector vs cube) has pipes on only one side, and iterating just the
    # left side would silently hide every right-only pipe.
    left_pipes = {p["pipe"]: p for p in a["pipes"]}
    right_pipes = {p["pipe"]: p for p in b["pipes"]}
    for key in sorted(set(left_pipes) | set(right_pipes)):
        left = left_pipes.get(key, {}).get("mean_ratio_pct")
        right = right_pipes.get(key, {}).get("mean_ratio_pct")
        label = (left_pipes.get(key) or right_pipes.get(key))["label"]
        delta = None if (left is None or right is None) else right - left
        lines.append(
            f"| {label} | {'n/a' if left is None else f'{left:.2f}'} | "
            f"{'n/a' if right is None else f'{right:.2f}'} | "
            f"{'n/a' if delta is None else f'{delta:+.2f}'} |"
        )
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kernel_perf.py",
        description=(
            "Ascend kernel hardware-level performance analyzer.\n"
            "Collects msopprof CSV + visualize_data.bin, decodes both, and "
            "converts every counter into absolute values plus % of the "
            "hardware peak recovered from the capture itself."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  kernel_perf.py profile --app ./vector_add --kernel add_kernel --out ./out\n"
            "  kernel_perf.py profile --app './bench 1024 1024 1024' --kernel foo --out ./out\n"
            "  kernel_perf.py analyze --out ./out --json\n"
            "  kernel_perf.py diff ./before ./after\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile", help="采集并分析")
    profile.add_argument("--app", required=True,
                         help="可执行文件路径，或带参数的完整命令行（用引号包起来）")
    profile.add_argument("--kernel", default=None,
                         help="kernel 名（子串匹配）；省略则采集第一个命中的 kernel")
    profile.add_argument("--out", default="./kernel_perf_out", help="输出目录")
    profile.add_argument("--warmup", type=int, default=3, help="预热次数（默认 3）")
    profile.add_argument("--launch-count", type=int, default=1, help="采集轮数（默认 1）")
    profile.add_argument("--replay-mode", default="kernel",
                         choices=["application", "kernel", "range"])
    profile.add_argument("--metrics", default=None,
                         help="逗号分隔的 msopprof metric 集合（覆盖默认 7 组）")
    profile.add_argument("--with-timeline", action="store_true",
                         help="额外采集 BasicInfo（pipeline 时间线需要）")
    profile.add_argument("--with-instructions", action="store_true",
                         help="额外分一趟采集 InstrTimeline，产出逐指令耗时"
                              "（慢：数十 MB~数百 MB 中间文件，通常 1-4 分钟）")
    profile.add_argument("--instr-pipes", default="vector|mte2|mte3",
                         help="InstrTimeline 的流水，取值限 "
                              "cube|fixp|vector|mte1|mte2|mte3，用 | 分隔"
                              "（默认 vector|mte2|mte3，适配 vector 算子）")
    profile.add_argument("--timeout", type=int, default=1800, help="单次采集超时秒数")
    profile.add_argument("--json", action="store_true", help="以 JSON 打印全量结果")
    profile.add_argument("--analyze-only", action="store_true",
                         help="跳过采集，直接解析 --out 下已有的 capture")
    profile.add_argument("--no-kernel-retry", action="store_true",
                         help="kernel 未匹配时不重采枚举（默认会重采一次）")
    profile.set_defaults(func=command_profile)

    analyze_cmd = sub.add_parser("analyze", help="重新解析已有采集")
    analyze_cmd.add_argument("--out", required=True)
    analyze_cmd.add_argument("--json", action="store_true")
    analyze_cmd.set_defaults(func=command_analyze)

    diff_cmd = sub.add_parser("diff", help="对比两个采集")
    diff_cmd.add_argument("left")
    diff_cmd.add_argument("right")
    diff_cmd.set_defaults(func=command_diff)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        args.app_args = extra
    else:
        args.app_args = []
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
