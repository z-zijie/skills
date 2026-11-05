"""Repository layout and the permission boundaries between agents and the evaluator.

Directory boundaries are permission boundaries: the fewer places an agent can write, the
more the system can be trusted. This module is the single place that decides what a role may
write, and every tool that writes calls it. An attempt to write outside the boundary is
recorded, because the attempt itself is worth seeing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

#: Roles that exist in the system. `human` is the owner: no boundary applies.
ROLES = (
    "human",
    "evaluator",
    "inner_loop",
    "outer_loop",
    "replay_judge",
    "distiller",
)

#: path pattern (relative to the RSI root, `/` separated, `*` matches one segment) ->
#: roles allowed to write there. Anything not listed is writable by nobody but the human.
WRITE_POLICY: Dict[str, Tuple[str, ...]] = {
    "operators/*/workspace/**": ("inner_loop",),
    "ledger/experiments.jsonl": ("inner_loop", "outer_loop"),
    "ledger/artifacts/**": ("inner_loop", "outer_loop", "evaluator"),
    "kb/proposals/**": ("outer_loop", "distiller", "inner_loop"),
    "kb/conjectures/**": ("outer_loop", "inner_loop"),
    "orchestrator/state/**": ("inner_loop", "outer_loop"),
    "reports/**": ("inner_loop", "outer_loop"),
    "benchmark/dev.txt": ("human",),
    # Written only by the calibration workflow inside the evaluator.
    "kb/hardware/measured/**": ("evaluator",),
}

#: Paths that are hidden from every agent: only the replay judge and the human read them.
HIDDEN_FROM_AGENTS = ("benchmark/holdout.txt", "benchmark/holdout/**")

#: Paths the outer loop may propose to change but never write directly.
PROPOSAL_ONLY = ("kb/rules", "kb/patterns", "kb/antipatterns", "skills", "playbooks")


def repo_root() -> Path:
    """The RSI root: the directory that contains ``schemas/`` and ``evaluator/``."""
    override = os.environ.get("RSI_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2]


def rel(path: os.PathLike, root: Optional[Path] = None) -> str:
    """A path relative to the RSI root.

    Both sides are resolved before the comparison. Resolving only one is a real bug on any
    platform with a symlinked temp directory: ``/var`` resolves to ``/private/var`` on macOS, so
    an unresolved root never prefixes a resolved path and every write looks like a violation.
    """
    base = Path(root).resolve() if root is not None else repo_root().resolve()
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:
        return resolved.as_posix()


def _match(pattern: str, target: str) -> bool:
    pattern_parts = pattern.split("/")
    target_parts = target.split("/")
    if pattern.endswith("/**"):
        head = pattern_parts[:-1]
        if len(target_parts) < len(head):
            return False
        for index, part in enumerate(head):
            if part == "*":
                continue
            if part != target_parts[index]:
                return False
        return True
    if len(pattern_parts) != len(target_parts):
        return False
    return all(p == "*" or p == t for p, t in zip(pattern_parts, target_parts))


def writable(role: str, path: os.PathLike, root: Optional[Path] = None) -> bool:
    """Whether ``role`` may write ``path``. The human may write everything."""
    if role == "human":
        return True
    if role not in ROLES:
        return False
    target = rel(path, root)
    for pattern, roles in sorted(WRITE_POLICY.items()):
        if _match(pattern, target):
            return role in roles
    return False


def hidden_from_agents(path: os.PathLike, root: Optional[Path] = None) -> bool:
    target = rel(path, root)
    return any(_match(item, target) for item in HIDDEN_FROM_AGENTS)


class PermissionDenied(PermissionError):
    """Raised when a role attempts a write outside its boundary."""


def require_writable(role: str, path: os.PathLike, root: Optional[Path] = None,
                     attempts_log: Optional[os.PathLike] = None) -> None:
    """Raise unless ``role`` may write ``path``; record the attempt either way."""
    if writable(role, path, root):
        return
    if attempts_log is not None:
        record_attempt(role, path, attempts_log)
    raise PermissionDenied(
        f"role {role!r} may not write {rel(path, root)!r}; "
        f"the writable boundary for {role!r} is {allowed_globs(role)!r}"
    )


def record_attempt(role: str, path: os.PathLike, log_path: os.PathLike,
                   root: Optional[Path] = None) -> None:
    import json
    import time

    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role": role,
             "path": rel(path, root)}
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def allowed_globs(role: str) -> List[str]:
    return sorted(pattern for pattern, roles in WRITE_POLICY.items() if role in roles)


def require_hidden(path: os.PathLike, root: Optional[Path] = None) -> None:
    if hidden_from_agents(path, root):
        raise PermissionDenied(
            f"{rel(path, root)!r} is the held-out split: only the replay judge and the human read it"
        )


def op_dir(op: str, root: Optional[Path] = None) -> Path:
    return (root or repo_root()) / "operators" / op


def ledger_path(root: Optional[Path] = None) -> Path:
    return (root or repo_root()) / "ledger" / "experiments.jsonl"


def kb_dir(root: Optional[Path] = None) -> Path:
    return (root or repo_root()) / "kb"


def artifacts_dir(root: Optional[Path] = None) -> Path:
    return (root or repo_root()) / "ledger" / "artifacts"


def state_dir(root: Optional[Path] = None) -> Path:
    return (root or repo_root()) / "orchestrator" / "state"


def ensure_parent(path: os.PathLike) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def iter_operators(root: Optional[Path] = None) -> Iterable[Path]:
    base = (root or repo_root()) / "operators"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "spec.yaml").is_file())
