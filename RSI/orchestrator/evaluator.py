"""The evaluator's CLI wrapper: one entry point, three commands, nothing else.

Agents reach Layer 0 only through this module (and through the ``run-evaluator``,
``collect-profile`` and ``calibrate-hardware`` skills that call it). Keeping the invocation in
one place is what makes the boundary auditable: there is one string an agent can construct, and
it can only ever name a command the evaluator already exposes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._tooling import paths


class EvaluatorError(RuntimeError):
    """Raised when the evaluator refuses or fails; the message is what it said."""


def cli_path(root: Optional[Path] = None) -> Path:
    return (root or paths.repo_root()) / "evaluator" / "cli.py"


def run(args: List[str], root: Optional[Path] = None, timeout: int = 7200,
        cwd: Optional[Path] = None) -> Dict[str, Any]:
    """Invoke the evaluator and return its parsed JSON output."""
    path = cli_path(root)
    if not path.is_file():
        raise EvaluatorError(f"no evaluator CLI at {path}")
    completed = subprocess.run(
        [sys.executable, str(path), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
        cwd=str(cwd or paths.repo_root()),
    )
    if completed.returncode != 0:
        raise EvaluatorError(
            f"evaluator {' '.join(args)} exited {completed.returncode}: "
            f"{completed.stderr.strip()[-800:]}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise EvaluatorError(f"evaluator output was not JSON: {completed.stdout[:400]}") from error


def evaluate(op: str, candidate: str, out: Optional[str] = None, samples: int = 30,
             seed: int = 0, root: Optional[Path] = None) -> Dict[str, Any]:
    args = ["evaluate", "--op", op, "--candidate", candidate, "--samples", str(samples),
            "--seed", str(seed)]
    if out:
        args += ["--out", out]
    return run(args, root)


def aa(op: str, out: Optional[str] = None, samples: int = 30, root: Optional[Path] = None) -> Dict[str, Any]:
    args = ["aa", "--op", op, "--samples", str(samples)]
    if out:
        args += ["--out", out]
    return run(args, root)


def profile(op: str, out: Optional[str] = None, command: Optional[List[str]] = None,
            out_dir: Optional[str] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    args = ["profile", "--op", op]
    if out:
        args += ["--out", out]
    if out_dir:
        args += ["--out-dir", out_dir]
    if command:
        args += ["--command", *command]
    return run(args, root)


def calibrate(suite: str = "core", out: Optional[str] = None, samples: int = 15,
              root: Optional[Path] = None) -> Dict[str, Any]:
    args = ["calibrate", "--suite", suite, "--samples", str(samples)]
    if out:
        args += ["--out", out]
    return run(args, root)


def redteam(op: str, out: Optional[str] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    args = ["redteam", "--op", op]
    if out:
        args += ["--out", out]
    return run(args, root, timeout=3600)
