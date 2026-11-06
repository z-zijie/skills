"""Single import path from the orchestrator to the shared tooling package.

The orchestrator is run as a top-level package (``orchestrator``) from the RSI root, so it
reaches ``tooling/rsi`` by putting that directory on ``sys.path`` exactly once, here. One
import site means no module has to guess how deep it sits.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TOOLING = _ROOT / "tooling"
if str(_TOOLING) not in sys.path:
    sys.path.insert(0, str(_TOOLING))

from rsi import (  # noqa: E402
    closures,
    counters,
    hardware,
    jsonschema_lite,
    knowledge,
    ledger,
    metrics,
    paths,
    signature,
    skills,
    spec,
    timing,
    yamlite,
)

__all__ = [
    "closures", "counters", "hardware", "jsonschema_lite", "knowledge", "ledger", "metrics", "paths",
    "signature", "skills", "spec", "timing", "yamlite", "ROOT",
]

ROOT = _ROOT
