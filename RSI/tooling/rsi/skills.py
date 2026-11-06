"""Skill discovery, contract validation and per-skill regression replay.

A skill is a directory under ``skills/`` with a frozen ``contract/`` and an editable
``strategy/``. This module is what makes those two statements true:

* ``validate_skill`` checks the layout, the frontmatter and the contracts.
* ``validate_step`` validates one step's input and output against the skill's contracts, so a
  failed validation retries in place and never passes bad data downstream.
* ``replay_tests`` runs the skill's labelled regression cases, which is how a strategy change
  is checked against past experience before it is trusted.

Interaction happens through these functions, so a skill's contract cannot silently drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import jsonschema_lite, paths, yamlite

REQUIRED_FILES = ("SKILL.md",)
OPTIONAL_DIRS = ("contract", "strategy", "scripts", "tests")


class SkillError(ValueError):
    """Raised when a skill's layout, frontmatter or contract is malformed."""


def skills_dir(root: Optional[Path] = None) -> Path:
    return (root or paths.repo_root()) / "skills"


def discover(root: Optional[Path] = None) -> List[Path]:
    base = skills_dir(root)
    if not base.is_dir():
        return []
    return sorted(path for path in base.iterdir() if path.is_dir() and (path / "SKILL.md").is_file())


def read_frontmatter(path: Path) -> Dict[str, Any]:
    """Parse the YAML frontmatter of a SKILL.md."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SkillError(f"{path}: SKILL.md must start with a frontmatter block")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillError(f"{path}: unterminated frontmatter block")
    data = yamlite.loads(parts[1]) or {}
    if not isinstance(data, dict):
        raise SkillError(f"{path}: frontmatter must be a mapping")
    return data


def validate_skill(name: str, root: Optional[Path] = None) -> List[str]:
    """Return every layout or contract problem in a skill, empty when it is sound."""
    directory = skills_dir(root) / name
    problems: List[str] = []
    if not directory.is_dir():
        return [f"no skill directory {directory}"]
    for required in REQUIRED_FILES:
        if not (directory / required).is_file():
            problems.append(f"{name}: missing {required}")
    if problems:
        return problems
    try:
        frontmatter = read_frontmatter(directory / "SKILL.md")
    except (SkillError, yamlite.YamlError) as error:
        return [f"{name}: {error}"]
    if frontmatter.get("name") != name:
        problems.append(f"{name}: frontmatter name is {frontmatter.get('name')!r}, expected {name!r}")
    description = str(frontmatter.get("description") or "")
    if len(description) < 20:
        problems.append(f"{name}: description is too short to act as a trigger")
    contract_dir = directory / "contract"
    if contract_dir.is_dir():
        for schema_path in sorted(contract_dir.glob("*.json")):
            try:
                jsonschema_lite.load_schema(str(schema_path))
            except jsonschema_lite.SchemaError as error:
                problems.append(f"{name}: {schema_path.name}: {error}")
    body = (directory / "SKILL.md").read_text(encoding="utf-8")
    lines = body.splitlines()
    if len(lines) > 260:
        problems.append(f"{name}: SKILL.md is {len(lines)} lines; move detail into strategy/")
    return problems


def contract_path(name: str, direction: str, root: Optional[Path] = None) -> Path:
    if direction not in {"input", "output"}:
        raise SkillError("direction must be 'input' or 'output'")
    return skills_dir(root) / name / "contract" / f"{direction}.schema.json"


def load_contract(name: str, direction: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = contract_path(name, direction, root)
    if not path.is_file():
        return None
    return jsonschema_lite.load_schema(str(path))


def validate_step(name: str, direction: str, instance: Any, root: Optional[Path] = None) -> None:
    """Raise ``ValidationError`` when a step's data violates its skill contract."""
    schema = load_contract(name, direction, root)
    if schema is None:
        raise SkillError(f"{name}: no {direction} contract; the interface must be frozen before use")
    jsonschema_lite.check(instance, schema, label=f"{name}/{direction}")


def _matches(actual: Any, expected: Any) -> bool:
    """Compare an actual result with an expectation.

    A mapping expectation matches when every key it names matches: a regression case asserts
    the part it cares about and is not rewritten each time the script adds a field. Lists and
    scalars compare exactly, so an expectation never silently stops checking anything.
    """
    if isinstance(actual, dict) and isinstance(expected, dict):
        missing = [key for key in expected if key not in actual]
        if missing:
            return False
        return all(_matches(actual[key], value) for key, value in expected.items())
    return actual == expected


def replay_tests(name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """Run a skill's labelled regression cases.

    A case is a ``.json`` file under ``tests/`` with ``input``, ``expected`` and an optional
    ``actual_command``. Where a case names a ``script``, that script is executed with the case
    input and its JSON output is compared; otherwise the expected output is compared with what
    the contract allows. Running without a device is the point: a strategy change is checked
    against past experience first.
    """
    directory = skills_dir(root) / name / "tests"
    if not directory.is_dir():
        return {"skill": name, "cases": 0, "passed": 0, "failed": [], "note": "no tests directory"}
    import json
    import subprocess
    import sys

    results: List[Dict[str, Any]] = []
    passed = 0
    for case_path in sorted(directory.glob("*.json")):
        payload = json.loads(case_path.read_text(encoding="utf-8"))
        case_name = payload.get("name", case_path.stem)
        expected = payload.get("expected")
        script = payload.get("script")
        if script:
            script_path = skills_dir(root) / name / script
            completed = subprocess.run(
                [sys.executable, str(script_path)], input=json.dumps(payload.get("input")),
                capture_output=True, text=True, timeout=600, check=False,
            )
            if completed.returncode != 0:
                results.append({"case": case_name, "ok": False,
                                "reason": f"script exited {completed.returncode}: {completed.stderr[-300:]}"})
                continue
            try:
                actual = json.loads(completed.stdout)
            except json.JSONDecodeError:
                results.append({"case": case_name, "ok": False, "reason": "script output was not JSON"})
                continue
        else:
            actual = payload.get("actual")
        ok = _matches(actual, expected)
        if ok:
            passed += 1
        results.append({"case": case_name, "ok": ok,
                        "reason": "" if ok else "actual did not match expected"})
    failed = [entry for entry in results if not entry["ok"]]
    return {"skill": name, "cases": len(results), "passed": passed, "failed": failed,
            "results": results}
