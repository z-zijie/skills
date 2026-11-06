#!/usr/bin/env python3
"""Run the documented-index builder over the repository and report its summary.

The regression case asserts that the index covers every entry and that no entry slipped in
without a source. A documented fact with no source is the failure this skill exists to prevent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from index_documented import build  # noqa: E402

TOOLING = HERE.parents[2] / "tooling"
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

from rsi import paths  # noqa: E402


def main() -> int:
    index = build(paths.repo_root())
    print(json.dumps({"entries": len(index["topic_index"]), "unsourced": index["unsourced"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
