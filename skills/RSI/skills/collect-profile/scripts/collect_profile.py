#!/usr/bin/env python3
"""collect-profile: run the profiler once, return canonical metrics and what is missing.

The profiler itself lives in ``evaluator/profiler/adapters/``; this script is a thin CLI over
``rsi.metrics`` and ``rsi.jsonschema_lite``. Its only judgement is a mechanical one: a name that
is not in the canonical registry is not evidence, so it is quarantined, not passed through.

Metrics and availability are reconciled against the registry rather than trusted: a device that
cannot see scratchpad conflict reports it as unavailable, and "unavailable" must never be read
downstream as zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, metrics, read_request, shared_schema  # noqa: E402


def build_command(request: Dict[str, Any]) -> List[str]:
    command = [sys.executable, "-m", "evaluator.cli", "profile",
               "--op", str(request["op"]), "--candidate", str(request["candidate"])]
    if request.get("adapter"):
        command += ["--adapter", str(request["adapter"])]
    if request.get("out_dir"):
        command += ["--out-dir", str(request["out_dir"])]
    if request.get("out"):
        command += ["--out", str(request["out"])]
    command += ["--command"] + [str(part) for part in (request.get("command") or ["true"])]
    return command


def canonicalize(profile: Dict[str, Any]) -> Dict[str, List[str]]:
    """Rewrite legacy spellings, quarantine unknown names, then reconcile against the registry.

    Returns the notes and problems the reconciliation produced. Names the registry knows are
    kept under their canonical spelling; names it does not know are moved out of ``metrics``
    because a number nobody can name cannot be compared across devices.
    """
    notes: List[str] = []
    problems: List[str] = []
    raw = profile.get("metrics") or {}
    if not isinstance(raw, dict):
        return {"notes": notes, "problems": ["schema: profile.metrics is not an object"]}

    canonical: Dict[str, Any] = {}
    unknown: List[str] = []
    for name, value in raw.items():
        resolved = metrics.canonical(str(name))
        if resolved not in metrics.METRICS:
            unknown.append(str(name))
            continue
        if resolved != name:
            notes.append(f"legacy metric {name!r} rewritten to {resolved!r}")
        canonical[resolved] = value
    profile["metrics"] = canonical

    declared = [str(name) for name in (profile.get("unavailable") or [])]
    declared = [metrics.canonical(name) for name in declared]
    for name in unknown:
        if name not in declared:
            declared.append(name)
            notes.append(f"metric {name!r} is not registered in docs/vocabulary.md; recorded as unavailable")
    missing = [name for name in metrics.METRICS if name not in canonical and name not in declared]
    declared.extend(missing)
    if missing:
        notes.append(f"{len(missing)} registered metric(s) were neither produced nor declared; "
                     "listed as unavailable rather than zero")
    profile["unavailable"] = declared
    return {"notes": notes, "problems": problems}


def normalize_optional(profile: Dict[str, Any]) -> List[str]:
    """Drop optional fields whose value is null instead of inventing one.

    An adapter that reports no version or no transaction granularity is saying "unknown". The
    shared profile schema types those fields as present-and-typed, so an explicit null is
    removed and recorded, which keeps the difference between "unknown" and "zero" visible.
    """
    notes: List[str] = []
    for field in ("adapter_version", "transaction_granularity_bytes", "created", "notes"):
        if field in profile and profile[field] is None:
            del profile[field]
            notes.append(f"{field} was null; omitted rather than defaulted")
    return notes


def finish(profile: Dict[str, Any], command: List[str]) -> Dict[str, Any]:
    notes = normalize_optional(profile)
    reconciled = canonicalize(profile)
    problems = list(reconciled["problems"])
    notes.extend(reconciled["notes"])
    try:
        jsonschema_lite.check(profile, shared_schema("profile.schema.json"), label="profile")
    except jsonschema_lite.ValidationError as error:
        problems.extend(f"schema: {line}" for line in error.errors)
    if not profile.get("metrics"):
        notes.append(f"adapter {profile.get('adapter')!r} produced no canonical metric; "
                     "diagnosis must fall back to class and timing")
    status = "schema_invalid" if problems else "ok"
    return {"status": status, "profile": profile, "problems": problems, "notes": notes,
            "command": command}


def collect_live(request: Dict[str, Any]) -> Dict[str, Any]:
    command = build_command(request)
    timeout = int(request.get("timeout_s") or 3600)
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                               timeout=timeout, check=False)
    if completed.returncode != 0:
        return {"status": "unavailable", "profile": None, "notes": [], "command": command,
                "problems": [f"unavailable: profiler run exited {completed.returncode}: "
                             f"{completed.stderr.strip()[-400:]}"]}
    try:
        profile = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as error:
        return {"status": "unavailable", "profile": None, "notes": [], "command": command,
                "problems": [f"unavailable: profiler output is not JSON: {error}"]}
    return finish(profile, command)


def main(argv: List[str]) -> int:
    request = read_request(argv)
    stored = request.get("profile_file")
    if stored:
        path = Path(str(stored))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("profile"), dict):
            payload = payload["profile"]
        return emit(finish(payload, ["replay", str(path)]))

    candidate = Path(str(request["candidate"]))
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    if not candidate.is_file():
        return emit({"status": "unavailable", "profile": None, "notes": [], "command": [],
                     "problems": [f"unavailable: no candidate module at {request['candidate']}"]})
    if not request.get("command"):
        request = dict(request, command=[sys.executable, "-c", "import runpy, sys; runpy.run_path(sys.argv[1])", str(candidate)])
    return emit(collect_live(dict(request, candidate=str(candidate))))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
