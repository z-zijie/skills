"""Signed results and an append-only ledger.

Two guarantees hold the whole system together:

* The evaluator signs every result. The ledger accepts nothing else, so a fabricated result
  file cannot become "experience".
* History is never edited or deleted. The ledger is append-only, entries are validated against
  the schema before they land, and each entry records the scaffold commit that was live.

The signature is HMAC-SHA256 over the canonical JSON body of the result, keyed by a file under
``.rsi/`` that belongs to the evaluator's user. This is integrity, not secrecy: it detects
tampering by an agent that can write the filesystem, and it is honest about not being a
security boundary against the owner.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import jsonschema_lite, metrics as metrics_mod, paths, yamlite

SCHEMA_VERSION = "1"
KEY_ENV = "RSI_SIGNING_KEY"
KEY_FILE = Path(".rsi") / "evaluator.key"


class SignatureError(ValueError):
    """Raised when a result's signature does not verify."""


class LedgerError(ValueError):
    """Raised when a ledger entry is rejected."""


def key_path(root: Optional[Path] = None) -> Path:
    return (root or paths.repo_root()) / KEY_FILE


def load_key(root: Optional[Path] = None, create: bool = False,
             allow_env: bool = False) -> bytes:
    """The evaluator's signing key.

    The key is a file owned by the evaluator's user, and by default that file is the only source.
    An environment variable is accepted only when a caller explicitly asks for it, because a key
    that any process can inject through its environment is a key any agent can sign with — which
    would make every signature in the ledger worthless while still looking verified.
    """
    if allow_env:
        env = os.environ.get(KEY_ENV)
        if env:
            return env.encode("utf-8")
    path = key_path(root)
    if path.is_file():
        return path.read_bytes().strip()
    if not create:
        raise SignatureError(
            f"no evaluator signing key at {path}: run `tools/rsi-cli keygen` once as the "
            "evaluator's user, or set RSI_SIGNING_KEY"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode("utf-8")
    # 0o600: the key belongs to the evaluator's user, not to an agent's account.
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key


def canonical_body(result: Dict[str, Any]) -> bytes:
    """The bytes that get signed: the result without its ``signature`` block."""
    body = {key: value for key, value in result.items() if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def candidate_hash(path: Any) -> str:
    """Hash of the candidate source a result describes.

    Without this a signed result says "some kernel measured X" rather than "this kernel measured
    X", so a result could be presented beside code it never ran. The hash is what pins a signature
    to the file it is evidence about.
    """
    target = Path(path)
    if not target.is_file():
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()


def tolerances_hash(op_spec: Any) -> str:
    """Hash of the operator's tolerance block, so a loosened tolerance cannot ride a signature.

    A result signed under ``rtol=1e-5`` must not be presentable as one signed under ``1e-2``: the
    numbers would be unchanged and the claim about them would not be.
    """
    tolerance = getattr(op_spec, "tolerance", None)
    if tolerance is None:
        return ""
    payload = json.dumps(tolerance, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def sign(result: Dict[str, Any], key: Optional[bytes] = None, root: Optional[Path] = None,
         key_id: str = "local") -> Dict[str, Any]:
    """Return a copy of ``result`` with a signature block added.

    The signature covers the whole body, which now includes the candidate's source hash, the
    tolerance hash and the environment fingerprint. Those three are what turn a signed number into
    a claim that can be checked against the code and the rules it came from.
    """
    secret = key if key is not None else load_key(root)
    digest = hmac.new(secret, canonical_body(result), hashlib.sha256).hexdigest()
    signed = dict(result)
    signed["signature"] = {"algo": "hmac-sha256", "key_id": key_id, "value": digest}
    return signed


def verify(result: Dict[str, Any], key: Optional[bytes] = None, root: Optional[Path] = None) -> bool:
    """Whether the result's signature block matches its body."""
    block = result.get("signature")
    if not isinstance(block, dict) or block.get("algo") != "hmac-sha256":
        raise SignatureError("result carries no hmac-sha256 signature block")
    secret = key if key is not None else load_key(root)
    expected = hmac.new(secret, canonical_body(result), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(block.get("value", ""))):
        raise SignatureError("result signature does not match its body: the result was modified")
    return True


def _schema_file(name: str, root: Optional[Path] = None) -> Path:
    """Schemas are the frozen interface: they live with the framework, never under a work root."""
    candidate = (root or paths.repo_root()) / "schemas" / name
    if candidate.is_file():
        return candidate
    return paths.repo_root() / "schemas" / name


def result_schema(root: Optional[Path] = None) -> Dict[str, Any]:
    return jsonschema_lite.load_schema(str(_schema_file("result.schema.json", root)))


def validate_result(result: Dict[str, Any], root: Optional[Path] = None,
                    key: Optional[bytes] = None) -> None:
    """Verify the signature first, then the shape.

    Order matters for the message a caller gets: an unsigned result is a forgery, not a schema
    violation, and saying so is the difference between "retry the step" and "someone edited a
    result file".

    ``key`` lets a caller verify against a key it supplies instead of the machine's key file,
    which is what makes a signature test independent of local deployment state.
    """
    verify(result, key=key, root=root)
    jsonschema_lite.check(result, result_schema(root), label="result")


# -- ledger ---------------------------------------------------------------

def entry_schema(root: Optional[Path] = None) -> Dict[str, Any]:
    return jsonschema_lite.load_schema(str(_schema_file("ledger-entry.schema.json", root)))


def read_entries(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = paths.ledger_path(root)
    if not path.is_file():
        return []
    entries: List[Dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise LedgerError(f"{path}:{number}: not a JSON object: {error}") from error
    return entries


def next_exp_id(entries: Optional[List[Dict[str, Any]]] = None, root: Optional[Path] = None) -> str:
    existing = entries if entries is not None else read_entries(root)
    highest = 0
    for entry in existing:
        try:
            highest = max(highest, int(str(entry.get("exp_id", "exp-0")).split("-")[1]))
        except (IndexError, ValueError):
            continue
    return f"exp-{highest + 1:04d}"


def validate_entry(entry: Dict[str, Any], root: Optional[Path] = None) -> None:
    """Schema-check an entry and refuse canonical-name violations in its counters.

    An entry with no evaluator signature is refused: the ledger's whole value is that its lines
    are claims the evaluator made, and a line without one is a claim somebody else made in the
    same format.
    """
    if not str(entry.get("evaluator_signature") or "").strip():
        raise LedgerError(
            f"{entry.get('exp_id')}: no evaluator signature. A line without one is not evidence, "
            "it is an assertion in the same format"
        )
    jsonschema_lite.check(entry, entry_schema(root), label=f"ledger entry {entry.get('exp_id')}")
    for field in ("counters_before", "counters_after"):
        raw = list((entry.get(field) or {}).keys())
        unknown = metrics_mod.check_names(raw)
        if unknown:
            raise LedgerError(f"{entry.get('exp_id')}: {field} names unregistered metrics {unknown}")


def append(entry: Dict[str, Any], root: Optional[Path] = None,
           role: str = "inner_loop") -> Path:
    """Validate and append one entry. The file is opened append-only and never rewritten.

    The write policy is checked here, at the write, rather than described in a document: a
    boundary that no call site enforces is a comment.
    """
    from . import paths as paths_mod

    validate_entry(entry, root)
    paths_mod.require_writable(role, paths_mod.ledger_path(root), root=root)
    existing = read_entries(root)
    if any(existing_entry.get("exp_id") == entry.get("exp_id") for existing_entry in existing):
        raise LedgerError(f"{entry.get('exp_id')} already exists: the ledger is append-only")
    path = paths.ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def entries_for_op(op: str, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    return [entry for entry in read_entries(root) if entry.get("op") == op]


def evidence_counts(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    """Confirmed, refuted and not-implemented counts over a set of entries."""
    counts = {"confirmed": 0, "refuted": 0, "not_implemented": 0, "implausible": 0}
    for entry in entries:
        verdict = entry.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
        if entry.get("implausible"):
            counts["implausible"] += 1
    return counts


def archive_artifact(exp_id: str, name: str, data: Any, root: Optional[Path] = None,
                     role: str = "inner_loop") -> Path:
    """Store a diff, report or log under ``ledger/artifacts/<exp_id>/``."""
    from . import paths as paths_mod

    directory = paths.artifacts_dir(root) / exp_id
    paths_mod.require_writable(role, directory / name, root=root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    elif isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(str(data), encoding="utf-8")
    return path


def scaffold_commit(root: Optional[Path] = None) -> str:
    """The git commit of skills and playbooks that is live right now.

    Attribution depends on this: without it nobody can say which scaffold change produced the
    gain. A dirty tree is recorded as ``<commit>+dirty`` rather than hidden.
    """
    import subprocess

    base = root or paths.repo_root()
    try:
        commit = subprocess.run(["git", "-C", str(base), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if commit.returncode != 0:
        return "unknown"
    sha = commit.stdout.strip()
    status = subprocess.run(["git", "-C", str(base), "status", "--porcelain", "--untracked-files=no"],
                            capture_output=True, text=True, timeout=10, check=False)
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
    return sha + ("+dirty" if dirty else "")


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_document(path: Path) -> Any:
    """Load a ``.yaml``/``.yml`` file as YAML, anything else as JSON."""
    path = Path(path)
    if path.suffix in {".yaml", ".yml"}:
        return yamlite.load(path)
    return load_json(path)
