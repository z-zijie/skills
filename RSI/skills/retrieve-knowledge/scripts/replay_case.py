#!/usr/bin/env python3
"""Offline regression driver for retrieve-knowledge.

Prints the facts a case asserts: the caps that were enforced, the match level per store, the ids
that survived, and whether the rendered context carries the warnings a thin or uncertain match
must carry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieve_knowledge import retrieve  # noqa: E402


def main() -> int:
    case = json.load(sys.stdin)
    request = {
        "signature": case["signature"],
        "kb_root": case.get("kb_root", "skills/retrieve-knowledge/tests/fixtures"),
        "include_hardware": bool(case.get("include_hardware", False)),
    }
    for key in ("caps", "ledger_file", "op"):
        if case.get(key) is not None:
            request[key] = case[key]
    outcome = retrieve(request)
    context = outcome.get("context_md") or ""
    print(json.dumps({
        "status": outcome["status"],
        "counts": outcome["counts"],
        "dropped": outcome.get("dropped"),
        "match_level": outcome["match_level"],
        "refs": outcome.get("refs"),
        "within_caps": (outcome["counts"]["rules"] <= 5
                        and outcome["counts"]["antipatterns"] <= 3
                        and outcome["counts"]["patterns"] <= 3
                        and outcome["counts"]["experiments"] <= 3),
        "relaxed_warning": "match was relaxed" in context,
        "has_sections": all(section in context for section in
                            ("## Rules", "## Patterns", "## Anti-patterns", "## Past experiments")),
        "carries_status": "(established)" in context or "(candidate)" in context,
        "problems": outcome.get("problems"),
        "note_count": len(outcome.get("notes") or []),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
