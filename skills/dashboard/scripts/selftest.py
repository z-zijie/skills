#!/usr/bin/env python3
"""Verify the dashboard skill without network, SSH, or a browser.

Covers: timestamp/bounded-reader helpers, the three synthetic agent fixtures and
their parsed sessions, the snapshot contract (fixtures + demo.json), static asset
references under web/, the SKILL.md doc pointers, and a loopback smoke test of
the HTTP server. Exit code 0 when every check passes, 1 when any check fails.
"""

import importlib.util
import json
import os
import re
import select
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
FIXTURES_DIR = os.path.join(SKILL_DIR, "tests", "fixtures")
FIXTURE_HOME = os.path.join(FIXTURES_DIR, "home")
WEB_DIR = os.path.join(SKILL_DIR, "web")
REFERENCES_DIR = os.path.join(SKILL_DIR, "references")
DEMO_FILE = os.path.join(WEB_DIR, "data", "demo.json")
SKILL_FILE = os.path.join(SKILL_DIR, "SKILL.md")

FIXED_NOW = "2026-11-08T18:00:00.000Z"
SESSION_STATUSES = {"running", "waiting", "done", "failed", "idle"}
AGENT_STATUSES = {"active", "idle", "offline"}
PLATFORMS = {"darwin", "linux", "win32", "other"}
STEP_KINDS = {"think", "tool", "message", "subagent"}
PRIVATE_MARKERS = [
    "PRIVATE-MESSAGE-BODY",
    "PRIVATE-COMMAND",
    "PRIVATE-ASSISTANT-TEXT",
    "PRIVATE-THINKING",
    "PRIVATE-SIDECHAIN-TEXT",
]

CHECKS = []
WARNINGS = []
FAILURES = []
PASSED = []
COL = None
SNAPSHOT = None
DEMO = None


# ---------------------------------------------------------------------------
# small assertion helpers


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def equal(actual, expected, label):
    expect(actual == expected, "%s: expected %r, got %r" % (label, expected, actual))


def is_int(value, label):
    expect(isinstance(value, int) and not isinstance(value, bool), "%s must be an int, got %r" % (label, value))


def is_number(value, label):
    expect(isinstance(value, (int, float)) and not isinstance(value, bool),
           "%s must be a number, got %r" % (label, value))


def is_iso(value, label):
    expect(isinstance(value, str) and COL._ms_from_iso(value) is not None,
           "%s must be an ISO-8601 timestamp, got %r" % (label, value))


def check(name):
    def register(function):
        CHECKS.append((name, function))
        return function
    return register


def warn(message):
    WARNINGS.append(message)


def load_collector():
    global COL
    path = os.path.join(SCRIPTS_DIR, "collect.py")
    spec = importlib.util.spec_from_file_location("dashboard_collect", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    COL = module
    return module


def fixture_snapshot():
    global SNAPSHOT
    if SNAPSHOT is None:
        SNAPSHOT = COL.collect_snapshot(
            home=FIXTURE_HOME,
            machine_id="demo-host",
            now_ms=COL._ms_from_iso(FIXED_NOW),
        )
    return SNAPSHOT


def load_demo():
    global DEMO
    if DEMO is None and os.path.isfile(DEMO_FILE):
        with open(DEMO_FILE, "r", encoding="utf-8") as handle:
            DEMO = json.load(handle)
    return DEMO


def sessions_by_agent(snapshot, agent):
    return [session for session in snapshot["sessions"] if session["agent"] == agent]


def session_for(snapshot, suffix):
    for session in snapshot["sessions"]:
        if session["id"].endswith(suffix):
            return session
    raise AssertionError("fixture session %r missing from snapshot" % suffix)


# ---------------------------------------------------------------------------
# checks: collector helpers


@check("timestamp helpers")
def timestamp_helpers():
    base = COL._ms_from_iso("2026-11-08T18:00:00.000Z")
    is_int(base, "ISO parse result")
    equal(COL._ms_from_iso("2026-11-08T18:00:00+02:00"), base - 7200000, "offset ISO")
    equal(COL._ms_from_epoch(1794160800), base, "epoch seconds")
    equal(COL._ms_from_epoch(1794160800000), base, "epoch milliseconds")
    equal(COL._ms_from_iso("not a timestamp"), None, "invalid ISO")
    equal(COL._ms_from_iso(None), None, "missing ISO")
    expect(COL._iso(base).endswith("Z"), "_iso should render UTC with a Z suffix")
    equal(COL._iso(base), "2026-11-08T18:00:00.000Z", "ISO round trip")
    return "Z/offset/epoch inputs agree"


@check("bounded JSONL reader keeps whole lines")
def bounded_reader():
    path = os.path.join(FIXTURES_DIR, "bounded", "long-lines.jsonl")
    total = 0
    with open(path, "r", encoding="utf-8") as handle:
        total = len([line for line in handle if line.strip()])
    saved = (COL.HEAD_BYTES, COL.TAIL_BYTES)
    COL.HEAD_BYTES, COL.TAIL_BYTES = 700, 200
    try:
        records = list(COL._iter_jsonl(path))
    finally:
        COL.HEAD_BYTES, COL.TAIL_BYTES = saved
    expect(records, "bounded read returned no records")
    expect(len(records) < total,
           "bounded read should drop the records between the head and tail, kept %d/%d" % (len(records), total))
    for record in records:
        expect(isinstance(record, dict), "bounded read yielded a non-object")
        expect("type" in record and "n" in record, "bounded read yielded a truncated record: %r" % (record,))
    return "%d/%d whole records, no partial line parsed" % (len(records), total)


@check("oversized JSONL record is skipped")
def oversized_record():
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "small", "n": 1}) + "\n")
        handle.write(json.dumps({"type": "big", "n": 2, "pad": "x" * 400}) + "\n")
        path = handle.name
    saved = COL.MAX_LINE_BYTES
    COL.MAX_LINE_BYTES = 120
    try:
        records = list(COL._iter_jsonl(path))
    finally:
        COL.MAX_LINE_BYTES = saved
        os.unlink(path)
    equal(len(records), 1, "records kept under MAX_LINE_BYTES")
    equal(records[0].get("type"), "small", "kept record")
    return "records over MAX_LINE_BYTES never reach json.loads"


@check("malformed JSONL lines are skipped")
def malformed_lines():
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as handle:
        handle.write("not json at all\n")
        handle.write(json.dumps({"type": "ok", "n": 1}) + "\n")
        handle.write('{"type": "cut", "n":\n')
        path = handle.name
    try:
        records = list(COL._iter_jsonl(path))
    finally:
        os.unlink(path)
    equal(len(records), 1, "valid records kept")
    equal(records[0].get("type"), "ok", "record kept")
    return "bad lines dropped, valid records survive"


# ---------------------------------------------------------------------------
# checks: fixtures


@check("fixtures are synthetic and tiny")
def fixtures_small():
    paths = []
    for root, _dirs, files in os.walk(FIXTURES_DIR):
        for name in files:
            paths.append(os.path.join(root, name))
    expect(paths, "no fixture files found")
    for path in paths:
        size = os.path.getsize(path)
        expect(size <= 8 * 1024, "fixture %s is %d bytes (keep fixtures tiny)" % (path, size))
        with open(path, "rb") as handle:
            data = handle.read()
        expect(b"PRIVATE-" in data or b"fixture" in data or path.endswith(".jsonl"),
               "fixture %s does not look synthetic" % path)
    return "%d synthetic files, largest %d bytes" % (
        len(paths), max(os.path.getsize(path) for path in paths))


@check("collector fixture snapshot")
def fixture_counts():
    snapshot = fixture_snapshot()
    equal(snapshot["schema_version"], 1, "schema_version")
    equal(snapshot["mode"], "live", "mode")
    equal(len(snapshot["machines"]), 1, "machines")
    equal(snapshot["machines"][0]["id"], "demo-host", "machine id")
    equal(len(snapshot["sessions"]), 6, "sessions")
    equal(snapshot["aggregate"]["agents"], 3, "aggregate agents")
    equal(snapshot["aggregate"]["sessions_total"], 6, "aggregate sessions_total")
    equal(snapshot["aggregate"]["sessions_active"], 2, "aggregate sessions_active (running only)")
    equal(snapshot["aggregate"]["requests_total"], 9, "aggregate requests_total")
    equal(snapshot["aggregate"]["tps"], 64.0, "aggregate.tps is the sum of running sessions")
    agents = {agent["id"]: agent for agent in snapshot["machines"][0]["agents"]}
    equal(sorted(agents), ["claude-code", "codex", "pi"], "agent ids")
    equal(agents["claude-code"]["sessions_active"], 1, "claude running sessions")
    equal(agents["pi"]["status"], "idle", "pi agent status")
    return "1 machine / 3 agents / 6 sessions / 9 requests"


@check("Claude Code session parsing")
def claude_parsing():
    snapshot = fixture_snapshot()
    session = session_for(snapshot, "11111111-1111-4111-8111-111111111111")
    equal(session["agent"], "claude-code", "agent")
    equal(session["title"], "Harden fixture parser", "ai-title wins")
    equal(session["project"], "atlas-web", "project from cwd")
    equal(session["branch"], "fix/parsers", "branch")
    equal(session["status"], "running", "status")
    equal(session["requests"], 4, "requests count unique message ids")
    equal(session["tokens"], {"input": 3050, "output": 530, "cache_read": 13300, "cache_write": 500},
          "token totals")
    equal(session["context"]["used"], 5810, "context.used from last request prompt + output")
    equal(session["context"]["limit"], 200000, "context.limit default window")
    equal(session["subagents"], {"count": 1, "active": 1}, "sidechain subagent")
    equal(session["duration_ms"], 30000, "duration")
    equal(session["tps"], 60.0, "tps is the latest 1s bucket")
    equal(len(session["tps_series"]), 90, "tps_series buckets")
    equal(session["cost_usd"], 1.2345, "cost_usd")
    expect(len(session["steps"]) >= 3, "tool/think steps should be recorded")
    expect(session["steps"][0]["kind"] in STEP_KINDS, "step kind")
    expect(any(event["text"].startswith("Read ") for event in session["events"]),
           "tool event label should name the tool and file, got %r" % (session["events"],))
    equal([step["label"] for step in session["steps"] if step["kind"] == "tool"], ["Read auth.ts"],
          "tool step label")
    return "ai-title, usage dedupe, sidechain, steps/events"


@check("Codex session parsing")
def codex_parsing():
    snapshot = fixture_snapshot()
    done = session_for(snapshot, "55555555-5555-4555-8555-555555555555")
    equal(done["agent"], "codex", "agent")
    equal(done["title"], "Freeze the eval snapshot", "thread_name from session_index")
    equal(done["status"], "done", "task_complete ends the session")
    is_iso(done["ended_at"], "ended_at")
    equal(done["requests"], 1, "assistant message count")
    equal(done["tokens"], {"input": 600, "output": 300, "cache_read": 400, "cache_write": 0},
          "cumulative token usage")
    equal(done["context"], {"used": 900, "limit": 258400}, "context from model_context_window")
    equal(done["agent_version"], "0.167.0", "cli_version")
    running = session_for(snapshot, "66666666-6666-4666-8666-666666666666")
    equal(running["status"], "running", "open task is running")
    equal(running["ended_at"], None, "no ended_at while running")
    equal(running["title"], "codex-live", "project fallback title")
    equal(running["tps"], 4.0, "tps is the latest 1s bucket")
    tool_steps = [step for step in done["steps"] if step["kind"] == "tool"]
    expect(tool_steps, "function_call should leave a tool step")
    equal(tool_steps[0]["label"], "shell", "function_call label")
    expect(done["events"] and done["events"][0]["kind"] == "tool", "function_call should leave an event")
    return "session_meta, token_count, task_complete, title index"


@check("pi session parsing")
def pi_parsing():
    snapshot = fixture_snapshot()
    session = session_for(snapshot, "77777777-7777-4777-8777-777777777777")
    equal(session["agent"], "pi", "agent")
    equal(session["title"], "pi-demo", "project fallback title")
    equal(session["status"], "waiting", "status from last activity")
    equal(session["model"], "deepseek-flash", "model_change")
    equal(session["requests"], 1, "assistant message count")
    equal(session["tokens"], {"input": 800, "output": 150, "cache_read": 2000, "cache_write": 0},
          "usage totals")
    equal(session["context"]["limit"], 128000, "model window estimate")
    equal(session["context"]["used"], 2950, "context.used from last request prompt + output")
    equal(session["tps"], 150.0, "tps is the latest 1s bucket")
    equal(session["latency_ms"], 1500, "mean request latency")
    equal(session["cost_usd"], 0.011, "cost from usage.cost.total")
    return "session, model_change, usage, cost, latency"


@check("displayed tps equals the last series bucket")
def tps_consistency():
    snapshot = fixture_snapshot()
    for session in snapshot["sessions"]:
        equal(len(session["tps_series"]), 90, "%s tps_series buckets" % session["agent"])
        equal(session["tps"], session["tps_series"][-1][1],
              "%s: tps must be the last tps_series bucket" % session["agent"])
    running = [session for session in snapshot["sessions"] if session["status"] == "running"]
    equal(snapshot["aggregate"]["tps"], round(sum(session["tps"] for session in running), 1),
          "aggregate.tps must be the sum of running sessions")
    equal(session_for(snapshot, "22222222-2222-4222-8222-222222222222")["tps"], 40.0,
          "a finished session keeps its last observed rate")
    equal(session_for(snapshot, "33333333-3333-4333-8333-333333333333")["tps"], 0.0,
          "a session with no generation reports 0.0")
    return "6/6 fixtures and the fleet aggregate agree with the series"


@check("context composition comes from the last request")
def context_composition():
    snapshot = fixture_snapshot()
    expected = {
        "11111111-1111-4111-8111-111111111111": {
            "input": 950, "cache_read": 4800, "cache_write": 0, "output": 60},
        "33333333-3333-4333-8333-333333333333": {
            "input": 300, "cache_read": 0, "cache_write": 0, "output": 0},
        "22222222-2222-4222-8222-222222222222": {
            "input": 400, "cache_read": 800, "cache_write": 0, "output": 40},
        "55555555-5555-4555-8555-555555555555": {
            "input": 900, "cache_read": 0, "cache_write": 0, "output": 0},
        "66666666-6666-4666-8666-666666666666": {
            "input": 1600, "cache_read": 0, "cache_write": 0, "output": 0},
        "77777777-7777-4777-8777-777777777777": {
            "input": 800, "cache_read": 2000, "cache_write": 0, "output": 150},
    }
    for session in snapshot["sessions"]:
        suffix = session["id"].rsplit(":", 1)[-1]
        parts = {part["key"]: part["tokens"] for part in session.get("context_parts") or []}
        expect(parts, "%s fixture session lacks context_parts" % session["agent"])
        equal(parts, expected[suffix], "context_parts for %s" % session["agent"])
        total = sum(parts.values())
        expect(abs(total - session["context"]["used"]) <= 1,
               "%s: context_parts sum %d != context.used %d"
               % (session["agent"], total, session["context"]["used"]))

    # Session tokens are cumulative; the last request's parts must not reuse them.
    claude_fixture = session_for(snapshot, "11111111-1111-4111-8111-111111111111")
    claude_parts = {part["key"]: part["tokens"] for part in claude_fixture["context_parts"]}
    equal(claude_fixture["tokens"]["cache_read"], 13300, "fixture cumulative Claude cache_read")
    expect(claude_parts["cache_read"] != claude_fixture["tokens"]["cache_read"],
           "Claude cumulative cache must not leak into context_parts")
    codex_fixture = session_for(snapshot, "55555555-5555-4555-8555-555555555555")
    codex_parts = {part["key"]: part["tokens"] for part in codex_fixture["context_parts"]}
    equal(codex_fixture["tokens"]["cache_read"], 400, "fixture cumulative Codex cache_read")
    expect(codex_parts["cache_read"] != codex_fixture["tokens"]["cache_read"],
           "Codex cumulative cache must not leak into context_parts")

    # A context larger than an estimated window means the estimate was wrong.
    estimated, estimated_parts = COL._context_with_parts({"input": 300000, "output": 0}, 200000)
    equal(estimated["limit"], None, "contradicted limit estimate is dropped")
    equal(estimated["used"], 300000, "used survives the dropped estimate")
    equal(sum(part["tokens"] for part in estimated_parts), 300000, "parts sum with dropped estimate")
    measured, _ = COL._context_with_parts({"input": 300000, "output": 0}, 258400, measured=True)
    equal(measured["limit"], 258400, "a window the log states is kept")
    return "6/6 fixture sessions: parts sum to context.used, no cumulative totals, sane limits"


@check("status inference covers the enum")
def status_inference():
    snapshot = fixture_snapshot()
    statuses = {session["status"] for session in snapshot["sessions"]}
    for expected in ("running", "waiting", "done", "failed"):
        expect(expected in statuses, "fixture snapshot lacks a %r session" % expected)
    failed = session_for(snapshot, "33333333-3333-4333-8333-333333333333")
    equal(failed["status"], "failed", "API error marks the session failed")
    equal(failed["title"], "flaky-project", "project fallback title without ai-title")
    return "running/waiting/done/failed inferred from logs"


@check("message bodies never enter the snapshot")
def privacy():
    blob = json.dumps(fixture_snapshot(), ensure_ascii=False)
    for marker in PRIVATE_MARKERS:
        expect(marker not in blob, "private fixture marker %r leaked into the snapshot" % marker)
    session = session_for(fixture_snapshot(), "11111111-1111-4111-8111-111111111111")
    for step in session["steps"]:
        expect(len(step["label"]) <= 48, "step label is longer than a tool/file name")
    expect("PRIVATE" not in session["title"], "title must not contain body text")
    return "prompt/tool/answer markers absent; labels are tool + basename"


@check("--no-titles strips identifying fields")
def no_titles():
    snapshot = COL.collect_snapshot(home=FIXTURE_HOME, machine_id="demo-host",
                                    now_ms=COL._ms_from_iso(FIXED_NOW), no_titles=True)
    expect(snapshot["sessions"], "no-titles snapshot is empty")
    for session in snapshot["sessions"]:
        expect(session["title"].endswith(" session"), "title not anonymised: %r" % session["title"])
        for key in ("project", "cwd", "branch"):
            expect(key not in session, "%s should be omitted with --no-titles" % key)
        expect("PRIVATE" not in json.dumps(session), "private marker leaked with --no-titles")
    return "titles become '<agent> session'; project/cwd/branch omitted"


# ---------------------------------------------------------------------------
# checks: contract schema


def validate_machine(machine, source):
    expect(isinstance(machine, dict), "%s: machine is not an object" % source)
    for key in ("id", "name", "host"):
        expect(isinstance(machine.get(key), str) and machine[key], "%s: machine.%s missing" % (source, key))
    expect(machine.get("platform") in PLATFORMS, "%s: machine.platform %r" % (source, machine.get("platform")))
    expect(isinstance(machine.get("online"), bool), "%s: machine.online must be bool" % source)
    if "last_seen" in machine and machine["last_seen"] is not None:
        is_iso(machine["last_seen"], "%s: machine.last_seen" % source)
    expect(isinstance(machine.get("agents"), list), "%s: machine.agents must be a list" % source)
    for agent in machine["agents"]:
        expect(isinstance(agent, dict), "%s: agent is not an object" % source)
        expect(isinstance(agent.get("id"), str) and agent["id"], "%s: agent.id missing" % source)
        expect(agent.get("status") in AGENT_STATUSES, "%s: agent.status %r" % (source, agent.get("status")))
        for key in ("sessions_active", "requests", "tokens"):
            is_int(agent.get(key), "%s: agent.%s" % (source, key))
        is_number(agent.get("tps"), "%s: agent.tps" % source)
        if "version" in agent:
            expect(isinstance(agent["version"], str), "%s: agent.version" % source)


def validate_steps(steps, source):
    expect(isinstance(steps, list), "%s: steps must be a list" % source)
    for step in steps:
        expect(isinstance(step, dict), "%s: step is not an object" % source)
        expect(isinstance(step.get("label"), str), "%s: step.label" % source)
        expect(step.get("kind") in STEP_KINDS, "%s: step.kind %r" % (source, step.get("kind")))
        is_int(step.get("start_ms"), "%s: step.start_ms" % source)
        is_int(step.get("dur_ms"), "%s: step.dur_ms" % source)


def validate_events(events, source):
    expect(isinstance(events, list), "%s: events must be a list" % source)
    expect(len(events) <= 4, "%s: events should hold at most 4 rows" % source)
    for event in events:
        expect(isinstance(event, dict), "%s: event is not an object" % source)
        is_iso(event.get("at"), "%s: event.at" % source)
        expect(event.get("kind") in STEP_KINDS, "%s: event.kind %r" % (source, event.get("kind")))
        expect(isinstance(event.get("text"), str) and len(event["text"]) <= 80,
               "%s: event.text should be a short string" % source)


def validate_tree(node, source):
    expect(isinstance(node, dict), "%s: subagent node is not an object" % source)
    for key in ("id", "label"):
        expect(isinstance(node.get(key), str), "%s: subagent.%s" % (source, key))
    is_int(node.get("tokens"), "%s: subagent.tokens" % source)
    expect(isinstance(node.get("children", []), list), "%s: subagent.children" % source)
    for child in node.get("children") or []:
        validate_tree(child, source)


def validate_snapshot(snapshot, source):
    expect(isinstance(snapshot, dict), "%s: snapshot is not an object" % source)
    equal(snapshot.get("schema_version"), 1, "%s: schema_version" % source)
    expect(snapshot.get("mode") in ("live", "demo", "snapshot"), "%s: mode %r" % (source, snapshot.get("mode")))
    is_iso(snapshot.get("generated_at"), "%s: generated_at" % source)
    expect(isinstance(snapshot.get("machines"), list), "%s: machines" % source)
    expect(isinstance(snapshot.get("sessions"), list), "%s: sessions" % source)
    if "refresh_seconds" in snapshot:
        is_int(snapshot["refresh_seconds"], "%s: refresh_seconds" % source)
    if "aggregate" in snapshot:
        aggregate = snapshot["aggregate"]
        expect(isinstance(aggregate, dict), "%s: aggregate must be an object" % source)
        for key in ("machines_online", "agents", "sessions_active", "sessions_total",
                    "requests_total", "tps"):
            is_number(aggregate.get(key), "%s: aggregate.%s" % (source, key))
        expect(isinstance(aggregate.get("tps_series"), list), "%s: aggregate.tps_series" % source)

    machine_ids = set()
    for machine in snapshot["machines"]:
        validate_machine(machine, source)
        expect(machine["id"] not in machine_ids, "%s: duplicate machine id %r" % (source, machine["id"]))
        machine_ids.add(machine["id"])

    session_ids = set()
    for session in snapshot["sessions"]:
        expect(isinstance(session, dict), "%s: session is not an object" % source)
        for key in ("id", "machine_id", "agent", "title"):
            expect(isinstance(session.get(key), str) and session[key], "%s: session.%s" % (source, key))
        expect(session["status"] in SESSION_STATUSES, "%s: session.status %r" % (source, session["status"]))
        expect(session["id"] not in session_ids, "%s: duplicate session id %r" % (source, session["id"]))
        session_ids.add(session["id"])
        expect(session["machine_id"] in machine_ids,
               "%s: session %r references unknown machine %r" % (source, session["id"], session["machine_id"]))
        is_iso(session.get("started_at"), "%s: session.started_at" % source)
        is_iso(session.get("updated_at"), "%s: session.updated_at" % source)
        if session.get("ended_at") is not None:
            is_iso(session["ended_at"], "%s: session.ended_at" % source)
        is_int(session.get("duration_ms"), "%s: session.duration_ms" % source)
        is_int(session.get("requests"), "%s: session.requests" % source)
        expect(session.get("tokens") is not None, "%s: session.tokens missing" % source)
        for key in ("input", "output"):
            is_int(session["tokens"].get(key), "%s: session.tokens.%s" % (source, key))
        context = session.get("context")
        expect(isinstance(context, dict), "%s: session.context" % source)
        is_int(context.get("used"), "%s: session.context.used" % source)
        expect(context.get("limit") is None or isinstance(context.get("limit"), int),
               "%s: session.context.limit" % source)
        subagents = session.get("subagents")
        expect(isinstance(subagents, dict), "%s: session.subagents" % source)
        is_int(subagents.get("count"), "%s: session.subagents.count" % source)
        is_int(subagents.get("active"), "%s: session.subagents.active" % source)
        is_number(session.get("tps"), "%s: session.tps" % source)
        if "tps_series" in session:
            series = session["tps_series"]
            equal(len(series), 90, "%s: tps_series length" % source)
            previous = None
            for point in series:
                expect(isinstance(point, list) and len(point) == 2, "%s: tps_series point" % source)
                is_int(point[0], "%s: tps_series timestamp" % source)
                is_number(point[1], "%s: tps_series value" % source)
                if previous is not None:
                    equal(point[0] - previous, 1000, "%s: tps_series must be 1s buckets" % source)
                previous = point[0]
            if "tps" in session:
                equal(session["tps"], series[-1][1],
                      "%s: session.tps must equal the last tps_series bucket" % source)
        if "steps" in session:
            validate_steps(session["steps"], source)
        if "events" in session:
            validate_events(session["events"], source)
        if "context_parts" in session:
            parts = session["context_parts"]
            expect(isinstance(parts, list) and parts, "%s: context_parts" % source)
            part_total = 0
            seen_keys = set()
            for part in parts:
                expect(isinstance(part, dict), "%s: context_parts item" % source)
                expect(isinstance(part.get("key"), str) and part["key"], "%s: context_parts key" % source)
                expect(isinstance(part.get("label"), str) and part["label"], "%s: context_parts label" % source)
                expect(part["key"] not in seen_keys, "%s: duplicate context_parts key %r" % (source, part["key"]))
                seen_keys.add(part["key"])
                is_int(part.get("tokens"), "%s: context_parts tokens" % source)
                expect(part["tokens"] >= 0, "%s: context_parts tokens must not be negative" % source)
                part_total += part["tokens"]
            expect(abs(part_total - context["used"]) <= 1,
                   "%s: context_parts sum %d != context.used %d"
                   % (source, part_total, context.get("used")))
            if context.get("limit"):
                expect(context["used"] <= context["limit"],
                       "%s: context.used %d exceeds limit %d"
                       % (source, context["used"], context["limit"]))
        if "subagent_tree" in session:
            validate_tree(session["subagent_tree"], source)
        if "cost_usd" in session:
            is_number(session["cost_usd"], "%s: session.cost_usd" % source)
        if "latency_ms" in session:
            is_int(session["latency_ms"], "%s: session.latency_ms" % source)

    if "aggregate" in snapshot:
        aggregate = snapshot["aggregate"]
        equal(aggregate["sessions_total"], len(snapshot["sessions"]),
              "%s: aggregate.sessions_total" % source)
        running = sum(1 for session in snapshot["sessions"] if session["status"] == "running")
        equal(aggregate["sessions_active"], running, "%s: aggregate.sessions_active" % source)
        running_tps = sum(float(session.get("tps") or 0) for session in snapshot["sessions"]
                          if session["status"] == "running")
        equal(aggregate["tps"], round(running_tps, 1),
              "%s: aggregate.tps must be the sum of running sessions' displayed tps" % source)


@check("snapshot schema: collector fixtures")
def schema_fixtures():
    validate_snapshot(fixture_snapshot(), "fixtures")
    return "fixture snapshot satisfies references/data-contract.md"


@check("snapshot schema: demo.json")
def schema_demo():
    demo = load_demo()
    if demo is None:
        warn("web/data/demo.json is missing (the UI needs it; not created by selftest)")
        return "skipped: demo.json missing"
    validate_snapshot(demo, "demo.json")
    equal(demo["mode"], "demo", "demo.json mode")
    expect(len(demo["machines"]) >= 3, "demo.json needs at least 3 machines")
    expect(any(not machine.get("online") for machine in demo["machines"]),
           "demo.json needs at least one offline machine")
    expect(len(demo["sessions"]) >= 5, "demo.json needs at least 5 sessions")
    statuses = {session["status"] for session in demo["sessions"]}
    for expected in ("running", "waiting", "done", "failed"):
        expect(expected in statuses, "demo.json lacks a %r session" % expected)
    for session in demo["sessions"]:
        for key in ("tps_series", "context", "tokens", "requests", "duration_ms", "subagents"):
            expect(key in session, "demo session %r lacks %s" % (session["id"], key))
    expect(sum(1 for session in demo["sessions"] if "subagent_tree" in session) >= 3,
           "demo.json needs at least 3 subagent_tree examples")
    expect(sum(1 for session in demo["sessions"] if "steps" in session or "context_parts" in session) >= 3,
           "demo.json needs at least 3 steps/context_parts examples")
    return "%d machines / %d sessions, coverage complete" % (len(demo["machines"]), len(demo["sessions"]))


# ---------------------------------------------------------------------------
# checks: CLI + static assets + docs


@check("collect.py CLI")
def collect_cli():
    command = [
        sys.executable, os.path.join(SCRIPTS_DIR, "collect.py"),
        "--json", "--home", FIXTURE_HOME, "--machine-id", "demo-host", "--now", FIXED_NOW,
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    expect(result.returncode == 0, "collect.py exited %d: %s"
           % (result.returncode, result.stderr.decode("utf-8", "replace")[-400:]))
    payload = json.loads(result.stdout.decode("utf-8"))
    equal(len(payload["sessions"]), 6, "CLI session count")
    equal(payload["machines"][0]["id"], "demo-host", "CLI machine id")
    return "prints a valid snapshot JSON"


def _local_reference(ref, base_dir):
    if not ref or ref.startswith(("#", "data:", "http:", "https:", "//", "mailto:", "javascript:")):
        return None
    cleaned = ref.split("#", 1)[0].split("?", 1)[0]
    if not cleaned:
        return None
    if cleaned.startswith("/"):
        return os.path.normpath(os.path.join(WEB_DIR, cleaned.lstrip("/")))
    return os.path.normpath(os.path.join(base_dir, cleaned))


def _missing_references(text, base_dir, pattern):
    missing = []
    for match in re.finditer(pattern, text):
        target = _local_reference(match.group(1), base_dir)
        if target is not None and not os.path.exists(target):
            missing.append(match.group(1))
    return missing


@check("web static asset references")
def static_assets():
    index = os.path.join(WEB_DIR, "index.html")
    if not os.path.isfile(index):
        warn("web/index.html is missing; static reference checks skipped")
        return "skipped: no web/index.html"
    problems = []
    with open(index, "r", encoding="utf-8", errors="replace") as handle:
        html = handle.read()
    problems += _missing_references(
        html, WEB_DIR, r"""(?:src|href)\s*=\s*["']([^"']+)["']""")
    problems += _missing_references(
        html, WEB_DIR, r"""url\(\s*["']?([^"')]+)["']?\s*\)""")
    css_files = []
    js_files = []
    for root, _dirs, files in os.walk(WEB_DIR):
        for name in files:
            path = os.path.join(root, name)
            if name.endswith(".css"):
                css_files.append(path)
            elif name.endswith(".js") and os.sep + "js" + os.sep in path:
                js_files.append(path)
    for path in css_files:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            problems += _missing_references(
                handle.read(), os.path.dirname(path), r"""url\(\s*["']?([^"')]+)["']?\s*\)""")
    for path in js_files:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        problems += _missing_references(
            text, os.path.dirname(path), r"""(?:from|import)\s*["']([^"']+)["']""")
        problems += _missing_references(
            text, os.path.dirname(path), r"""import\s*\(\s*["']([^"']+)["']""")
    if problems:
        raise AssertionError("missing local assets referenced by web/: %s" % ", ".join(sorted(set(problems))))
    return "%d css + %d js files, every local reference resolves" % (len(css_files), len(js_files))


@check("SKILL.md frontmatter and doc pointers")
def documentation():
    expect(os.path.isfile(SKILL_FILE), "SKILL.md is missing")
    with open(SKILL_FILE, "r", encoding="utf-8") as handle:
        text = handle.read()
    lines = text.splitlines()
    expect(lines and lines[0].strip() == "---", "SKILL.md must start with YAML frontmatter")
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    expect(end is not None, "SKILL.md frontmatter is not closed")
    frontmatter = "\n".join(lines[1:end])
    expect(re.search(r"^name:\s*dashboard\s*$", frontmatter, re.MULTILINE) is not None,
           "frontmatter name must be 'dashboard'")
    description = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    expect(description is not None and len(description.group(1).strip()) > 30,
           "frontmatter needs a model-facing description with triggers")
    expect(len(lines) <= 130, "SKILL.md is %d lines; keep it under ~120" % len(lines))
    body = "\n".join(lines[end + 1:])
    for name in ("data-contract.md", "design-language.md"):
        expect(name in body, "SKILL.md must point to references/%s" % name)
        expect(os.path.isfile(os.path.join(REFERENCES_DIR, name)), "references/%s is missing" % name)
    with open(os.path.join(REFERENCES_DIR, "data-contract.md"), "r", encoding="utf-8") as handle:
        contract = handle.read()
    for token in ("schema_version", "machines", "sessions", "tps_series"):
        expect(token in contract, "data-contract.md must document %r" % token)
    return "frontmatter valid, %d lines, references linked" % len(lines)


def _wait_for_url(stream, timeout=10.0):
    deadline = timeout
    while deadline > 0:
        ready, _w, _x = select.select([stream], [], [], min(0.25, deadline))
        if ready:
            line = stream.readline()
            match = re.search(r"http://127\.0\.0\.1:\d+/", line)
            if match:
                return match.group(0)
        deadline -= 0.25
    return None


@check("HTTP server smoke (loopback)")
def http_smoke():
    demo = load_demo()
    if demo is None:
        warn("demo.json missing; HTTP smoke test skipped")
        return "skipped: no demo.json"
    command = [
        sys.executable, os.path.join(SCRIPTS_DIR, "dashboard.py"),
        "--demo", "--port", "0", "--no-open", "--quiet",
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   universal_newlines=True)
    except OSError as exc:
        warn("cannot start the dashboard server here (%s); smoke test skipped" % exc)
        return "skipped: server unavailable"
    try:
        url = _wait_for_url(process.stdout)
        expect(url is not None, "server did not print its URL")
        try:
            with urllib.request.urlopen(url + "api/health", timeout=10) as response:
                equal(response.status, 200, "GET /api/health status")
                equal(response.headers.get_content_type(), "application/json", "health content type")
                health = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(url + "api/state", timeout=10) as response:
                equal(response.status, 200, "GET /api/state status")
                state = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(url, timeout=10) as response:
                equal(response.status, 200, "GET / status")
        except urllib.error.URLError as exc:
            warn("loopback requests unavailable (%s); smoke test skipped" % exc)
            return "skipped: loopback unavailable"
        equal(health.get("ok"), True, "health.ok")
        equal(health.get("mode"), "demo", "health.mode")
        equal(health.get("sessions"), len(demo["sessions"]), "health.sessions")
        equal(state.get("mode"), "demo", "state.mode")
        equal(len(state.get("sessions") or []), len(demo["sessions"]), "state.sessions")
        return "health/state/static served on a loopback port"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


# ---------------------------------------------------------------------------
# runner


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    skip_assets = "--skip-assets" in argv
    load_collector()

    for name, function in CHECKS:
        if skip_assets and name.startswith("web static"):
            warn("static asset checks skipped by --skip-assets")
            continue
        try:
            detail = function()
        except AssertionError as exc:
            FAILURES.append((name, str(exc)))
            print("  FAIL %s: %s" % (name, exc))
        except Exception as exc:  # a crashing check is a failing check
            FAILURES.append((name, "%s: %s" % (type(exc).__name__, exc)))
            print("  FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            PASSED.append(name)
            print("  ok   %s%s" % (name, " — %s" % detail if detail else ""))

    print("")
    for warning in WARNINGS:
        print("  WARN %s" % warning)
    if FAILURES:
        print("  %d passed, %d failed, %d warnings" % (len(PASSED), len(FAILURES), len(WARNINGS)))
        for name, detail in FAILURES:
            print("    failed: %s: %s" % (name, detail))
        return 1
    print("  %d passed, 0 failed, %d warnings" % (len(PASSED), len(WARNINGS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
