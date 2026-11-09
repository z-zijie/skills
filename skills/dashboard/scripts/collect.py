#!/usr/bin/env python3
"""Collect coding-agent session logs into one dashboard snapshot.

Single file, standard library only, Python 3.9+. Local mode scans the session
stores of Claude Code, Codex, and pi. Remote mode runs this same file on another
machine over SSH, piped as source:

    ssh -o BatchMode=yes host "python3 - --remote" < collect.py

The printed JSON is the contract in ../references/data-contract.md. Message
bodies never enter the snapshot: a title is the agent's own session title or the
project directory name, and step/event labels are tool names and file basenames.

Reads are bounded (recent sessions by mtime, head + tail of each file) so a
local sweep stays under a few seconds even with hundreds of sessions.
"""

import argparse
import concurrent.futures
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

SCHEMA_VERSION = 1
DEFAULT_REFRESH_SECONDS = 5
TPS_WINDOW_SECONDS = 90
MAX_SESSIONS_PER_AGENT = 20
HEAD_BYTES = 128 * 1024
TAIL_BYTES = 512 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024
REMOTE_TIMEOUT_SECONDS = 60
REMOTE_CONNECT_TIMEOUT_SECONDS = 8

RUNNING_MS = 120 * 1000
WAITING_MS = 30 * 60 * 1000
DONE_MS = 6 * 60 * 60 * 1000

AGENTS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "pi": "pi",
}

# Model context windows, longest-substring first. Unknown models report a null
# limit instead of guessing one.
MODEL_WINDOWS = [
    ("claude-sonnet-4", 200000),
    ("claude-opus-4", 200000),
    ("claude-haiku", 200000),
    ("claude-3", 200000),
    ("gpt-5", 400000),
    ("gpt-4.1", 1000000),
    ("gpt-4o", 128000),
    ("gpt-4", 128000),
    ("o3", 200000),
    ("o4", 200000),
    ("deepseek", 128000),
    ("gemini-2.5-pro", 1048576),
    ("gemini", 1000000),
    ("qwen", 131072),
    ("kimi", 262144),
    ("glm", 200000),
    ("grok", 256000),
    ("llama", 128000),
    ("mistral", 128000),
    ("minimax", 200000),
]

# Claude Code talks to models whose window the local log does not record; the
# Claude Code default window is the honest estimate there.
CLAUDE_DEFAULT_WINDOW = 200000


# ---------------------------------------------------------------------------
# time / formatting helpers


def _now_ms():
    return int(time.time() * 1000)


def _ms_from_iso(value):
    """Parse an ISO-8601 timestamp (any valid TZ/offset) into epoch ms."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        match = re.match(
            r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?([+-]\d\d:?\d\d)?$", text
        )
        if not match:
            return None
        base, fraction, tz = match.groups()
        if fraction:
            base = base + "." + fraction[:6].ljust(6, "0")
        if tz:
            base = base + tz
        try:
            dt = datetime.fromisoformat(base)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ms_from_epoch(value):
    """Accept epoch seconds, epoch milliseconds, or an ISO string."""
    if isinstance(value, str):
        return _ms_from_iso(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            return None
        return int(value if value > 1e11 else value * 1000)
    return None


def _iso(ms):
    if ms is None:
        return None
    dt = datetime.fromtimestamp(ms / 1000.0, timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value != value or value in (float("inf"), float("-inf")):
        return 0
    return value


def _slug(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "machine"


def _short_host(host):
    return (host or "").split(".")[0] or host


def _platform_name():
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform in ("win32", "cygwin", "msys") or os.name == "nt":
        return "win32"
    return "other"


def _arch():
    machine = (platform.machine() or "").lower()
    return machine or None


def _model_window(model):
    if not isinstance(model, str) or not model:
        return None
    lowered = model.lower()
    for needle, window in MODEL_WINDOWS:
        if needle in lowered:
            return window
    return None


def _tilde(path):
    """Shorten an absolute local path to a home-relative one for display."""
    if not path:
        return None
    home = os.path.expanduser("~")
    if home and path == home:
        return "~"
    if home and path.startswith(home + os.sep):
        return "~/" + path[len(home) + 1:]
    return path


def _basename(path):
    if not isinstance(path, str) or not path:
        return ""
    cleaned = path.rstrip("/\\")
    name = re.split(r"[/\\]", cleaned)[-1]
    return name[:40]


# ---------------------------------------------------------------------------
# bounded JSONL reading


def _iter_jsonl(path):
    """Yield JSON objects from a JSONL file, capped to HEAD + TAIL bytes.

    Only whole lines reach the parser: a record split mid-line is dropped
    rather than fed to json.loads.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    try:
        with open(path, "rb") as handle:
            if size <= HEAD_BYTES + TAIL_BYTES:
                blocks = [handle.read()]
            else:
                head = handle.read(HEAD_BYTES)
                cut = head.rfind(b"\n")
                head = head[:cut + 1] if cut >= 0 else b""
                handle.seek(size - TAIL_BYTES)
                tail = handle.read()
                first = tail.find(b"\n")
                tail = tail[first + 1:] if first >= 0 else b""
                blocks = [head, tail]
    except OSError:
        return
    for block in blocks:
        for raw in block.splitlines():
            if not raw.strip() or len(raw) > MAX_LINE_BYTES:
                continue
            try:
                record = json.loads(raw.decode("utf-8", "replace"))
            except (ValueError, RecursionError):
                continue
            if isinstance(record, dict):
                yield record


def _session_files(root, name_filter, limit):
    """Most recently modified matching files under root, newest first."""
    if not root or not os.path.isdir(root):
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") or d == "."]
        for name in filenames:
            if not name.endswith(".jsonl"):
                continue
            if name_filter is not None and not name_filter(name):
                continue
            path = os.path.join(dirpath, name)
            try:
                found.append((os.stat(path).st_mtime, path))
            except OSError:
                continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found[:limit]]


def _is_rollout(name):
    return name.startswith("rollout-")


# ---------------------------------------------------------------------------
# shared derivations


def _status(last_ms, now_ms, failed=False, ended_ms=None):
    """Status heuristic from the contract: recency, explicit end, error end."""
    if failed:
        return "failed"
    if ended_ms is not None and last_ms is not None and ended_ms >= last_ms:
        return "done"
    if last_ms is None:
        return "idle"
    age = now_ms - last_ms
    if age < RUNNING_MS:
        return "running"
    if age < WAITING_MS:
        return "waiting"
    if age >= DONE_MS:
        return "done"
    return "idle"


def _series(events, anchor_ms, window=TPS_WINDOW_SECONDS):
    """1s buckets of output tokens, ending at anchor_ms; zero-filled."""
    anchor_ms = anchor_ms if anchor_ms is not None else _now_ms()
    start = anchor_ms - (window - 1) * 1000
    buckets = [0.0] * window
    for ts, tokens in events:
        if ts is None or tokens <= 0:
            continue
        index = int((ts - start) // 1000)
        if index < 0 or index >= window:
            continue
        buckets[index] += tokens
    return [[start + i * 1000, round(value, 2)] for i, value in enumerate(buckets)]


def _spread_cumulative(points):
    """Turn (ts, cumulative_output) samples into (ts, delta) points."""
    events = []
    previous_ts = None
    previous_total = 0
    for ts, total in sorted((item for item in points if item[0] is not None),
                            key=lambda item: item[0]):
        if previous_ts is not None and ts > previous_ts:
            delta = total - previous_total
            if delta > 0:
                span = ts - previous_ts
                steps = max(1, int(span // 1000))
                share = float(delta) / steps
                for index in range(steps):
                    events.append((previous_ts + index * 1000, share))
        previous_ts, previous_total = ts, total
    return events


def _tps(series):
    """Displayed TPS: the most recent 1s bucket of tps_series (one number, one clock)."""
    if not series:
        return 0.0
    return float(series[-1][1])


def _tokens_dict(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    return {
        "input": int(round(input_tokens)),
        "output": int(round(output_tokens)),
        "cache_read": int(round(cache_read)),
        "cache_write": int(round(cache_write)),
    }


_TOOL_LABELS = {"bash": "Bash", "shell": "shell", "edit": "Edit", "read": "Read",
                "write": "Write", "apply_patch": "patch", "grep": "Grep",
                "glob": "Glob", "agent": "Agent", "task": "Task"}


def _tool_label(name, arguments):
    """Label from a tool name plus a file basename; never command contents."""
    name = name if isinstance(name, str) and name else "tool"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = None
    if isinstance(arguments, dict):
        for key in ("file_path", "path", "filename", "file", "target"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                base = _basename(value)
                if base:
                    return ("%s %s" % (name, base))[:48]
    known = _TOOL_LABELS.get(name.lower())
    return (known or name)[:48]


def _finalize_steps(raw_steps, started_ms):
    """Waterfall rows from (ts, kind, label), newest tail first kept."""
    steps = sorted((item for item in raw_steps if item[0] is not None),
                   key=lambda item: item[0])[-12:]
    if not steps:
        return []
    base = started_ms if started_ms is not None else steps[0][0]
    out = []
    for index, (ts, kind, label) in enumerate(steps):
        next_ts = steps[index + 1][0] if index + 1 < len(steps) else None
        duration = (next_ts - ts) if next_ts and next_ts > ts else 400
        out.append({
            "label": label,
            "kind": kind,
            "start_ms": int(max(0, ts - base)),
            "dur_ms": int(min(duration, 600000)),
        })
    return out


def _finalize_events(raw_events):
    events = sorted((item for item in raw_events if item[0] is not None),
                    key=lambda item: item[0])[-4:]
    return [{"at": _iso(ts), "kind": kind, "text": label} for ts, kind, label in reversed(events)]


def _project_name(cwd):
    if not isinstance(cwd, str) or not cwd:
        return None
    name = _basename(cwd)
    return name or None


def _context(used, limit):
    return {
        "used": int(round(used or 0)),
        "limit": int(limit) if isinstance(limit, int) and limit > 0 else None,
    }


# Composition of the context after the last request: its prompt tokens plus the
# tokens it produced. `context.used` is their sum, so the inspector's parts and
# its capacity gauge can never contradict each other.
CONTEXT_PART_LABELS = (
    ("input", "Input"),
    ("cache_read", "Cache read"),
    ("cache_write", "Cache write"),
    ("output", "Output"),
)


def _context_parts(usage):
    """Per-request context breakdown; None when no usage was recoverable."""
    if not isinstance(usage, dict):
        return None
    parts = []
    total = 0
    for key, label in CONTEXT_PART_LABELS:
        tokens = int(round(_num(usage.get(key))))
        tokens = max(0, tokens)
        total += tokens
        parts.append({"key": key, "label": label, "tokens": tokens})
    return parts if total > 0 else None


def _context_with_parts(usage, limit, measured=False):
    """Return (context, context_parts) with parts summing to context.used.

    A model-family limit estimate that the observed context contradicts
    (`used > limit`) is dropped to null: a request cannot exceed its window, so
    the estimate was wrong. A window the log states itself is kept as measured.
    """
    parts = _context_parts(usage)
    used = sum(part["tokens"] for part in parts) if parts else 0
    if isinstance(limit, int) and limit > 0 and not measured and used > limit:
        limit = None
    if parts is None:
        return _context(0, limit), None
    return _context(used, limit), parts


def _session_base(machine_id, agent_id, agent_name, raw_id, title, project,
                  cwd, branch, model, no_titles):
    session = {
        "id": "%s:%s:%s" % (agent_id, machine_id, raw_id),
        "machine_id": machine_id,
        "agent": agent_id,
        "agent_name": agent_name,
        "title": title,
        "status": "idle",
    }
    if not no_titles:
        if project:
            session["project"] = project
        if cwd:
            session["cwd"] = _tilde(cwd)
        if branch:
            session["branch"] = branch
    if model:
        session["model"] = model
    return session


def _fallback_title(no_titles, explicit, project, agent_name):
    if explicit:
        return explicit
    if no_titles:
        return "%s session" % agent_name
    if project:
        return project
    return "%s session" % agent_name


# ---------------------------------------------------------------------------
# Claude Code: ~/.claude/projects/**/*.jsonl


def _parse_claude_session(path, machine_id, now_ms, no_titles, title_index):
    agent_id, agent_name = "claude-code", AGENTS["claude-code"]
    first_ms = last_ms = None
    explicit_title = None
    cwd = branch = version = model = None
    input_tokens = output_tokens = cache_read = cache_write = 0
    requests = 0
    assistant_records = 0
    seen_message_ids = set()
    last_usage = None
    cost = None
    sidechains = {}
    agent_tool_calls = 0
    error_last = False
    output_events = []
    last_output_ms = None
    raw_steps = []
    raw_events = []

    for record in _iter_jsonl(path):
        ts = _ms_from_iso(record.get("timestamp"))
        if ts is not None:
            first_ms = ts if first_ms is None or ts < first_ms else first_ms
            last_ms = ts if last_ms is None or ts > last_ms else last_ms
        kind = record.get("type")

        if kind == "ai-title":
            value = record.get("aiTitle")
            if not no_titles and isinstance(value, str) and value.strip():
                explicit_title = value.strip()[:120]
            continue
        if kind == "cost-state":
            value = record.get("totalCostUSD")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cost = float(value)
            continue

        if cwd is None:
            value = record.get("cwd")
            if isinstance(value, str) and value:
                cwd = value
        if branch is None:
            value = record.get("gitBranch")
            if isinstance(value, str) and value:
                branch = value
        if version is None:
            value = record.get("version")
            if isinstance(value, str) and value:
                version = value

        if record.get("isSidechain") and record.get("agentId"):
            agent_key = record["agentId"]
            sidechains[agent_key] = max(sidechains.get(agent_key) or 0, ts or 0)

        message = record.get("message")
        if not isinstance(message, dict):
            continue

        if kind == "assistant":
            assistant_records += 1
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            message_id = message.get("id")
            fresh = message_id not in seen_message_ids
            if fresh:
                seen_message_ids.add(message_id)
                input_tokens += _num(usage.get("input_tokens"))
                output_tokens += _num(usage.get("output_tokens"))
                cache_read += _num(usage.get("cache_read_input_tokens"))
                cache_write += _num(usage.get("cache_creation_input_tokens"))
                requests += 1
                if ts is not None:
                    output_events.append((ts, _num(usage.get("output_tokens"))))
                    if _num(usage.get("output_tokens")) > 0:
                        last_output_ms = max(last_output_ms or 0, ts)
                last_usage = {
                    "input": _num(usage.get("input_tokens")),
                    "cache_read": _num(usage.get("cache_read_input_tokens")),
                    "cache_write": _num(usage.get("cache_creation_input_tokens")),
                    "output": _num(usage.get("output_tokens")),
                }
                if isinstance(message.get("model"), str):
                    model = message["model"]
                error_last = bool(record.get("isApiErrorMessage")) or bool(record.get("apiErrorStatus"))

            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "tool_use":
                        name = block.get("name") or "tool"
                        label = _tool_label(name, block.get("input"))
                        if str(name).lower() in ("agent", "task"):
                            agent_tool_calls += 1
                        if ts is not None:
                            raw_steps.append((ts, "tool", label))
                            raw_events.append((ts, "tool", label))
                    elif block_type == "thinking" and ts is not None:
                        raw_steps.append((ts, "think", "thinking"))
                    elif block_type == "text" and ts is not None:
                        raw_steps.append((ts, "message", "reply"))

    raw_id = os.path.splitext(os.path.basename(path))[0]
    project = _project_name(cwd)
    title = _fallback_title(no_titles, explicit_title, project, agent_name)
    session = _session_base(machine_id, agent_id, agent_name, raw_id, title,
                            project, cwd, branch, model, no_titles)

    anchor = last_output_ms or last_ms or now_ms
    series = _series(output_events, anchor)
    started = first_ms if first_ms is not None else last_ms
    ended = None
    duration = ((last_ms if last_ms is not None else now_ms) - started) if started is not None else 0
    subagent_count = max(len(sidechains), agent_tool_calls)
    active = sum(1 for ts in sidechains.values() if ts and now_ms - ts < RUNNING_MS)

    context, context_parts = _context_with_parts(
        last_usage, _model_window(model) or CLAUDE_DEFAULT_WINDOW)
    session.update({
        "status": _status(last_ms, now_ms, failed=error_last, ended_ms=ended),
        "started_at": _iso(started),
        "updated_at": _iso(last_ms),
        "ended_at": _iso(ended),
        "duration_ms": int(max(0, duration)),
        "requests": int(requests or assistant_records),
        "tokens": _tokens_dict(input_tokens, output_tokens, cache_read, cache_write),
        "context": context,
        "subagents": {"count": int(subagent_count), "active": int(active)},
        "tps": _tps(series),
        "tps_series": series,
        "steps": _finalize_steps(raw_steps, started),
        "events": _finalize_events(raw_events),
    })
    if context_parts:
        session["context_parts"] = context_parts
    if cost is not None:
        session["cost_usd"] = round(cost, 4)
    if version:
        session["agent_version"] = version
    return session


# ---------------------------------------------------------------------------
# Codex: ~/.codex/sessions/**/rollout-*.jsonl


def _codex_title_index(home):
    """Codex keeps agent-generated thread names in session_index.jsonl."""
    path = os.path.join(home, ".codex", "session_index.jsonl")
    index = {}
    if not os.path.isfile(path):
        return index
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle):
                if line_number > 5000:
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                key = record.get("id")
                name = record.get("thread_name")
                if isinstance(key, str) and isinstance(name, str) and name.strip():
                    index[key] = name.strip()[:120]
    except OSError:
        return index
    return index


def _parse_codex_session(path, machine_id, now_ms, no_titles, title_index):
    agent_id, agent_name = "codex", AGENTS["codex"]
    first_ms = last_ms = None
    session_id = cwd = cli_version = None
    model = None
    context_limit = None
    total_usage = None
    previous_total_usage = None
    last_usage = None
    cumulative_points = []
    requests = 0
    ended_ms = None
    failed = False
    last_task = None
    raw_steps = []
    raw_events = []

    for record in _iter_jsonl(path):
        ts = _ms_from_iso(record.get("timestamp"))
        if ts is not None:
            first_ms = ts if first_ms is None or ts < first_ms else first_ms
            last_ms = ts if last_ms is None or ts > last_ms else last_ms
        kind = record.get("type")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}

        if kind == "session_meta":
            session_id = payload.get("id") or session_id
            cwd = payload.get("cwd") or cwd
            cli_version = payload.get("cli_version") or cli_version
            meta_ts = _ms_from_epoch(payload.get("timestamp"))
            if meta_ts is not None:
                first_ms = meta_ts if first_ms is None or meta_ts < first_ms else first_ms
        elif kind == "turn_context":
            if isinstance(payload.get("model"), str):
                model = payload["model"]
        elif kind == "event_msg":
            event_type = payload.get("type")
            if event_type == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    usage = info.get("total_token_usage")
                    turn_usage = info.get("last_token_usage")
                    if isinstance(turn_usage, dict):
                        last_usage = turn_usage
                    elif isinstance(usage, dict) and isinstance(previous_total_usage, dict):
                        # No per-turn row in this build: difference two cumulative rows.
                        last_usage = {
                            "input_tokens": max(0.0, _num(usage.get("input_tokens"))
                                                - _num(previous_total_usage.get("input_tokens"))),
                            "cached_input_tokens": max(0.0, _num(usage.get("cached_input_tokens"))
                                                       - _num(previous_total_usage.get("cached_input_tokens"))),
                            "output_tokens": max(0.0, _num(usage.get("output_tokens"))
                                                 - _num(previous_total_usage.get("output_tokens"))),
                        }
                    if isinstance(usage, dict):
                        total_usage = usage
                        previous_total_usage = usage
                    window = info.get("model_context_window")
                    if isinstance(window, (int, float)) and window > 0:
                        context_limit = int(window)
                    if ts is not None and isinstance(usage, dict):
                        cumulative_points.append((ts, _num(usage.get("output_tokens"))))
            elif event_type == "task_started":
                window = payload.get("model_context_window")
                if isinstance(window, (int, float)) and window > 0:
                    context_limit = int(window)
                ended_ms = None
                failed = False
                last_task = "started"
            elif event_type in ("task_complete", "turn_aborted"):
                completed = _ms_from_epoch(payload.get("completed_at")) or ts
                ended_ms = completed
                last_task = event_type
                failed = event_type == "turn_aborted" and payload.get("reason") not in (None, "interrupted", "user_interrupted")
        elif kind == "response_item":
            item_type = payload.get("type")
            role = payload.get("role")
            if item_type == "message" and role == "assistant":
                requests += 1
            elif item_type == "function_call" and ts is not None:
                label = _tool_label(payload.get("name"), payload.get("arguments"))
                raw_steps.append((ts, "tool", label))
                raw_events.append((ts, "tool", label))
            elif item_type == "reasoning" and ts is not None:
                raw_steps.append((ts, "think", "thinking"))

    raw_id = session_id or os.path.splitext(os.path.basename(path))[0]
    project = _project_name(cwd)
    thread_title = None if no_titles else title_index.get(session_id)
    title = _fallback_title(no_titles, thread_title, project, agent_name)
    session = _session_base(machine_id, agent_id, agent_name, raw_id, title,
                            project, cwd, None, model, no_titles)

    events = _spread_cumulative(cumulative_points)
    last_output_ms = max((ts for ts, tokens in events if tokens > 0), default=None)
    anchor = last_output_ms or last_ms or now_ms
    series = _series(events, anchor)

    if total_usage:
        cached = _num(total_usage.get("cached_input_tokens"))
        tokens = _tokens_dict(
            max(0.0, _num(total_usage.get("input_tokens")) - cached),
            _num(total_usage.get("output_tokens")),
            cached,
            0.0,
        )
    else:
        tokens = _tokens_dict()

    turn_usage = None
    if last_usage:
        cached_turn = _num(last_usage.get("cached_input_tokens"))
        turn_usage = {
            "input": max(0.0, _num(last_usage.get("input_tokens")) - cached_turn),
            "cache_read": cached_turn,
            "cache_write": 0.0,
            "output": _num(last_usage.get("output_tokens")),
        }
    context, context_parts = _context_with_parts(
        turn_usage, context_limit or _model_window(model), measured=bool(context_limit))
    started = first_ms if first_ms is not None else last_ms
    duration = ((ended_ms or last_ms or now_ms) - started) if started is not None else 0

    session.update({
        "status": _status(last_ms, now_ms, failed=failed, ended_ms=ended_ms),
        "started_at": _iso(started),
        "updated_at": _iso(last_ms),
        "ended_at": _iso(ended_ms),
        "duration_ms": int(max(0, duration)),
        "requests": int(requests),
        "tokens": tokens,
        "context": context,
        "subagents": {"count": 0, "active": 0},
        "tps": _tps(series),
        "tps_series": series,
        "steps": _finalize_steps(raw_steps, started),
        "events": _finalize_events(raw_events),
    })
    if context_parts:
        session["context_parts"] = context_parts
    if cli_version:
        session["agent_version"] = cli_version
    return session


# ---------------------------------------------------------------------------
# pi: ~/.pi/agent/sessions/**/*.jsonl


def _parse_pi_session(path, machine_id, now_ms, no_titles, title_index):
    agent_id, agent_name = "pi", AGENTS["pi"]
    first_ms = last_ms = None
    session_id = cwd = None
    model = None
    input_tokens = output_tokens = cache_read = cache_write = 0
    requests = 0
    cost = 0.0
    last_usage = None
    error_last = False
    latencies = []
    output_events = []
    last_output_ms = None
    raw_steps = []
    raw_events = []

    for record in _iter_jsonl(path):
        ts = _ms_from_iso(record.get("timestamp"))
        if ts is not None:
            first_ms = ts if first_ms is None or ts < first_ms else first_ms
            last_ms = ts if last_ms is None or ts > last_ms else last_ms
        kind = record.get("type")

        if kind == "session":
            session_id = record.get("id") or session_id
            cwd = record.get("cwd") or cwd
            continue
        if kind == "model_change":
            if isinstance(record.get("modelId"), str):
                model = record["modelId"]
            continue
        if kind != "message":
            continue

        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        role = message.get("role")
        message_ts = _ms_from_epoch(message.get("timestamp")) or ts

        if role == "assistant":
            requests += 1
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            input_tokens += _num(usage.get("input"))
            output_tokens += _num(usage.get("output"))
            cache_read += _num(usage.get("cacheRead"))
            cache_write += _num(usage.get("cacheWrite"))
            cost += _num((usage.get("cost") if isinstance(usage.get("cost"), dict) else {}).get("total"))
            last_usage = {
                "input": _num(usage.get("input")),
                "cache_read": _num(usage.get("cacheRead")),
                "cache_write": _num(usage.get("cacheWrite")),
                "output": _num(usage.get("output")),
            }
            if message_ts is not None:
                output_events.append((message_ts, _num(usage.get("output"))))
                if _num(usage.get("output")) > 0:
                    last_output_ms = max(last_output_ms or 0, message_ts)
            if isinstance(message.get("model"), str):
                model = message["model"]
            duration = _num(message.get("durationMs"))
            if duration > 0:
                latencies.append(duration)
            error_last = bool(message.get("errorMessage")) or message.get("stopReason") == "error"

            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "toolCall":
                        label = _tool_label(block.get("name"), block.get("arguments"))
                        if message_ts is not None:
                            raw_steps.append((message_ts, "tool", label))
                            raw_events.append((message_ts, "tool", label))
                    elif block_type == "thinking" and message_ts is not None:
                        raw_steps.append((message_ts, "think", "thinking"))
                    elif block_type == "text" and message_ts is not None:
                        raw_steps.append((message_ts, "message", "reply"))

    raw_id = session_id or os.path.splitext(os.path.basename(path))[0]
    project = _project_name(cwd)
    title = _fallback_title(no_titles, None, project, agent_name)
    session = _session_base(machine_id, agent_id, agent_name, raw_id, title,
                            project, cwd, None, model, no_titles)

    anchor = last_output_ms or last_ms or now_ms
    series = _series(output_events, anchor)
    started = first_ms if first_ms is not None else last_ms
    duration = ((last_ms if last_ms is not None else now_ms) - started) if started is not None else 0

    context, context_parts = _context_with_parts(last_usage, _model_window(model))
    session.update({
        "status": _status(last_ms, now_ms, failed=error_last, ended_ms=None),
        "started_at": _iso(started),
        "updated_at": _iso(last_ms),
        "ended_at": None,
        "duration_ms": int(max(0, duration)),
        "requests": int(requests),
        "tokens": _tokens_dict(input_tokens, output_tokens, cache_read, cache_write),
        "context": context,
        "subagents": {"count": 0, "active": 0},
        "tps": _tps(series),
        "tps_series": series,
        "steps": _finalize_steps(raw_steps, started),
        "events": _finalize_events(raw_events),
    })
    if context_parts:
        session["context_parts"] = context_parts
    if cost > 0:
        session["cost_usd"] = round(cost, 4)
    if latencies:
        session["latency_ms"] = int(round(sum(latencies) / len(latencies)))
    return session


# ---------------------------------------------------------------------------
# machine assembly


def _machine_identity(hostname, machine_id=None):
    short = _short_host(hostname)
    return machine_id or os.environ.get("DASHBOARD_MACHINE_ID") or _slug(short), short


def _agent_summary(agent_id, sessions):
    name = AGENTS.get(agent_id, agent_id)
    if not sessions:
        return None
    running = [session for session in sessions if session["status"] == "running"]
    requests = sum(int(session.get("requests") or 0) for session in sessions)
    tokens = sum(
        int((session.get("tokens") or {}).get("input") or 0)
        + int((session.get("tokens") or {}).get("output") or 0)
        + int((session.get("tokens") or {}).get("cache_read") or 0)
        + int((session.get("tokens") or {}).get("cache_write") or 0)
        for session in sessions
    )
    tps = sum(float(session.get("tps") or 0) for session in running)
    newest = max(sessions, key=lambda s: s.get("updated_at") or "")
    summary = {
        "id": agent_id,
        "name": name,
        "status": "active" if running else "idle",
        "sessions_active": len(running),
        "requests": requests,
        "tokens": tokens,
        "tps": round(tps, 1),
    }
    version = newest.get("agent_version")
    if version:
        summary["version"] = str(version)
    if newest.get("model"):
        summary["model"] = newest["model"]
    return summary


def _machine_sources(home):
    return [
        ("claude-code", os.path.join(home, ".claude", "projects"), None, _parse_claude_session),
        ("codex", os.path.join(home, ".codex", "sessions"), _is_rollout, _parse_codex_session),
        ("pi", os.path.join(home, ".pi", "agent", "sessions"), None, _parse_pi_session),
    ]


def collect_machine(home=None, machine_id=None, now_ms=None,
                    max_sessions=MAX_SESSIONS_PER_AGENT, no_titles=False,
                    hostname=None):
    """Scan one machine's agent stores. Returns (machine, sessions)."""
    home = os.path.expanduser(home or "~")
    now_ms = now_ms if now_ms is not None else _now_ms()
    hostname = hostname or socket.gethostname()
    resolved_id, short = _machine_identity(hostname, machine_id)

    machine = {
        "id": resolved_id,
        "name": short,
        "host": hostname,
        "platform": _platform_name(),
        "arch": _arch(),
        "online": True,
        "last_seen": None,
        "agents": [],
    }

    title_index = _codex_title_index(home) if not no_titles else {}
    sessions = []
    for agent_id, root, name_filter, parser in _machine_sources(home):
        files = _session_files(root, name_filter, max_sessions)
        parsed = []
        for path in files:
            try:
                session = parser(path, resolved_id, now_ms, no_titles, title_index)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if session:
                parsed.append(session)
        if not parsed:
            continue
        parsed.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        summary = _agent_summary(agent_id, parsed)
        if summary is not None:
            machine["agents"].append(summary)
        sessions.extend(parsed)

    sessions.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return machine, sessions


def _aggregate(machines, sessions, now_ms):
    online = sum(1 for machine in machines if machine.get("online"))
    agents = sum(len(machine.get("agents") or []) for machine in machines)
    active = sum(1 for session in sessions if session.get("status") == "running")
    requests = sum(int(session.get("requests") or 0) for session in sessions)
    running = [session for session in sessions if session.get("status") == "running"]
    tps = round(sum(float(session.get("tps") or 0) for session in running), 1)
    buckets = {}
    for session in running:
        for ts, value in session.get("tps_series") or []:
            if isinstance(ts, int) and isinstance(value, (int, float)):
                buckets[ts] = buckets.get(ts, 0.0) + float(value)
    start = now_ms - (TPS_WINDOW_SECONDS - 1) * 1000
    series = [[start + index * 1000, round(buckets.get(start + index * 1000, 0.0), 2)]
              for index in range(TPS_WINDOW_SECONDS)]
    return {
        "machines_online": online,
        "agents": agents,
        "sessions_active": active,
        "sessions_total": len(sessions),
        "requests_total": requests,
        "tps": tps,
        "tps_series": series,
    }


# ---------------------------------------------------------------------------
# remote machines over SSH


def _validate_host(host):
    host = (host or "").strip()
    if not host or host.startswith("-"):
        raise ValueError("invalid host %r" % host)
    if not re.match(r"^[A-Za-z0-9_.:@\[\]-]+$", host):
        raise ValueError("invalid host %r" % host)
    return host


def _self_source():
    try:
        path = os.path.abspath(__file__)
    except NameError:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _cache_dir(home):
    return os.environ.get("DASHBOARD_CACHE_DIR") or os.path.join(home, ".cache", "dashboard")


def _load_host_cache(home):
    path = os.path.join(_cache_dir(home), "hosts.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_host_cache(home, cache):
    path = os.path.join(_cache_dir(home), "hosts.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2, sort_keys=True)
    except OSError:
        return


def _remote_command(no_titles, max_sessions):
    parts = ["python3", "-", "--remote", "--max-sessions", str(int(max_sessions))]
    if no_titles:
        parts.append("--no-titles")
    return " ".join(parts)


def collect_remote(host, source, no_titles=False,
                   max_sessions=MAX_SESSIONS_PER_AGENT,
                   timeout=REMOTE_TIMEOUT_SECONDS, now_ms=None, cache=None):
    """Run collect.py on `host` over SSH. Returns (machine, sessions)."""
    host = _validate_host(host)
    now_ms = now_ms if now_ms is not None else _now_ms()
    machine_id = _slug(host)
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=%d" % REMOTE_CONNECT_TIMEOUT_SECONDS,
        host,
        _remote_command(no_titles, max_sessions),
    ]
    error = None
    try:
        result = subprocess.run(
            command,
            input=source.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if result.returncode != 0:
            error = (result.stderr.decode("utf-8", "replace").strip().splitlines() or ["ssh failed"])[-1]
        else:
            payload = json.loads(result.stdout.decode("utf-8", "replace"))
            remote_machine = (payload.get("machines") or [None])[0]
            remote_sessions = payload.get("sessions") or []
            if not isinstance(remote_machine, dict):
                error = "remote returned no machine"
            else:
                previous_id = remote_machine.get("id")
                remote_machine["id"] = machine_id
                remote_machine["name"] = host
                remote_machine["host"] = host
                remote_machine["online"] = True
                remote_machine["last_seen"] = None
                for session in remote_sessions:
                    parts = str(session.get("id") or "").split(":", 2)
                    if len(parts) == 3:
                        session["id"] = "%s:%s:%s" % (parts[0], machine_id, parts[2])
                    session["machine_id"] = machine_id
                return remote_machine, remote_sessions, None
    except subprocess.TimeoutExpired:
        error = "ssh timed out after %ds" % timeout
    except (OSError, ValueError) as exc:
        error = str(exc)

    cached = (cache or {}).get(host) or {}
    agents = []
    for agent in cached.get("agents") or []:
        if isinstance(agent, dict):
            known = dict(agent)
            known["status"] = "offline"
            known["sessions_active"] = 0
            known["tps"] = 0
            agents.append(known)
    machine = {
        "id": machine_id,
        "name": host,
        "host": host,
        "platform": cached.get("platform") or "other",
        "arch": cached.get("arch"),
        "online": False,
        "last_seen": cached.get("last_seen"),
        "agents": agents,
        "error": error or "unreachable",
    }
    return machine, [], error


def collect_snapshot(hosts=(), home=None, now_ms=None,
                     max_sessions=MAX_SESSIONS_PER_AGENT, no_titles=False,
                     refresh_seconds=DEFAULT_REFRESH_SECONDS, machine_id=None,
                     timeout=REMOTE_TIMEOUT_SECONDS):
    """Build the contract snapshot for this machine plus any remote hosts."""
    home = os.path.expanduser(home or "~")
    now_ms = now_ms if now_ms is not None else _now_ms()
    machines = []
    sessions = []

    local_machine, local_sessions = collect_machine(
        home=home, machine_id=machine_id, now_ms=now_ms,
        max_sessions=max_sessions, no_titles=no_titles,
    )
    machines.append(local_machine)
    sessions.extend(local_sessions)

    host_list = [host.strip() for host in hosts if host and host.strip()]
    if host_list:
        source = _self_source()
        if not source:
            raise RuntimeError("collect.py must run from a file to collect remote hosts")
        cache = _load_host_cache(home)
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(host_list))) as pool:
            futures = {
                pool.submit(collect_remote, host, source, no_titles, max_sessions,
                            timeout, now_ms, cache): host
                for host in host_list
            }
            for future, host in [(future, futures[future]) for future in futures]:
                try:
                    results[host] = future.result()
                except (OSError, ValueError, RuntimeError) as exc:
                    results[host] = ({
                        "id": _slug(host), "name": host, "host": host,
                        "platform": "other", "arch": None, "online": False,
                        "last_seen": (cache.get(host) or {}).get("last_seen"),
                        "agents": [], "error": str(exc),
                    }, [], str(exc))
        for host in host_list:
            machine, remote_sessions, error = results[host]
            if not error:
                cache[host] = {
                    "platform": machine.get("platform"),
                    "arch": machine.get("arch"),
                    "last_seen": _iso(now_ms),
                    "agents": machine.get("agents") or [],
                }
            machines.append(machine)
            sessions.extend(remote_sessions)
        _save_host_cache(home, cache)

    sessions.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "live",
        "generated_at": _iso(now_ms),
        "refresh_seconds": int(refresh_seconds),
        "aggregate": _aggregate(machines, sessions, now_ms),
        "machines": machines,
        "sessions": sessions,
    }


# ---------------------------------------------------------------------------
# CLI


def _print_summary(snapshot):
    aggregate = snapshot.get("aggregate") or {}
    print("dashboard snapshot · %s · mode=%s · %d machine%s / %d agent%s / %d session%s / %.1f tps"
          % (snapshot["generated_at"], snapshot["mode"], len(snapshot["machines"]),
             "" if len(snapshot["machines"]) == 1 else "s",
             aggregate.get("agents", 0), "" if aggregate.get("agents") == 1 else "s",
             len(snapshot["sessions"]), "" if len(snapshot["sessions"]) == 1 else "s",
             aggregate.get("tps", 0)))
    for machine in snapshot["machines"]:
        state = "online" if machine.get("online") else "offline"
        print("  machine %-16s %-8s agents=%d sessions=%d%s"
              % (machine.get("id"), state, len(machine.get("agents") or []),
                 sum(1 for s in snapshot["sessions"] if s.get("machine_id") == machine.get("id")),
                 "" if machine.get("online") else "  last_seen=%s" % (machine.get("last_seen") or "unknown")))
    for session in snapshot["sessions"]:
        print("    %-32s %-8s %6.1fs  %s" % (
            session.get("id", "")[:32], session.get("status", ""),
            (session.get("duration_ms") or 0) / 1000.0, session.get("title", "")))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Collect coding-agent session logs into a dashboard snapshot.",
        epilog="Pipe this file to a remote machine for --remote collection.",
    )
    parser.add_argument("--json", action="store_true", help="print the snapshot JSON")
    parser.add_argument("--remote", action="store_true", help="collect only this machine (used over SSH)")
    parser.add_argument("--hosts", default="", help="comma-separated SSH hosts to collect")
    parser.add_argument("--refresh", type=int, default=DEFAULT_REFRESH_SECONDS,
                        help="suggested refresh seconds (default %(default)s)")
    parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS_PER_AGENT,
                        help="sessions kept per agent (default %(default)s)")
    parser.add_argument("--no-titles", action="store_true",
                        help="replace titles with '<agent> session' and omit project/cwd/branch")
    parser.add_argument("--machine-id", default=None, help="override this machine's id")
    parser.add_argument("--home", default=None, help="alternate home directory (tests/fixtures)")
    parser.add_argument("--now", default=None, help="freeze the clock at an ISO timestamp (tests)")
    parser.add_argument("--timeout", type=int, default=REMOTE_TIMEOUT_SECONDS,
                        help="per-host SSH timeout in seconds (default %(default)s)")
    args = parser.parse_args(argv)

    hosts = [] if args.remote else [host for host in args.hosts.split(",") if host.strip()]
    now_ms = _ms_from_iso(args.now) if args.now else None
    if args.now and now_ms is None:
        parser.error("--now must be an ISO-8601 timestamp")

    snapshot = collect_snapshot(
        hosts=hosts, home=args.home, now_ms=now_ms,
        max_sessions=max(1, args.max_sessions), no_titles=args.no_titles,
        refresh_seconds=max(1, args.refresh), machine_id=args.machine_id,
        timeout=max(5, args.timeout),
    )
    if args.json or args.remote:
        print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
    else:
        _print_summary(snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
