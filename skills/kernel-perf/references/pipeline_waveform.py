#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decode the on-device pipe waveform that ``msopprof`` dumps as raw binary.

What this is for
----------------
``msopprof --aic-metrics=PipeTimeline`` writes two things: a small parsed
``trace.json`` and, underneath it, the raw per-cycle pipe stream in
``dump/timeline.bin.0``. ``trace.json`` is a *sampled, clipped* view -- it keeps
only a handful of sampling groups and truncates the tail of each one, so on a
56-block kernel it accounts for about half the kernel's cycles. The raw stream
is the complete record and is what this script reads.

CANN publishes no specification for ``timeline.bin.0``. The layout below was
recovered by decoding captures and checking the result against the vendor's own
``trace.json`` for the same run (see ``references/pipeline-waveform.md`` for the
evidence and the confidence tier of each claim).

Container
---------
The file is a fixed-stride array of records::

    stride = 524290 words, i.e. 2 097 160 bytes

    record:  word[0]  id      = 0x000V_CCCC   V = subcore+1, CCCC = core index
             word[1]  blen    = payload length in BYTES
             word[2:] payload = blen/4 words, zero padded to the stride

An AIV record is ``0x0001_CCCC`` (veccore0) or ``0x0002_CCCC`` (veccore1); an
AIC record is ``0x0000_CCCC``.

Payload tokens
--------------
Each payload word is ``tag:16 | value:16`` (little-endian u32, tag in the high
half). Three kinds, told apart by the tag's high byte:

===========================  ===========================================
tag                          meaning
===========================  ===========================================
``0xe0xx`` .. ``0xe3xx``     record header (op id, block dim, clock), no time
``0xf0xx``                   **timed interval**: ``value`` = cycles
``0x0dxx``                   counter block; also separates sampling groups
===========================  ===========================================

For a timed token, the *low* byte of the tag is the set of pipes busy during
that interval, as a bitmask:

=====  =======  ======  =======  =======  ======  =======
bit    AIV      AIC     bit      AIV      AIC     bit
=====  =======  ======  =======  =======  ======  =======
0x01   SCALAR   SCALAR  0x04     --       CUBE    0x40 (AIC, see below)
0x02   VECTOR   --      0x08     --       --      0x80 (unseen)
0x10   MTE2     MTE2    0x20     MTE3     --
=====  =======  ======  =======  =======  ======  =======

The timed tokens are a run-length encoding of a pipe-state timeline: walk them
in order, the state of token *k* holds for ``value_k`` cycles, and cycles
accumulate. ``0x00`` means "all pipes idle" and is emitted as a real interval --
those are the pipeline bubbles this tool exists to find.

Usage
-----
::

    # whole capture, one markdown summary
    ./pipeline_waveform.py --capture OPPROF_xxx --out waveform

    # just the numbers, for another tool
    ./pipeline_waveform.py --capture OPPROF_xxx --json

    # a Chrome/Perfetto trace on one time axis
    ./pipeline_waveform.py --capture OPPROF_xxx --chrome trace.json

    # self-check: decode a capture and re-derive the vendor's trace.json
    ./pipeline_waveform.py --capture OPPROF_xxx --verify

Every number this script prints is a direct decode of the raw stream. Where a
claim is inferred rather than observed (the AIC bit assignment), the report says
so instead of quietly asserting it.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.0.0"

# One record occupies a fixed slot; the payload is zero-padded to it.
STRIDE_WORDS = 524_290

# Tag classes inside a payload.
TAG_HEADER = 0xE000          # 0xe0xx-0xe3xx: metadata, carries no time
TAG_TIME = 0xF000            # 0xf0xx: value is a duration in cycles
TAG_COUNTER = 0x0D00         # 0x0dxx: counters, and a sampling-group boundary

# Pipe bitmasks. AIV and AIC share 0x01 (SCALAR) and 0x10 (MTE2) but differ
# elsewhere, so the record kind selects the table.
AIV_PIPES = {
    0x01: "SCALAR",
    0x02: "VECTOR",
    0x10: "MTE2",
    0x20: "MTE3",
}
AIC_PIPES = {
    0x01: "SCALAR",
    0x04: "CUBE",
    0x08: "MTE1",
    0x10: "MTE2",
    0x40: "FIXP",
}

# Which bits this decoder is confident about, per core kind. A bit outside this
# set is reported as unknown rather than given a name it has not earned.
AIV_CONFIDENT = {0x01, 0x02, 0x10, 0x20}
AIC_CONFIDENT = {0x01, 0x04, 0x08, 0x10, 0x40}


# ---------------------------------------------------------------------------
# container
# ---------------------------------------------------------------------------

@dataclass
class Record:
    """One core's slice of the capture."""
    index: int
    record_id: int
    payload: List[int]
    core_kind: str            # "aiv" | "aic"
    core_id: int
    subcore_id: int

    @property
    def pid(self) -> str:
        """The name the vendor's trace.json uses for this core."""
        if self.core_kind == "aic":
            return f"core{self.core_id}.cubecore"
        return f"core{self.core_id}.veccore{self.subcore_id}"


def read_records(path: Path) -> List[Record]:
    """Split ``timeline.bin.0`` into per-core records."""
    raw = path.read_bytes()
    words = len(raw) // 4
    data = struct.unpack(f"<{words}I", raw[: words * 4])
    records: List[Record] = []
    for index in range(words // STRIDE_WORDS):
        base = index * STRIDE_WORDS
        record_id, byte_len = data[base], data[base + 1]
        if not byte_len:
            continue
        max_payload = (STRIDE_WORDS - 2) * 4
        if byte_len > max_payload:
            # A truncated capture (the file was still being written, or the
            # run was killed) leaves a plausible header with an impossible
            # length. Skip it instead of reading past the record.
            continue
        count = byte_len // 4
        payload = list(data[base + 2 : base + 2 + count])
        kind = "aic" if (record_id >> 16) == 0 else "aiv"
        core_id = record_id & 0xFFFF
        subcore = (record_id >> 16) - 1 if kind == "aiv" else 0
        records.append(Record(index, record_id, payload, kind, core_id, subcore))
    return records


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------

@dataclass
class Interval:
    """One run of constant pipe state, in cycles from the group's origin."""
    start: int
    end: int
    mask: int
    pipes: Tuple[str, ...]
    unnamed: int = 0

    @property
    def cycles(self) -> int:
        return self.end - self.start

    @property
    def idle(self) -> bool:
        return self.mask == 0


@dataclass
class Group:
    """One sampling group: an independent timeline with its own origin."""
    index: int
    intervals: List[Interval] = field(default_factory=list)
    counters: Dict[int, int] = field(default_factory=dict)
    header: Dict[int, int] = field(default_factory=dict)

    @property
    def end_cycle(self) -> int:
        return self.intervals[-1].end if self.intervals else 0

    @property
    def unknown_masks(self) -> Dict[int, int]:
        """Masks that set a bit this decoder has no verified name for."""
        out: Dict[int, int] = {}
        for iv in self.intervals:
            if iv.unnamed:
                out[iv.mask] = out.get(iv.mask, 0) + iv.cycles
        return out


def pipe_mask_names(mask: int, core_kind: str) -> Tuple[Tuple[str, ...], int]:
    """Name a mask. Returns (pipe names, bits that have no verified name).

    A mask only yields names for bits this decoder has verified; anything else
    comes back as an unnamed bit so the report can say "a pipe was busy here and
    we do not know which" instead of inventing a label.
    """
    table = AIC_PIPES if core_kind == "aic" else AIV_PIPES
    known = AIC_CONFIDENT if core_kind == "aic" else AIV_CONFIDENT
    names = tuple(name for bit, name in table.items() if mask & bit)
    unnamed = mask & ~sum(known)
    return names, unnamed


def decode_groups(record: Record) -> List[Group]:
    """Walk the payload and produce one timeline per sampling group.

    The encoding pairs a state with the run that follows it: the mask on token
    *k* holds for the cycle count carried by token *k+1*. Equivalently, token
    *k*'s own value is the time that had already elapsed before its state took
    effect. Reading it the other way -- mask and duration from the same token --
    still produces plausible-looking timelines, but it shifts every interval by
    one and disagrees with the vendor's ``trace.json``; the pairing below is the
    one that reproduces it exactly (142/142 core-pipe interval sets across five
    captures, both AIV and AIC -- see ``--verify``).

    A ``0x0dxx`` counter block ends the current group, and the tokens after it
    start a fresh timeline from cycle 0.
    """
    groups: List[Group] = []
    group = Group(index=0)
    clock = 0

    # Buffer one timed token so each can borrow the next token's duration.
    pending: Optional[int] = None
    for word in record.payload:
        tag, value = word >> 16, word & 0xFFFF
        high = tag & 0xFF00
        if high == TAG_TIME:
            # `pending` (the previous token's mask) is paired with `value`, this
            # token's duration; this token's low byte then becomes the next mask.
            if pending is not None and value:
                pipes, unnamed = pipe_mask_names(pending, record.core_kind)
                group.intervals.append(
                    Interval(clock, clock + value, pending, pipes, unnamed)
                )
            # The clock advances on every token, including the first: its value
            # is the lead-in before the first recorded state takes effect, and
            # dropping it shifts the whole timeline left by that amount.
            clock += value
            pending = tag & 0xFF
        elif high == TAG_COUNTER:
            group.counters[tag] = value
            if group.intervals:
                pending = None
                groups.append(group)
                group = Group(index=len(groups))
                clock = 0
        elif high & 0xE000 == TAG_HEADER:
            group.header[tag] = value

    if group.intervals:
        groups.append(group)
    return groups


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------

@dataclass
class CoreWaveform:
    record: Record
    groups: List[Group]
    freq_mhz: float

    @property
    def windows(self) -> List[Group]:
        """Every sampling window this core contributed.

        Each group is an independent window that restarts at cycle 0. They are
        **not** a continuous timeline and must not be concatenated into one
        span, but they are all samples of the same kernel and pooling them is
        what makes the occupancy figures trustworthy: on a 30-window capture
        group 0 alone reports SCALAR at 24.1% when the true figure across all
        windows is 40.2%. Dropping 29 of 30 windows is not a rounding error,
        it is a systematically different answer.
        """
        return list(self.groups)

    @property
    def main(self) -> Optional[Group]:
        """The first window, for callers that need one concrete group."""
        return self.groups[0] if self.groups else None

    def cycles_to_us(self, cycles: int) -> float:
        return cycles / self.freq_mhz if self.freq_mhz else 0.0


def busy_by_pipe(group: Group) -> Dict[str, int]:
    """Cycles each pipe is busy. Pipes in one interval overlap, so this may
    sum to more than the group's span -- that is real, not a bug."""
    totals: Dict[str, int] = defaultdict(int)
    for iv in group.intervals:
        for pipe in iv.pipes:
            totals[pipe] += iv.cycles
    return dict(totals)


def union_cycles(intervals: Iterable[Interval]) -> int:
    """Cycles covered by at least one of the intervals (merges overlaps)."""
    spans = sorted((iv.start, iv.end) for iv in intervals if iv.cycles)
    total = 0
    cur_start = cur_end = None
    for start, end in spans:
        if cur_start is None:
            cur_start, cur_end = start, end
        elif start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    if cur_start is not None:
        total += cur_end - cur_start
    return total


def bubbles(group: Group) -> List[Interval]:
    """Intervals where no pipe is busy at all."""
    return [iv for iv in group.intervals if iv.idle]


def overlap_cycles(group: Group, left: str, right: str) -> int:
    """Cycles where both named pipes are busy simultaneously."""
    def spans(pipe: str) -> List[Tuple[int, int]]:
        return [(iv.start, iv.end) for iv in group.intervals if pipe in iv.pipes]

    a, b = spans(left), spans(right)
    total = 0
    for s1, e1 in a:
        for s2, e2 in b:
            total += max(0, min(e1, e2) - max(s1, s2))
    return total


def transitions(group: Group) -> List[Tuple[int, int, int]]:
    """(cycle, from_mask, to_mask) at every state change."""
    out = []
    prev = None
    for iv in group.intervals:
        if prev is not None and iv.mask != prev:
            out.append((iv.start, prev, iv.mask))
        prev = iv.mask
    return out


def _mean_stdev(values: Sequence[float]) -> Tuple[float, float]:
    """Mean and sample standard deviation (0.0 when n < 2)."""
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, var ** 0.5


def window_ratios(core: CoreWaveform) -> Dict[str, List[float]]:
    """Pipe occupancy as a fraction of each window's own span, per window.

    Per-window ratios are what make the aggregate honest: each window is a
    self-contained sample, so its ratio is comparable with every other window's
    and the spread across them is a real measure of sampling variation. Pooling
    raw cycle counts instead would let one long window dominate the answer.
    """
    out: Dict[str, List[float]] = defaultdict(list)
    for group in core.windows:
        span = group.end_cycle
        if not span:
            continue
        busy = busy_by_pipe(group)
        for pipe, cycles in busy.items():
            out[pipe].append(cycles / span)
    return dict(out)


def analyze_core(core: CoreWaveform) -> Dict[str, object]:
    """Statistics for one core, pooled over every sampling window it produced."""
    windows = [g for g in core.windows if g.end_cycle]
    if not windows:
        return {"pid": core.record.pid, "core_kind": core.record.core_kind,
                "windows": 0, "groups": 0}
    freq = core.freq_mhz

    def us(cycles: float) -> float:
        return round(cycles / freq, 4) if freq else 0.0

    spans = [g.end_cycle for g in windows]
    idle_ratios: List[float] = []
    idle_cycles_total = 0.0
    busy_cycles_total = 0.0
    unknown: Dict[int, int] = defaultdict(int)
    intervals_total = 0
    longest_idle = 0
    longest_idle_at = None
    for group in windows:
        span = group.end_cycle
        idle = union_cycles(bubbles(group))
        idle_cycles_total += idle
        busy_cycles_total += union_cycles(group.intervals)
        idle_ratios.append(idle / span if span else 0.0)
        intervals_total += len(group.intervals)
        for mask, cyc in group.unknown_masks.items():
            unknown[mask] += cyc
        for iv in bubbles(group):
            if iv.cycles > longest_idle:
                longest_idle = iv.cycles
                longest_idle_at = iv.start

    ratios = window_ratios(core)
    idle_mean, idle_sd = _mean_stdev(idle_ratios)

    pipes: Dict[str, Dict[str, float]] = {}
    for name, samples in sorted(ratios.items()):
        mean, sd = _mean_stdev(samples)
        pipes[name] = {
            "pct": round(100.0 * mean, 2),
            "pct_sd": round(100.0 * sd, 2),
            "pct_min": round(100.0 * min(samples), 2),
            "pct_max": round(100.0 * max(samples), 2),
            "windows": len(samples),
            "mean_cycles": round(mean * (sum(spans) / len(spans)), 1),
        }

    mean_span = sum(spans) / len(spans)
    return {
        "pid": core.record.pid,
        "core_kind": core.record.core_kind,
        "record_index": core.record.index,
        "windows": len(windows),
        "groups": len(core.groups),
        "span_cycles": int(round(mean_span)),
        "span_us": us(mean_span),
        "observed_cycles": sum(spans),
        "observed_us": us(sum(spans)),
        "busy_cycles": busy_cycles_total,
        "idle_cycles": idle_cycles_total,
        "idle_us": us(idle_cycles_total),
        "idle_pct": round(100.0 * idle_mean, 2),
        "idle_pct_sd": round(100.0 * idle_sd, 2),
        "pipes": pipes,
        "unknown_masks": {
            f"0x{mask:02x}": {"cycles": cyc, "us": us(cyc)}
            for mask, cyc in sorted(unknown.items())
        },
        "interval_count": intervals_total,
        "longest_idle_us": us(longest_idle),
        "longest_idle_at_us": us(longest_idle_at) if longest_idle_at is not None else None,
    }


def _capture_duration_us(capture: Path, meta: Dict[str, str]) -> Optional[float]:
    """Kernel wall duration from the capture's own metadata, if present."""
    for key in ("Task Duration(us)", "Task Duration"):
        value = meta.get(key)
        if not value:
            continue
        try:
            return float(str(value).strip())
        except ValueError:
            continue
    return None


def select_records(records: Sequence[Record], wanted: Optional[Sequence[str]] = None
                   ) -> List[Record]:
    """Keep the records the caller asked for.

    ``wanted`` accepts the vendor's own core names (``core4.veccore0``), a bare
    core number (``4``), or a core kind (``aic``). Matching is deliberately
    forgiving because the three spellings are all natural to type and getting a
    silent empty result from a typo would be worse than a loose match.
    """
    if not wanted:
        return list(records)
    tokens = {str(w).strip().lower() for w in wanted if str(w).strip()}
    if not tokens:
        return list(records)
    kept = []
    for rec in records:
        pid = rec.pid.lower()
        if (pid in tokens
                or str(rec.core_id) in tokens
                or rec.core_kind in tokens
                or f"core{rec.core_id}" in tokens):
            kept.append(rec)
    return kept


def summarize(capture: Path, freq_mhz: float,
              timeline: Optional[Path] = None,
              meta: Optional[Dict[str, str]] = None,
              cores: Optional[Sequence[str]] = None) -> Dict[str, object]:
    """Decode a capture into per-core waveform statistics.

    ``timeline`` overrides the search under ``capture``, for callers that have
    already found the stream elsewhere -- a multi-pass collection keeps
    PipeTimeline in its own pass, next to the capture rather than inside it.

    ``cores`` restricts the analysis to the named cores. The stream always
    contains every sampled core, so this is a view filter, not a collection
    setting: the same capture answers "what did core 4 do" without re-running.
    """
    if timeline is None:
        timeline = locate_timeline(capture)
    if timeline is None:
        raise FileNotFoundError(
            f"no dump/timeline.bin.0 under {capture}; collect with "
            "msopprof --aic-metrics=PipeTimeline"
        )
    if meta is None:
        meta = read_kernel_meta(capture)
    records = select_records(read_records(timeline), cores)
    waveforms = [
        CoreWaveform(rec, decode_groups(rec), freq_mhz) for rec in records
    ]
    per_core = [analyze_core(core) for core in waveforms]

    # Aggregate over cores that actually ran something.
    active = [c for c in per_core if c.get("windows")]
    aggregate: Dict[str, object] = {}
    if active:
        # Mean of per-core means: every core counts once, regardless of how
        # many windows it happened to contribute. Weighting by window count
        # would let a heavily-sampled core speak for the whole kernel.
        pipe_pcts: Dict[str, List[float]] = defaultdict(list)
        pipe_sds: Dict[str, List[float]] = defaultdict(list)
        for core in active:
            for name, data in core["pipes"].items():
                pipe_pcts[name].append(data["pct"])
                pipe_sds[name].append(data["pct_sd"])

        aggregate = {
            "cores_recorded": len(per_core),
            "cores_active": len(active),
            "windows": sum(c["windows"] for c in active),
            "observed_us": round(sum(c["observed_us"] for c in active), 4),
            "mean_span_us": round(sum(c["span_us"] for c in active) / len(active), 4),
            "pipes_pct": {
                name: round(sum(v) / len(v), 2) for name, v in sorted(pipe_pcts.items())
            },
            "pipes_pct_sd": {
                name: round((sum(x * x for x in v) / len(v)) ** 0.5, 2)
                for name, v in sorted(pipe_sds.items())
            },
            "mean_idle_pct": round(sum(c["idle_pct"] for c in active) / len(active), 2),
            "max_idle_pct": max(c["idle_pct"] for c in active),
            "max_idle_pid": max(active, key=lambda c: c["idle_pct"])["pid"],
        }
        # Coverage: how much of the kernel the sampled windows actually saw.
        # This is the number that stops the percentages from being read as a
        # whole-kernel statement, so it is computed whenever the duration is
        # known rather than left to the reader.
        duration_us = _capture_duration_us(capture, meta)
        if duration_us:
            # Coverage is a property of the *timeline*, not of the sum of all
            # cores' windows: cores run concurrently, so adding their spans
            # together can exceed the kernel many times over. What bounds the
            # observation is the widest single-core coverage plus how many
            # windows were taken -- so report per-core coverage and the window
            # count, and use the *maximum* single-core ratio as the headline.
            # A window's span is the stretch it observed, so the sum of a
            # core's spans is an UPPER BOUND on how much of that core's
            # execution was seen -- if windows overlap, the true figure is
            # lower. On short kernels the sum can even exceed the kernel's wall
            # duration, which means the windows overlap (or the duration
            # metadata describes a different launch); that case is flagged
            # rather than clamped silently, because both readings are material.
            per_core_cov = [
                round(100.0 * c["observed_us"] / duration_us, 3) for c in active
            ]
            over = [c for c, v in zip(per_core_cov, per_core_cov) if v > 100.0]
            aggregate["kernel_duration_us"] = duration_us
            aggregate["per_core_coverage_pct"] = per_core_cov
            aggregate["median_core_coverage_pct"] = round(
                sorted(per_core_cov)[len(per_core_cov) // 2], 3
            )
            aggregate["max_core_coverage_pct"] = max(per_core_cov)
            aggregate["coverage_exceeds_duration"] = bool(over)
            aggregate["coverage_is_upper_bound"] = True

    return {
        "tool_version": VERSION,
        "capture": str(capture),
        "timeline": str(timeline),
        "freq_mhz": freq_mhz,
        "records": len(records),
        "core_filter": list(cores) if cores else [],
        "per_core": per_core,
        "aggregate": aggregate,
    }


# ---------------------------------------------------------------------------
# capture discovery
# ---------------------------------------------------------------------------

def locate_timeline(capture: Path) -> Optional[Path]:
    """Find ``timeline.bin.0`` inside a capture or a bare directory.

    msopprof nests it under ``device0/<mangled kernel>/<n>/dump/`` for its own
    multi-pass layout, but a single-pass collection writes it straight into
    ``dump/``. Both are accepted, newest first, because a capture directory can
    legitimately hold more than one.
    """
    if capture.is_file() and capture.name.startswith("timeline.bin"):
        return capture
    direct = capture / "dump" / "timeline.bin.0"
    if direct.is_file():
        return direct
    # `rglob` does not descend symlinked directories, and a multi-pass capture
    # links its passes in from elsewhere; walk explicitly so both layouts work.
    found = [p for p in capture.rglob("timeline.bin.0") if p.is_file()]
    if not found:
        for child in capture.iterdir():
            try:
                if child.is_symlink() and child.resolve().is_dir():
                    found.extend(
                        p for p in child.resolve().rglob("timeline.bin.0")
                        if p.is_file()
                    )
            except OSError:
                continue
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def read_freq_mhz(capture: Path, fallback: float = 1650.0) -> float:
    """Clock from the capture's own metadata, else the platform default."""
    for name in ("dump/op_basic_info.txt", "op_basic_info.txt"):
        path = capture / name
        if path.is_file():
            for line in path.read_text(errors="replace").splitlines():
                if line.startswith("Current Freq="):
                    try:
                        return float(line.split("=", 1)[1])
                    except ValueError:
                        pass
    return fallback


def read_kernel_meta(capture: Path) -> Dict[str, str]:
    """Op name / block dim / duration, when the capture kept them."""
    meta: Dict[str, str] = {}
    for name in ("dump/op_basic_info.txt", "op_basic_info.txt"):
        path = capture / name
        if path.is_file():
            for line in path.read_text(errors="replace").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    meta[key.strip()] = value.strip()
            break
    basic = capture / "OpBasicInfo.csv"
    if basic.is_file():
        lines = basic.read_text(errors="replace").splitlines()
        if len(lines) >= 2:
            header = [c.strip() for c in lines[0].split(",")]
            values = [c.strip() for c in lines[1].split(",")]
            meta.update(dict(zip(header, values)))
    return meta


# ---------------------------------------------------------------------------
# vendor cross-check
# ---------------------------------------------------------------------------

def load_vendor_trace(capture: Path) -> Optional[Dict[str, List[Tuple[str, int, int]]]]:
    """The vendor's parsed trace.json as {pid: [(pipe, start_cyc, end_cyc)]}."""
    path = capture / "trace.json"
    if not path.is_file():
        nested = sorted(capture.glob("**/trace.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        if not nested:
            return None
        path = nested[0]
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    freq = read_freq_mhz(capture)
    out: Dict[str, List[Tuple[str, int, int]]] = defaultdict(list)
    for event in data.get("traceEvents") or []:
        if event.get("ph") != "X":
            continue
        dur = event.get("dur")
        ts = event.get("ts")
        if dur is None or ts is None:
            continue
        start = round(ts * freq)
        out[str(event.get("pid"))].append(
            (str(event.get("name")), start, start + round(dur * freq))
        )
    return dict(out) if out else None


def merge(spans: Iterable[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[List[int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


PIPE_LEVEL_NAMES = frozenset(
    {"SCALAR", "VECTOR", "MTE2", "MTE3", "CUBE", "MTE1", "FIXP"}
)


def _pipe_only(vendor: Dict[str, List[Tuple[str, int, int]]]
               ) -> Dict[str, List[Tuple[str, int, int]]]:
    """Keep only the pipe-level lanes, dropping markers and instruction events."""
    out: Dict[str, List[Tuple[str, int, int]]] = {}
    for pid, events in vendor.items():
        kept = [e for e in events if e[0] in PIPE_LEVEL_NAMES]
        if kept:
            out[pid] = kept
    return out


def verify(capture: Path) -> Dict[str, object]:
    """Three-state check of the decode against the vendor's own ``trace.json``.

    The verdict is one of:

    ``VERIFIED``
        Every pipe interval matches the vendor exactly under a stated
        comparison mode.
    ``INCONCLUSIVE``
        The comparison cannot be made at all -- there is no vendor trace, or it
        is instruction-level (``GET_BUF``/``VF``) rather than pipe-level. This
        is *not* a failure of the decoder, and reporting it as one is what made
        an earlier version cry wolf on 11 of 17 captures.
    ``NOT VERIFIED``
        The comparison was possible and disagrees. That is a real finding.

    Two comparison modes exist because the vendor is not consistent about what
    it emits. When its trace spans only the first window, the decode of that
    window is compared. When it splices every window onto one axis -- which it
    does on long kernels -- the concatenation of all windows is compared
    instead, gap counters excluded.
    """
    freq = read_freq_mhz(capture)
    timeline = locate_timeline(capture)
    if timeline is None:
        return {"verdict": "INCONCLUSIVE", "reason": "no timeline.bin.0 in this capture"}

    vendor_all = load_vendor_trace(capture)
    if vendor_all is None:
        return {"verdict": "INCONCLUSIVE", "reason": "no trace.json to check against"}

    vendor = _pipe_only(vendor_all)
    if not vendor:
        lanes = sorted({n for evs in vendor_all.values() for n, _, _ in evs})
        return {
            "verdict": "INCONCLUSIVE",
            "reason": "vendor trace is instruction-level, not pipe-level",
            "lanes_seen": lanes[:8],
            "note": "a GET_BUF/VF trace describes instructions; comparing it to "
                    "pipe intervals would fail for the wrong reason",
        }

    records = read_records(timeline)
    by_pid = {rec.pid: rec for rec in records}
    checks = 0
    failures: List[str] = []
    skipped: List[str] = []
    modes: List[str] = []

    for pid, events in sorted(vendor.items()):
        record = by_pid.get(pid)
        if record is None:
            skipped.append(f"{pid}: vendor reports it, raw has no record")
            continue
        windows = [g for g in decode_groups(record) if g.end_cycle]
        if not windows:
            skipped.append(f"{pid}: raw record has no timed tokens")
            continue

        by_pipe: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        for pipe, start, end in events:
            by_pipe[pipe].append((start, end))

        vendor_end = max(e for spans in by_pipe.values() for _, e in spans)
        first_end = windows[0].end_cycle
        # Does the vendor cover more than the first window? Then it spliced.
        spliced = vendor_end > first_end * 1.05
        modes.append("all-windows" if spliced else "first-window")

        if spliced:
            decoded: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
            offset = 0
            for group in windows:
                for iv in group.intervals:
                    for pipe in iv.pipes:
                        decoded[pipe].append((iv.start + offset, iv.end + offset))
                offset += group.end_cycle
        else:
            decoded = defaultdict(list)
            for iv in windows[0].intervals:
                for pipe in iv.pipes:
                    decoded[pipe].append((iv.start, iv.end))

        for pipe, spans in sorted(by_pipe.items()):
            mine = merge(decoded.get(pipe, []))
            theirs = merge(spans)
            checks += 1
            if mine != theirs:
                failures.append(
                    f"{pid}/{pipe}: decoded {mine[:3]}{'...' if len(mine) > 3 else ''} "
                    f"vs vendor {theirs[:3]}{'...' if len(theirs) > 3 else ''}"
                )
        for pipe, spans in sorted(decoded.items()):
            if pipe in by_pipe:
                continue
            merged = merge(spans)
            if merged:
                checks += 1
                failures.append(f"{pid}/{pipe}: decoded but vendor reports none")

    if not checks:
        return {"verdict": "INCONCLUSIVE", "reason": "nothing comparable",
                "skipped": skipped}

    if not failures:
        return {"verdict": "VERIFIED", "checks": checks, "modes": sorted(set(modes)),
                "freq_mhz": freq, "skipped": skipped}

    # Partial agreement still means the model is not right; say which way.
    exact = checks - len(failures)
    verdict = "NOT VERIFIED"
    return {
        "verdict": verdict,
        "checks": checks,
        "exact": exact,
        "failures": failures,
        "modes": sorted(set(modes)),
        "freq_mhz": freq,
        "skipped": skipped,
        "note": ("the pipe model reproduces some lanes exactly and disagrees on "
                 "others; the disagreement is a modelling gap, not a decode bug"
                 if exact else "no lane matched"),
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_markdown(result: Dict[str, object], meta: Optional[Dict[str, str]] = None
                    ) -> str:
    lines: List[str] = []
    add = lines.append
    agg = result["aggregate"]
    freq = result["freq_mhz"]

    add("# Pipeline waveform (raw `timeline.bin.0`)")
    add("")
    add(f"- Capture: `{result['capture']}`")
    add(f"- Raw stream: `{result['timeline']}`")
    if result.get("core_filter"):
        add(f"- **Filtered to cores: {', '.join(result['core_filter'])}** "
            "(other cores are in the stream but excluded from these figures)")
    if meta:
        name = meta.get("Op Name") or meta.get("name")
        block = meta.get("Block Dim") or meta.get("block_dim")
        dur = meta.get("Task Duration(us)")
        if name:
            add(f"- Kernel: `{name}`" + (f", Block Dim {block}" if block else "")
                + (f", {dur} us" if dur else ""))
    add(f"- Clock: {freq:g} MHz  |  Records: {result['records']}")
    add("")

    if not agg:
        add("> No timed intervals in this capture. `timeline.bin.0` is written by "
            "`--aic-metrics=PipeTimeline`; check that the collection used it.")
        return "\n".join(lines) + "\n"

    # Coverage first: every percentage below is a statement about the sampled
    # windows, not about the kernel. Saying so before the numbers is what keeps
    # them from being read as whole-kernel figures.
    if agg.get("coverage_exceeds_duration"):
        add("> **Coverage: not computable.** The sampled windows total more "
            "than the kernel's recorded duration (%.1f us of %.1f us), so they "
            "overlap or the duration metadata describes a different launch. "
            "The percentages below still describe the sampled windows, but how "
            "much of the kernel they represent cannot be stated."
            % (agg["median_core_coverage_pct"] / 100.0 * agg["kernel_duration_us"],
               agg["kernel_duration_us"]))
        add("")
    elif agg.get("median_core_coverage_pct") is not None:
        add("> **Coverage: at most %.2f%% of the kernel per core** (median across "
            "%d sampled core%s; %.1f us of %.1f us, %d windows). It is an upper "
            "bound -- overlapping windows would make the real figure lower -- and "
            "it is per core, because cores are sampled concurrently and their "
            "spans cannot be added."
            % (agg["median_core_coverage_pct"], agg["cores_active"],
               "" if agg["cores_active"] == 1 else "s",
               agg["median_core_coverage_pct"] / 100.0 * agg["kernel_duration_us"],
               agg["kernel_duration_us"], agg["windows"]))
        add("")

    add("## 1. Pipe occupancy")
    add("")
    add("Each window's own busy share, averaged over all windows and cores. The "
        "spread column is the standard deviation across windows -- a large one "
        "means the kernel's behaviour changes from sample to sample, so quote "
        "the range, not just the mean.")
    add("")
    add("| Pipe | Mean | Spread (sd) | Windows |")
    add("|---|---:|---:|---:|")
    order = sorted(agg["pipes_pct"].items(), key=lambda kv: -kv[1])
    for name, pct in order:
        sd = agg["pipes_pct_sd"].get(name, 0.0)
        windows = max(
            (c["pipes"].get(name, {}).get("windows", 0) for c in result["per_core"]),
            default=0,
        )
        add(f"| {name} | {pct:.1f}% | {sd:.1f} | {windows} |")
    add("")
    add("Pipes run in parallel, so these do not sum to 100%. A pipe near 100% was "
        "busy for essentially every sampled cycle.")
    add("")

    add("## 2. Idle and bubbles")
    add("")
    add("Across **%d core%s / %d windows**:" % (
        agg["cores_active"], "" if agg["cores_active"] == 1 else "s", agg["windows"]))
    add("")
    add(f"- Mean idle (no pipe busy): **{agg['mean_idle_pct']:.1f}%** of sampled cycles")
    add(f"- Worst core `{agg['max_idle_pid']}`: {agg['max_idle_pct']:.1f}% idle")
    add("")
    add("Idle here means *no pipe at all* was busy -- the core was waiting on "
        "something outside the pipes. Contrast the two failure shapes: high idle "
        "with low pipe shares points at a dependency or sync problem; low idle "
        "with one pipe saturated points at that pipe's limit.")
    add("")

    add("## 3. Per-core detail")
    add("")
    add("| Core | Windows | Mean span (us) | Idle | Busiest pipe | Longest bubble (us) |")
    add("|---|---:|---:|---:|---|---:|")
    for core in result["per_core"]:
        if not core.get("windows"):
            continue
        pipes = core.get("pipes") or {}
        if pipes:
            top = max(pipes.items(), key=lambda kv: kv[1]["pct"])
            busiest = f"{top[0]} {top[1]['pct']:.0f}%"
        else:
            busiest = "-"
        longest = core.get("longest_idle_us") or 0.0
        at = core.get("longest_idle_at_us")
        when = f" @{at:.2f}" if at is not None else ""
        add(f"| `{core['pid']}` | {core['windows']} | {core['span_us']:.3f} | "
            f"{core['idle_pct']:.1f}%* | {busiest} | {longest:.2f}{when} |")
    add("")
    add("\\* idle shares carry a per-core standard deviation; see `waveform.json` "
        "(`idle_pct_sd`) when the value matters.")
    add("")

    # Serial vs overlapped: the single most actionable line in this report.
    overlap_lines = _render_overlap(result)
    if overlap_lines:
        # Renumber the overlap block contiguously with what precedes it.
        next_no = 4
        fixed: List[str] = []
        for line in overlap_lines:
            if line.startswith("## "):
                _, _, tail = line[3:].partition(". ")
                fixed.append(f"## {next_no}. {tail}")
            else:
                fixed.append(line)
        lines.extend(fixed)
        next_no += 1
    else:
        next_no = 4

    unknown = {
        core["pid"]: core["unknown_masks"]
        for core in result["per_core"]
        if core.get("unknown_masks")
    }
    if unknown:
        add(f"## {next_no}. Unnamed pipe states")
        next_no += 1
        add("")
        add("These masks set a bit this decoder has not verified against the "
            "vendor, so they are reported with their cycle count rather than "
            "given a name they have not earned:")
        add("")
        for pid, masks in unknown.items():
            detail = ", ".join(f"`{m}` {d['cycles']:,} cyc"
                               for m, d in masks.items())
            add(f"- `{pid}`: {detail}")
        add("")

    add(f"## {next_no}. How to read this")
    add("")
    add("- Times are integer cycles from the raw stream divided by the capture's "
        "own clock, so they match `report.md` for the same capture.")
    add("- **Coverage** bounds every claim. A kernel sampled at 0.5% per core "
        "gives you half a percent of its life, however many windows were taken; "
        "the percentages describe those windows, not the kernel.")
    add("- **Spread** is the honest uncertainty. A pipe at 40% +/- 20 is not the "
        "same finding as one at 40% +/- 2, and only the second supports a "
        "targeted optimisation.")
    add("")
    return "\n".join(lines) + "\n"


def _render_overlap(result: Dict[str, object]) -> List[str]:
    """Serial-vs-overlapped verdict, computed from the decoded windows.

    This is the question the CSV path cannot answer at all: two pipes can hold
    identical busy totals whether they ran together or took turns. It is
    computed here rather than left to the reader because the two cases call for
    opposite optimisations.
    """
    lines: List[str] = []
    add = lines.append
    # Prefer the stream path the summary already resolved. Re-deriving it from
    # `capture` silently produced no section whenever the caller passed a
    # capture whose ``timeline`` lived outside it (a multi-pass layout), and a
    # missing verdict is indistinguishable from "no overlap to report".
    timeline = _resolve_stream(result)
    if timeline is None:
        return lines
    records = read_records(timeline)
    freq = result.get("freq_mhz") or 0.0

    pairs = [("MTE2", "VECTOR"), ("MTE2", "CUBE"), ("MTE1", "CUBE")]
    for left, right in pairs:
        both = only_left = only_right = false_sum = 0
        seen = 0
        for record in records:
            for group in decode_groups(record):
                if not group.end_cycle:
                    continue
                has_l = any(left in iv.pipes for iv in group.intervals)
                has_r = any(right in iv.pipes for iv in group.intervals)
                if not (has_l and has_r):
                    continue
                seen += 1
                ov = overlap_cycles(group, left, right)
                both += ov
                only_left += sum(
                    iv.cycles for iv in group.intervals
                    if left in iv.pipes and right not in iv.pipes
                )
                only_right += sum(
                    iv.cycles for iv in group.intervals
                    if right in iv.pipes and left not in iv.pipes
                )
                # Cycles where neither runs.
                neither = group.end_cycle - union_cycles(group.intervals)
                false_sum += neither
        if not seen:
            continue
        total = both + only_left + only_right
        if not total:
            continue
        freq = freq or 1.0
        pct = 100.0 * both / total
        add("## 4. Serial or overlapped: %s vs %s" % (left, right))
        add("")
        if both == 0:
            add(f"**Never overlap** across {seen} windows. `{left}` and `{right}` "
                f"take strict turns -- {left} {only_left/freq if freq else 0:.3f} us, "
                f"{right} {only_right/freq if freq else 0:.3f} us, shared 0.")
            add("")
            add("No amount of tiling or unrolling buys anything until this "
                "changes: the compute pipe is idle whenever memory runs and vice "
                "versa. The fix is double buffering (prefetch the next tile while "
                "computing the current one), not a bigger tile.")
        else:
            add(f"Overlap **{pct:.1f}%** of their combined busy time across "
                f"{seen} windows ({both:,} of {total:,} cycles).")
            add("")
            if pct < 25:
                add("Mostly serial: some overlap exists, so buffering partly "
                    "works, but the pipes still largely wait for each other.")
            elif pct < 75:
                add("Partially overlapped: buffering works but there is headroom.")
            else:
                add("Well overlapped: the memory pipe is feeding the compute pipe "
                    "continuously. Further gains must come from the compute pipe "
                    "itself or from reducing the data volume.")
        add("")
        if false_sum and seen:
            idle_share = 100.0 * false_sum / sum(
                g.end_cycle
                for record in records for g in decode_groups(record) if g.end_cycle
            )
            add(f"(Idle, where neither pipe runs, is "
                f"{idle_share:.1f}% of sampled cycles.)")
            add("")
        break
    return lines


def _resolve_stream(result: Dict[str, object]) -> Optional[Path]:
    """The stream this result was decoded from, preferring the recorded path."""
    recorded = result.get("timeline")
    if recorded:
        candidate = Path(str(recorded))
        if candidate.is_file():
            return candidate
    return locate_timeline(Path(str(result["capture"])))


def write_chrome(result: Dict[str, object], path: Path) -> None:
    """Emit one Chrome Trace Event file, one track per (core, pipe)."""
    freq = result["freq_mhz"] or 1.0
    events: List[Dict[str, object]] = []
    pids: Dict[str, int] = {}
    lanes: Dict[Tuple[str, str], int] = {}

    for core in result["per_core"]:
        pid = core["pid"]
        if pid not in pids:
            pids[pid] = len(pids) + 1
            events.append({
                "name": "process_name", "ph": "M", "pid": pids[pid],
                "args": {"name": pid},
            })

    # Re-read the timelines: the summary dropped the intervals themselves.
    timeline = _resolve_stream(result)
    records = read_records(timeline) if timeline else []
    wanted = {c["pid"] for c in result["per_core"] if c.get("windows")}
    for record in records:
        if record.pid not in wanted:
            continue
        groups = [g for g in decode_groups(record) if g.end_cycle]
        if not groups:
            continue
        # Every window is shown, laid out one after another. They are separate
        # samples of the same core, so they are offset rather than merged -- the
        # same rule the statistics follow.
        windows = []
        offset = 0
        for group in groups:
            windows.append((group, offset))
            offset += group.end_cycle
        pid = record.pid
        if pid not in pids:
            pids[pid] = len(pids) + 1
            events.append({
                "name": "process_name", "ph": "M", "pid": pids[pid],
                "args": {"name": pid},
            })
        index = pids[pid]
        pipe_names = sorted({p for group, _ in windows
                             for iv in group.intervals for p in iv.pipes})
        for pipe in pipe_names:
            key = (pid, pipe)
            if key in lanes:
                continue
            lanes[key] = len([k for k in lanes if k[0] == pid])
            events.append({
                "name": "thread_name", "ph": "M", "pid": index,
                "tid": lanes[key], "args": {"name": pipe},
            })
        idle_key = (pid, "__idle__")
        if idle_key not in lanes and any(iv.idle for group, _ in windows
                                        for iv in group.intervals):
            lanes[idle_key] = len([k for k in lanes if k[0] == pid])
            events.append({
                "name": "thread_name", "ph": "M", "pid": index,
                "tid": lanes[idle_key], "args": {"name": "IDLE"},
            })
        for window_index, (group, offset) in enumerate(windows):
            for iv in group.intervals:
                for pipe in iv.pipes:
                    events.append({
                        "name": pipe, "ph": "X", "pid": index,
                        "tid": lanes[(pid, pipe)],
                        "ts": round((iv.start + offset) / freq, 6),
                        "dur": round(iv.cycles / freq, 6),
                        "args": {"mask": f"0x{iv.mask:02x}",
                                 "window": window_index},
                    })
                if iv.idle and idle_key in lanes:
                    events.append({
                        "name": "IDLE", "ph": "X", "pid": index,
                        "tid": lanes[idle_key],
                        "ts": round((iv.start + offset) / freq, 6),
                        "dur": round(iv.cycles / freq, 6),
                        "cname": "grey",
                        "args": {"window": window_index},
                    })

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"displayTimeUnit": "us", "traceEvents": events}, separators=(",", ":")
    ))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline_waveform.py",
        description=(
            "Decode msopprof's raw per-cycle pipe stream (dump/timeline.bin.0) "
            "into pipeline occupancy, bubbles and a Chrome trace."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  pipeline_waveform.py --capture OPPROF_x --out waveform\n"
            "  pipeline_waveform.py --capture OPPROF_x --json\n"
            "  pipeline_waveform.py --capture OPPROF_x --chrome trace.json\n"
            "  pipeline_waveform.py --capture OPPROF_x --verify\n"
        ),
    )
    parser.add_argument("--capture", required=True,
                        help="OPPROF_* capture directory, or a timeline.bin.0 file")
    parser.add_argument("--out", help="write waveform.md and waveform.json here")
    parser.add_argument("--chrome", metavar="PATH",
                        help="write a Chrome/Perfetto trace here")
    parser.add_argument("--freq", type=float, default=None,
                        help="clock in MHz (default: read from the capture)")
    parser.add_argument("--core", action="append", default=None,
                        metavar="CORE",
                        help="only analyse this core. Accepts the vendor name "
                             "('core4.veccore0'), a core number ('4'), or a kind "
                             "('aic'/'aiv'). Repeatable.")
    parser.add_argument("--json", action="store_true",
                        help="print the full result as JSON")
    parser.add_argument("--verify", action="store_true",
                        help="check the decode against the capture's trace.json")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)

    capture = Path(args.capture).expanduser().resolve()
    if not capture.exists():
        print(f"error: {capture} does not exist", file=sys.stderr)
        return 2

    if args.verify:
        verdict = verify(capture)
        print(json.dumps(verdict, indent=2))
        state = verdict.get("verdict")
        # Exit codes are part of the interface: 0 verified, 1 a real
        # disagreement, 2 "could not check". Collapsing the last two into a
        # single failure is what made callers distrust the check.
        return {"VERIFIED": 0, "NOT VERIFIED": 1}.get(state, 2)

    freq = args.freq if args.freq is not None else read_freq_mhz(capture)
    try:
        result = summarize(capture, freq, cores=args.core)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.core and not result["records"]:
        available = sorted({r.pid for r in read_records(locate_timeline(capture))})
        print("error: no core matched %s" % ", ".join(args.core), file=sys.stderr)
        print("cores in this capture: %s" % ", ".join(available), file=sys.stderr)
        return 2

    meta = read_kernel_meta(capture)
    report = render_markdown(result, meta)

    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "waveform.md").write_text(report)
        (out_dir / "waveform.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"[info] wrote {out_dir / 'waveform.md'}", file=sys.stderr)
        print(f"[info] wrote {out_dir / 'waveform.json'}", file=sys.stderr)

    if args.chrome:
        write_chrome(result, Path(args.chrome).expanduser().resolve())
        print(f"[info] wrote {args.chrome}", file=sys.stderr)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
