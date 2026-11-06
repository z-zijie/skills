#!/usr/bin/env python3
"""implement-change: apply one proposed edit under one card, compile it, and return the diff.

The edit itself is the model's work, so the script cannot invent it. What the script owns is the
part that must not be a judgement, and each of those is a hard failure rather than advice:

* the card must satisfy ``schemas/hypothesis.schema.json``, including its evidence statements;
* the edit must land inside the operator workspace, so a change to a spec, a tolerance, the
  evaluator or a harness is refused as a scope violation;
* the diff must touch the files the edit names and nothing else, one variable per experiment;
* the result must build, or the module must import when the operator has no build step.

The proposed edit arrives as a list of replacements, each an exact old block and its new text. That
keeps the transformation reproducible and reviewable: a replacement that does not match the file
exactly is a `build_failed` with the line it failed at, never a silent partial apply.
"""

from __future__ import annotations

import difflib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, read_request  # noqa: E402

FORBIDDEN_PREFIXES = ("evaluator/", "schemas/", "tooling/", "orchestrator/", "benchmark/",
                      "docs/", "skills/", "kb/", "playbook")
EDIT_SUFFIXES = (".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".cl", ".metal", ".ascendc", ".tile")


def resolve_workspace(request: Dict[str, Any]) -> Path:
    workspace = Path(str(request["workspace"]))
    if not workspace.is_absolute():
        workspace = ROOT / workspace
    return workspace.resolve()


def scope_violation(workspace: Path, path: Path) -> bool:
    """A path outside the operator workspace, or inside it but in a frozen area."""
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return True
    text = relative.as_posix()
    return any(text.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)


def apply_edits(source: Path, edits: List[Dict[str, str]]) -> Dict[str, Any]:
    """Apply exact replacements in order. A replacement that does not match stops the run."""
    original = source.read_text(encoding="utf-8")
    updated = original
    applied: List[Dict[str, Any]] = []
    for index, edit in enumerate(edits, start=1):
        old = str(edit.get("old") or "")
        new = str(edit.get("new") or "")
        if not old:
            return {"ok": False, "reason": f"edit {index} has no 'old' block to locate",
                    "original": original, "updated": updated, "applied": applied}
        count = updated.count(old)
        if count == 0:
            return {"ok": False,
                    "reason": f"edit {index} does not match the source: the text to replace is absent",
                    "original": original, "updated": updated, "applied": applied}
        if count > 1:
            return {"ok": False,
                    "reason": f"edit {index} matches {count} places; an ambiguous replacement is not one change",
                    "original": original, "updated": updated, "applied": applied}
        start = updated.index(old)
        line = updated.count("\n", 0, start) + 1
        updated = updated.replace(old, new, 1)
        applied.append({"edit": index, "line": line,
                        "removed_lines": old.count("\n") + 1,
                        "added_lines": new.count("\n") + 1})
    return {"ok": True, "reason": "", "original": original, "updated": updated, "applied": applied}


def build(workspace: Path, command: List[str], module: Path) -> Dict[str, Any]:
    """Compile when the operator has a build step, otherwise import the module."""
    if command:
        completed = subprocess.run([str(part) for part in command], cwd=str(workspace),
                                   capture_output=True, text=True, timeout=900, check=False)
        return {"ok": completed.returncode == 0, "command": [str(part) for part in command],
                "output": (completed.stdout + completed.stderr).strip()[-2000:], "skipped": False}
    completed = subprocess.run(
        [sys.executable, "-c", "import importlib.util,sys;"
                               "s=importlib.util.spec_from_file_location('candidate', sys.argv[1]);"
                               "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)",
         str(module)],
        cwd=str(workspace), capture_output=True, text=True, timeout=300, check=False)
    return {"ok": completed.returncode == 0, "command": [], "skipped": True,
            "output": (completed.stdout + completed.stderr).strip()[-2000:]}


def implement(request: Dict[str, Any]) -> Dict[str, Any]:
    notes: List[str] = []
    card = request.get("card")
    if not isinstance(card, dict):
        return {"status": "cannot_implement", "diff": None, "files_changed": [], "problems": [
            "cannot_implement: no hypothesis card was supplied"], "notes": notes,
            "build": {"ok": False, "skipped": False}}
    try:
        jsonschema_lite.check(card, jsonschema_lite.load_schema(
            str(ROOT / "schemas" / "hypothesis.schema.json")), label="card")
    except jsonschema_lite.ValidationError as error:
        return {"status": "cannot_implement", "diff": None, "files_changed": [],
                "build": {"ok": False, "skipped": False},
                "problems": [f"cannot_implement: the card fails its contract: {line}"
                             for line in error.errors], "notes": notes}

    workspace = resolve_workspace(request)
    if not workspace.is_dir():
        return {"status": "cannot_implement", "diff": None, "files_changed": [],
                "build": {"ok": False, "skipped": False},
                "problems": [f"cannot_implement: no workspace at {workspace}"], "notes": notes}

    entry = str(request.get("entry") or "kernel.py")
    source = (workspace / entry).resolve()
    if not source.is_file():
        return {"status": "cannot_implement", "diff": None, "files_changed": [],
                "build": {"ok": False, "skipped": False},
                "problems": [f"cannot_implement: no kernel module at {entry} in the workspace"],
                "notes": notes}
    if scope_violation(workspace, source):
        return {"status": "scope_violation", "diff": None, "files_changed": [],
                "build": {"ok": False, "skipped": False},
                "problems": [f"scope_violation: {entry} resolves outside the operator workspace"],
                "notes": notes}

    edits = request.get("edits")
    if not isinstance(edits, list) or not edits:
        return {"status": "cannot_implement", "diff": None, "files_changed": [],
                "build": {"ok": False, "skipped": False},
                "problems": ["cannot_implement: no proposed edit was supplied; the model supplies "
                             "the change and this script applies and checks it"], "notes": notes}

    applied = apply_edits(source, edits)
    if not applied["ok"]:
        return {"status": "build_failed", "diff": None, "files_changed": [],
                "build": {"ok": False, "skipped": True},
                "problems": [f"build_failed: {applied['reason']}"], "notes": notes}

    original, updated = applied["original"], applied["updated"]
    if original == updated:
        return {"status": "cannot_implement", "diff": "", "files_changed": [],
                "build": {"ok": False, "skipped": True},
                "problems": ["cannot_implement: the edit leaves the source unchanged, so there is "
                             "no change for a verifier to find"], "notes": notes}

    source.write_text(updated, encoding="utf-8")
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), updated.splitlines(keepends=True),
        fromfile=f"a/{entry}", tofile=f"b/{entry}"))
    built = build(workspace, list(request.get("build") or []), source)

    evidence = []
    statements = card.get("evidence_of_implementation") or []
    added = [line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    for statement in statements:
        evidence.append({"statement": str(statement),
                         "where": f"diff adds {len(added)} line(s); see {entry} "
                                  f"lines {applied['applied'][0]['line']}+"})
    if not built["ok"]:
        notes.append("the diff was applied and left in the workspace so a retry can build on it; "
                     "it is not a candidate until it compiles")
    if request.get("retry"):
        notes.append(f"retry attempt {request['retry'].get('attempt')}: the failing cases named "
                     "there are the target, and the mechanism is unchanged")
    status = "ok" if built["ok"] else "build_failed"
    return {"status": status, "diff": diff, "files_changed": [entry],
            "build": built, "evidence_map": evidence,
            "problems": [] if built["ok"] else [f"build_failed: {built['output'][:400]}"],
            "notes": notes}


def main(argv: List[str]) -> int:
    outcome = implement(read_request(argv))
    try:
        schema = jsonschema_lite.load_schema(
            str(ROOT / "skills" / "implement-change" / "contract" / "output.schema.json"))
        jsonschema_lite.check(outcome, schema, label="implement-change")
    except jsonschema_lite.ValidationError as error:
        outcome["problems"] = list(outcome.get("problems") or []) + \
            [f"schema: {line}" for line in error.errors]
        outcome["status"] = "cannot_implement" if outcome["status"] == "ok" else outcome["status"]
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
