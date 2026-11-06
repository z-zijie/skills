#!/usr/bin/env python3
"""run-evaluator: invoke the frozen evaluator once, check what came back, report it.

The evaluator is Layer 0 and is reached only through its CLI. This script does three mechanical
things: run the CLI, validate the emitted object against ``schemas/result.schema.json``, and
verify the signature. A result that fails either check is labelled and never passed onward.

Two properties are deliberate. The schema check separates *missing or mistyped fields*, which
block a result, from *extra bookkeeping blocks the evaluator adds*, which are recorded in
``notes`` so contract drift stays visible without stopping the loop. Offline replay (a request
carrying ``result_file``) takes the same check path, so a regression case needs no device.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, ledger, read_request, shared_schema  # noqa: E402

BLOCKING_PREFIX = ("$",)


def build_command(request: Dict[str, Any]) -> List[str]:
    """The evaluator CLI argv for one request, always via ``-m`` and never via its internals."""
    command = [sys.executable, "-m", "evaluator.cli", "evaluate",
               "--op", str(request["op"]), "--candidate", str(request["candidate"])]
    for flag in ("samples", "warmup", "seed", "backend"):
        if request.get(flag) is not None:
            command += [f"--{flag}", str(request[flag])]
    if request.get("out"):
        command += ["--out", str(request["out"])]
    return command


def resolve_candidate(candidate: str) -> Path:
    """The evaluator's own resolution order, so "missing" here means missing there too."""
    raw = Path(candidate)
    options = [raw] if raw.is_absolute() else [Path.cwd() / raw, ROOT / raw]
    for option in options:
        for form in (option, option.with_suffix(".py")):
            if form.is_file():
                return form
    return Path(candidate)


def split_schema_errors(result: Dict[str, Any]) -> Dict[str, List[str]]:
    """Schema violations, split into those that block and those that only show drift.

    An extra key inside a nested block is the evaluator documenting itself; a missing or
    mistyped required field means the object downstream would break. Only the second blocks.
    """
    errors = jsonschema_lite.validate(result, shared_schema("result.schema.json"))
    blocking = [error for error in errors if "unexpected property" not in error]
    drift = [error for error in errors if "unexpected property" in error]
    return {"blocking": blocking, "drift": drift}


def check_result(result: Dict[str, Any], key: Optional[bytes] = None) -> Dict[str, Any]:
    """Schema and signature checks. ``key`` is only supplied by the offline regression cases."""
    problems: List[str] = []
    notes: List[str] = []
    split = split_schema_errors(result)
    problems.extend(f"schema: {line}" for line in split["blocking"])
    notes.extend(f"schema_drift: {line}" for line in split["drift"])
    try:
        ledger.verify(result, key=key, root=ROOT)
    except (ledger.SignatureError, OSError, ValueError) as error:
        problems.append(f"signature: {error}")
    return {"problems": problems, "notes": notes}


def classify(result: Dict[str, Any], problems: List[str]) -> str:
    if problems:
        first = problems[0].split(":", 1)[0]
        return {"schema": "schema_invalid", "signature": "signature_invalid"}.get(first, "unavailable")
    passed = bool((result.get("correctness") or {}).get("passed"))
    return "ok" if passed else "correctness_failed"


def finish(result: Dict[str, Any], command: List[str],
           key: Optional[bytes] = None) -> Dict[str, Any]:
    checked = check_result(result, key=key)
    return {"status": classify(result, checked["problems"]), "result": result,
            "problems": checked["problems"], "notes": checked["notes"], "command": command}


def run_live(request: Dict[str, Any]) -> Dict[str, Any]:
    command = build_command(request)
    timeout = int(request.get("timeout_s") or 3600)
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                               timeout=timeout, check=False)
    if completed.returncode != 0:
        return {"status": "unavailable", "result": None, "notes": [], "command": command,
                "problems": [f"unavailable: evaluator exited {completed.returncode}: "
                             f"{completed.stderr.strip()[-400:]}"]}
    text = completed.stdout.strip()
    if not text:
        return {"status": "unavailable", "result": None, "notes": [], "command": command,
                "problems": ["unavailable: the evaluator printed nothing"]}
    try:
        result = json.loads(text)
    except json.JSONDecodeError as error:
        return {"status": "unavailable", "result": None, "notes": [], "command": command,
                "problems": [f"unavailable: evaluator output is not JSON: {error}"]}
    return finish(result, command)


def main(argv: List[str]) -> int:
    request = read_request(argv)
    stored = request.get("result_file")
    if stored:
        path = Path(str(stored))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            payload = payload["result"]
        return emit(finish(payload, ["replay", str(path)]))

    candidate = resolve_candidate(str(request["candidate"]))
    if not candidate.is_file():
        return emit({"status": "unavailable", "result": None, "notes": [], "command": [],
                     "problems": [f"unavailable: no candidate module at {request['candidate']}"]})
    return emit(run_live(dict(request, candidate=str(candidate))))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
