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
    def main(self) -> Optional[Group]:
        """The group that carries the kernel body.

        That is the *first* group: later groups sit behind a ``0x0dxx``
        boundary, start from cycle 0 again, and hold a follow-up sample of the
        same core (their counter block differs -- the first group carries
        ``0xd88``, later ones ``0xd99`` onward). The vendor's ``trace.json``
        reports the first group only, so picking the longest one instead would
        mix a later sample into the numbers and inflate the span.
        """
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


def analyze_core(core: CoreWaveform) -> Dict[str, object]:
    group = core.main
    if group is None:
        return {"pid": core.record.pid, "groups": 0}
    span = group.end_cycle
    busy = busy_by_pipe(group)
    idle = union_cycles(bubbles(group))
    covered = union_cycles(group.intervals)
    freq = core.freq_mhz

    def us(cycles: int) -> float:
        return round(cycles / freq, 4) if freq else 0.0

    return {
        "pid": core.record.pid,
        "core_kind": core.record.core_kind,
        "record_index": core.record.index,
        "groups": len(core.groups),
        "span_cycles": span,
        "span_us": us(span),
        "busy_cycles": covered,
        "busy_us": us(covered),
        "idle_cycles": idle,
        "idle_us": us(idle),
        "idle_pct": round(100.0 * idle / span, 2) if span else 0.0,
        "pipes": {
            name: {
                "cycles": cycles,
                "us": us(cycles),
                "pct": round(100.0 * cycles / span, 2) if span else 0.0,
            }
            for name, cycles in sorted(busy.items())
        },
        "unknown_masks": {
            f"0x{mask:02x}": {"cycles": cyc, "us": us(cyc)}
            for mask, cyc in sorted(group.unknown_masks.items())
        },
        "interval_count": len(group.intervals),
        "state_changes": len(transitions(group)),
        "longest_idle_us": us(max((iv.cycles for iv in bubbles(group)), default=0)),
        "longest_idle_at_us": us(
            max(bubbles(group), key=lambda iv: iv.cycles).start
        ) if bubbles(group) else None,
    }


def summarize(capture: Path, freq_mhz: float,
              timeline: Optional[Path] = None) -> Dict[str, object]:
    """Decode a capture into per-core waveform statistics.

    ``timeline`` overrides the search under ``capture``, for callers that have
    already found the stream elsewhere -- a multi-pass collection keeps
    PipeTimeline in its own pass, next to the capture rather than inside it.
    """
    if timeline is None:
        timeline = locate_timeline(capture)
    if timeline is None:
        raise FileNotFoundError(
            f"no dump/timeline.bin.0 under {capture}; collect with "
            "msopprof --aic-metrics=PipeTimeline"
        )
    records = read_records(timeline)
    cores = [
        CoreWaveform(rec, decode_groups(rec), freq_mhz) for rec in records
    ]
    per_core = [analyze_core(core) for core in cores]

    # Aggregate over cores that actually ran something.
    active = [c for c in per_core if c.get("span_cycles")]
    aggregate: Dict[str, object] = {}
    if active:
        span_sum = sum(c["span_cycles"] for c in active)
        pipes: Dict[str, int] = defaultdict(int)
        for c in active:
            for name, data in c["pipes"].items():
                pipes[name] += data["cycles"]
        aggregate = {
            "cores_recorded": len(per_core),
            "cores_active": len(active),
            "mean_span_us": round(
                sum(c["span_us"] for c in active) / len(active), 4
            ),
            "union_mean_us": round(
                sum(c["busy_us"] for c in active) / len(active), 4
            ),
            "mean_idle_pct": round(
                sum(c["idle_pct"] for c in active) / len(active), 2
            ),
            "pipes_weighted_pct": {
                name: round(100.0 * cycles / span_sum, 2)
                for name, cycles in sorted(pipes.items())
            },
            "pipe_cycles": dict(sorted(pipes.items())),
            "max_idle_pct": max(c["idle_pct"] for c in active),
            "max_idle_pid": max(active, key=lambda c: c["idle_pct"])["pid"],
        }

    return {
        "tool_version": VERSION,
        "capture": str(capture),
        "timeline": str(timeline),
        "freq_mhz": freq_mhz,
        "records": len(records),
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


def verify(capture: Path) -> Dict[str, object]:
    """Check the decode against the vendor's own trace.json for the same run.

    ``trace.json`` and ``timeline.bin.0`` are two views of the *same* sampled
    records, so for the group the vendor chose they must agree interval for
    interval -- that is an equality test, not a containment test. It is the
    sharpest available check on the bit mapping and the timing convention: a
    wrong pipe bit or an off-by-one pairing breaks it immediately.

    The vendor keeps only its chosen sampling group per core, so the comparison
    runs against the record's *first* group (groups after a ``0x0dxx`` boundary
    are later samples with their own origin and have no vendor counterpart).
    """
    freq = read_freq_mhz(capture)
    timeline = locate_timeline(capture)
    if timeline is None:
        return {"ok": False, "reason": "no timeline.bin.0 in this capture"}
    vendor = load_vendor_trace(capture)
    if vendor is None:
        return {"ok": False, "reason": "no trace.json to check against",
                "note": "collect without PipeTimeline? then there is no vendor view"}

    records = read_records(timeline)
    by_pid = {rec.pid: rec for rec in records}
    checks = 0
    failures: List[str] = []
    skipped: List[str] = []

    for pid, events in sorted(vendor.items()):
        record = by_pid.get(pid)
        if record is None:
            skipped.append(f"{pid}: vendor reports it, raw has no record")
            continue
        groups = decode_groups(record)
        if not groups:
            skipped.append(f"{pid}: raw record has no timed tokens")
            continue
        group = groups[0]
        by_pipe: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        for pipe, start, end in events:
            by_pipe[pipe].append((start, end))

        for pipe, spans in sorted(by_pipe.items()):
            mine = merge(
                (iv.start, iv.end) for iv in group.intervals if pipe in iv.pipes
            )
            theirs = merge(spans)
            checks += 1
            if mine != theirs:
                failures.append(
                    f"{pid}/{pipe}: decoded {mine[:4]}"
                    f"{'...' if len(mine) > 4 else ''} "
                    f"vs vendor {theirs[:4]}{'...' if len(theirs) > 4 else ''}"
                )
        # Any pipe the decode claims but the vendor omits is equally wrong.
        for pipe in ("SCALAR", "VECTOR", "MTE2", "MTE3", "CUBE", "MTE1", "FIXP"):
            if pipe in by_pipe:
                continue
            mine = merge(
                (iv.start, iv.end) for iv in group.intervals if pipe in iv.pipes
            )
            if mine:
                checks += 1
                failures.append(
                    f"{pid}/{pipe}: decoded {mine[:4]} but vendor reports none"
                )

    return {
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "skipped": skipped,
        "freq_mhz": freq,
        "note": (
            "compares the raw decode of each record's first sampling group "
            "against the vendor trace.json for the same run"
        ),
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

    add("## 1. Pipeline utilisation by pipe")
    add("")
    add("Cycles a pipe is busy, as a share of each core's own span. Pipes run in "
        "parallel, so a row can exceed 100% -- that is overlap, not error.")
    add("")
    add("| Pipe | Share of summed core-span | Cycles |")
    add("|---|---:|---:|")
    for name, pct in agg["pipes_weighted_pct"].items():
        add(f"| {name} | {pct:.1f}% | {agg['pipe_cycles'][name]:,} |")
    add("")

    add("## 2. Bubbles")
    add("")
    add(f"- Mean idle share across {agg['cores_active']} active cores: "
        f"**{agg['mean_idle_pct']:.1f}%**")
    add(f"- Worst core: `{agg['max_idle_pid']}` at **{agg['max_idle_pct']:.1f}%** idle")
    add("")
    add("Idle means *no* pipe was busy for that stretch. A kernel with high idle "
        "is waiting on something outside the pipes (dependency, sync, launch "
        "gap); a kernel that is busy but slow is limited by the pipe doing the "
        "work.")
    add("")

    add("## 3. Per-core detail")
    add("")
    add("| Core | Span (us) | Idle | Busiest pipe | Longest idle |")
    add("|---|---:|---:|---|---:|")
    for core in result["per_core"]:
        if not core.get("span_cycles"):
            continue
        pipes = core["pipes"]
        if pipes:
            top = max(pipes.items(), key=lambda kv: kv[1]["cycles"])
            busiest = f"{top[0]} {top[1]['pct']:.0f}%"
        else:
            busiest = "--"
        longest = core.get("longest_idle_us") or 0.0
        at = core.get("longest_idle_at_us")
        when = f" @{at:.2f}us" if at else ""
        add(f"| `{core['pid']}` | {core['span_us']:.3f} | "
            f"{core['idle_pct']:.1f}% | {busiest} | {longest:.2f}{when} |")
    add("")

    unknown = {
        core["pid"]: core["unknown_masks"]
        for core in result["per_core"]
        if core.get("unknown_masks")
    }
    if unknown:
        add("### Unnamed pipe states")
        add("")
        add("These masks carry a bit this decoder has not verified. They are "
            "reported rather than guessed:")
        add("")
        for pid, masks in unknown.items():
            detail = ", ".join(
                f"`{mask}` ({data['cycles']:,} cyc)" for mask, data in masks.items()
            )
            add(f"- `{pid}`: {detail}")
        add("")

    add("## 4. How to read this")
    add("")
    add(f"- Times are cycles from the raw stream divided by the {freq:g} MHz clock, "
        "so they line up with `vis.trace`/`report.md` for the same capture.")
    add("- **Idle share** is the number to look at first: it is time no pipe was "
        "doing work.")
    add("- **Overlap** between the memory pipe and the compute pipe is what "
        "double buffering buys; zero overlap means they serialise.")
    add("")

    return "\n".join(lines) + "\n"


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
    capture = Path(str(result["capture"]))
    timeline = locate_timeline(capture)
    records = read_records(timeline) if timeline else []
    for record in records:
        groups = decode_groups(record)
        if not groups:
            continue
        group = groups[0]   # same group the summary reports
        pid = record.pid
        if pid not in pids:
            pids[pid] = len(pids) + 1
            events.append({
                "name": "process_name", "ph": "M", "pid": pids[pid],
                "args": {"name": pid},
            })
        index = pids[pid]
        pipe_names = sorted({p for iv in group.intervals for p in iv.pipes})
        for pipe in pipe_names:
            key = (pid, pipe)
            if key in lanes:
                continue
            lanes[key] = len([k for k in lanes if k[0] == pid])
            events.append({
                "name": "thread_name", "ph": "M", "pid": index,
                "tid": lanes[key], "args": {"name": pipe},
            })
        for iv in group.intervals:
            for pipe in iv.pipes:
                events.append({
                    "name": pipe, "ph": "X", "pid": index,
                    "tid": lanes[(pid, pipe)],
                    "ts": round(iv.start / freq, 6),
                    "dur": round(iv.cycles / freq, 6),
                    "args": {"mask": f"0x{iv.mask:02x}"},
                })
        # Idle stretches get their own lane so the gaps are visible.
        idle_lane = len([k for k in lanes if k[0] == pid])
        idle_key = (pid, "__idle__")
        if idle_key not in lanes and any(iv.idle for iv in group.intervals):
            lanes[idle_key] = idle_lane
            events.append({
                "name": "thread_name", "ph": "M", "pid": index,
                "tid": idle_lane, "args": {"name": "IDLE"},
            })
        if idle_key in lanes:
            for iv in group.intervals:
                if iv.idle:
                    events.append({
                        "name": "IDLE", "ph": "X", "pid": index,
                        "tid": lanes[idle_key],
                        "ts": round(iv.start / freq, 6),
                        "dur": round(iv.cycles / freq, 6),
                        "cname": "grey",
                    })

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"displayTimeUnit": "us", "traceEvents": events}, separators=(",", ":")
    ))
    if result.get("aggregate"):
        pass


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
        return 0 if verdict.get("ok") else 1

    freq = args.freq if args.freq is not None else read_freq_mhz(capture)
    try:
        result = summarize(capture, freq)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
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
