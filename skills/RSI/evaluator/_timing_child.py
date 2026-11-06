#!/usr/bin/env python3
"""The timing child: runs candidate code in its own interpreter.

Kept deliberately small. It reads a plan on stdin, imports the two kernel modules, times them, and
prints the raw samples with the request nonce. It never sees the signing key — the parent strips it
from the environment — and it holds no reference output, so there is nothing here worth cheating
for beyond the numbers it is already asked to produce.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluator.harness import timing as protocol  # noqa: E402
from evaluator import backends  # noqa: E402
from evaluator.tooling_path import spec as spec_mod  # noqa: E402


def main() -> int:
    request = json.load(sys.stdin)
    op_spec = spec_mod.Spec.load(request["op"], Path(request["root"]))
    backend = backends.select(request.get("backend"), request.get("device") or {})
    # Setup is what happens inside this child before the timing loop: building the arms and the
    # buffers. That is the cost the spec's cap governs. Starting this interpreter is not the
    # candidate's setup, so it is reported separately rather than charged against it.
    setup_start = backend.now()
    try:
        arms = protocol.build_arms(
            op_spec, request["candidate"], request["parent"], backend,
            case=request.get("case") or None, seed=int(request.get("seed", 0)),
            verify_output=bool(request.get("verify_output", True)),
        )
    except protocol.TimingError as error:
        # A refused candidate is a result, not a crash: report it in the same channel as a
        # measurement so the parent can print one sentence instead of a traceback.
        print(json.dumps({"ok": False, "nonce": request["nonce"], "refused": True,
                          "error": str(error)}))
        return 0
    setup_us = (backend.now() - setup_start) * 1e6
    try:
        samples = protocol.interleaved(arms["candidate"], arms["parent"], backend,
                                       samples=int(request.get("samples", protocol.DEFAULT_SAMPLES)),
                                       warmup=int(request.get("warmup", protocol.DEFAULT_WARMUP)))
    except protocol.TimingError as error:
        print(json.dumps({"ok": False, "nonce": request["nonce"], "refused": True,
                          "error": str(error)}))
        return 0
    print(json.dumps({"ok": True, "nonce": request["nonce"],
                      "candidate": samples["candidate"], "parent": samples["parent"],
                      "backend": backend.name, "case": arms["case"],
                      "setup_us": setup_us, "process_launch_us": 0.0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
