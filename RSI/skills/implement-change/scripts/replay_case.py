#!/usr/bin/env python3
"""Offline regression driver for implement-change.

Each case copies a known kernel into a scratch workspace and hands the skill a proposed edit, so
the gate is exercised exactly as it would be in a round: apply, diff, build. Nothing is stubbed and
no model is involved, which is what makes the failure paths testable.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT  # noqa: E402
from implement_change import implement  # noqa: E402


def main() -> int:
    case = json.load(sys.stdin)
    workspace = Path(tempfile.mkdtemp(prefix="rsi-implement-"))
    source = case.get("kernel") or "operators/vec_add/baseline/kernel.py"
    shutil.copyfile(ROOT / source, workspace / "kernel.py")

    card_path = case.get("card_file", "skills/implement-change/tests/fixtures/card.json")
    card = json.loads((ROOT / card_path).read_text(encoding="utf-8"))
    edits = case.get("edits")
    if edits is None and case.get("edits_file"):
        edits = json.loads((ROOT / case["edits_file"]).read_text(encoding="utf-8"))["edits"]

    request = {
        "card": card,
        "workspace": str(workspace),
        "entry": case.get("entry", "kernel.py"),
        "edits": edits or [],
    }
    if case.get("build"):
        request["build"] = case["build"]
    if case.get("retry"):
        request["retry"] = case["retry"]
    if case.get("frozen_subdir"):
        # A path inside the workspace but in a frozen area. Copying the file rather than pointing
        # at the real tree means the check is exercised without any chance of a write landing there.
        frozen = workspace / case["frozen_subdir"]
        frozen.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, frozen)
        request["entry"] = case["frozen_subdir"]

    outcome = implement(request)
    diff = outcome.get("diff") or ""
    added = [line for line in diff.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in diff.splitlines()
               if line.startswith("-") and not line.startswith("---")]
    print(json.dumps({
        "status": outcome["status"],
        "files_changed": outcome.get("files_changed"),
        "build_ok": (outcome.get("build") or {}).get("ok"),
        "build_skipped": (outcome.get("build") or {}).get("skipped"),
        "diff_lines_added": len(added),
        "diff_lines_removed": len(removed),
        "diff_touches_hot_loop": "out[index] = a[index] + b[index]" in diff,
        "evidence_entries": len(outcome.get("evidence_map") or []),
        "problem_kinds": sorted({problem.split(":", 1)[0] for problem in outcome.get("problems") or []}),
        "notes": outcome.get("notes") or [],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
