"""Running candidate code without handing it the signing key.

The evaluator's whole authority rests on one property: the signature means *the evaluator* produced
the measurement. If candidate code runs in the process that holds the key, that property is gone —
the candidate can read the key from memory, from the environment, or from the filesystem, and sign
whatever it likes. It can also reach the reference implementation's output in the caller's frame,
which makes correctness meaningless.

This module runs candidate code in a **separate interpreter** and passes it nothing but numbers:

* the child gets a minimal environment, with the signing key removed;
* it receives the plan (case, dtype, paths) over stdin and returns samples over stdout;
* the parent never imports candidate code, so the candidate can never see the parent's frame.

What this is and is not:

It **is** a real boundary against reading the key, the reference output, and the parent's timing —
the three things a cheating candidate most wants, and the three the review named.

It is **not** a security sandbox. A determined kernel can still reach the network or the
filesystem, because the same user runs both processes. Closing that needs the deployment step in
`docs/permissions.md`: a separate user, a read-only mount, and the key readable only by the
evaluator's user. This module makes the boundary true at the process level; that step makes it true
at the permission level.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Environment variables a timing child is allowed to receive. Nothing else is forwarded, because
#: an inherited environment is the easiest way for a key to leak into a process running candidate
#: code.
PASSTHROUGH_ENV = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "PYTHONPATH")

#: Names never forwarded to a child that runs candidate code.
SECRET_ENV = ("RSI_SIGNING_KEY",)


def child_env(root: Optional[Path] = None) -> Dict[str, str]:
    """A minimal environment for a process that will import candidate code."""
    env = {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}
    for name in SECRET_ENV:
        env.pop(name, None)
    env.setdefault("PATH", os.defpath)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    # A fresh nonce the child must echo back, so a stale or injected payload cannot be replayed
    # into a result.
    env["RSI_TIMING_NONCE"] = secrets.token_hex(16)
    return env


def measure_in_child(plan: Dict[str, Any], root: Optional[Path] = None,
                     timeout: float = 3600.0) -> Dict[str, Any]:
    """Run one timing measurement in a separate interpreter and return its samples.

    The plan names what to run; the child returns raw sample lists and nothing else. All
    statistical work — the interval, the win rule, the comparison with the noise floor — stays in
    the parent, so the child cannot influence the conclusion, only the numbers it reports.
    """
    request = dict(plan)
    request["nonce"] = secrets.token_hex(16)
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "_timing_child.py")],
        input=json.dumps(request), capture_output=True, text=True, timeout=timeout,
        env=child_env(root), cwd=str(root or Path.cwd()), check=False,
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr.strip()[-2000:] or "the timing child failed"}
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": f"the timing child returned no JSON: {completed.stdout[-400:]}"}
    if payload.get("nonce") != request["nonce"]:
        return {"ok": False, "error": "the timing child's reply did not echo the request nonce"}
    return payload
