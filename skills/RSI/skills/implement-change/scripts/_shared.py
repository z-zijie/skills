"""Shared plumbing for the scripts in this skill directory.

Every script here is a thin CLI over ``tooling/rsi``: find the RSI root, put ``tooling`` on the
path, read one JSON request from argv or stdin, print one JSON object. Nothing in this directory
reimplements a rule that the shared tooling already owns.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def repo_root(start: Optional[Path] = None) -> Path:
    """The RSI root: the directory that contains ``schemas/`` and ``tooling/``."""
    override = os.environ.get("RSI_ROOT")
    if override:
        return Path(override).resolve()
    here = Path(start or __file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "tooling" / "rsi").is_dir() and (candidate / "schemas").is_dir():
            return candidate
    raise SystemExit("cannot locate the RSI root: set RSI_ROOT")


ROOT = repo_root()
if str(ROOT / "tooling") not in sys.path:
    sys.path.insert(0, str(ROOT / "tooling"))

from rsi import jsonschema_lite, ledger, metrics, paths, yamlite  # noqa: E402


def read_request(argv) -> Dict[str, Any]:
    """One JSON object, from a file named on the command line or from stdin."""
    if len(argv) > 1:
        text = Path(argv[1]).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise SystemExit("pass a JSON request as a file path or on stdin")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise SystemExit("the request must be a JSON object")
    return payload


def contract(name: str, direction: str) -> Dict[str, Any]:
    return jsonschema_lite.load_schema(str(ROOT / "skills" / name / "contract" / f"{direction}.schema.json"))


def shared_schema(name: str) -> Dict[str, Any]:
    return jsonschema_lite.load_schema(str(ROOT / "schemas" / name))


def emit(payload: Dict[str, Any]) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


__all__ = [
    "ROOT", "contract", "emit", "jsonschema_lite", "ledger", "metrics", "paths",
    "read_request", "shared_schema", "yamlite",
]
