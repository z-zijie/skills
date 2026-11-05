"""Bridge from the evaluator to the shared tooling package.

``tooling/rsi`` holds everything that is not device-specific: schemas, units, statistics,
signatures, the ledger. The evaluator imports it through here so there is exactly one import
path and no copy of the tooling inside Layer 0. Layer 0 itself never imports a skill.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional


def _ensure_tooling_on_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    tooling = root / "tooling"
    if str(tooling) not in sys.path:
        sys.path.insert(0, str(tooling))
    return tooling


_ensure_tooling_on_path()

from rsi import (  # noqa: E402
    counters,
    hardware,
    jsonschema_lite,
    knowledge,
    ledger,
    metrics,
    paths,
    signature,
    spec,
    timing,
    yamlite,
)

__all__ = [
    "counters", "hardware", "jsonschema_lite", "knowledge", "ledger", "metrics", "paths",
    "signature", "spec", "timing", "yamlite", "load_kernel_module", "load_module_from_path",
    "device_config",
]


def load_module_from_path(name: str, path: Path) -> ModuleType:
    """Import a single file as a module, under a deterministic name."""
    path = Path(path)
    cache_key = f"{name}@{path}"
    if cache_key in sys.modules:
        return sys.modules[cache_key]
    spec_obj = importlib.util.spec_from_file_location(cache_key, str(path))
    if spec_obj is None or spec_obj.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[cache_key] = module
    spec_obj.loader.exec_module(module)
    return module


def load_kernel_module(module_name: str, directory: Path, backend: Any) -> ModuleType:
    """Load a kernel module by name from a candidate or reference directory.

    A module is one file, ``<module_name>.py``, whose entry function takes
    ``(inputs, out, case, dtype)``. When a module directory contains a compiled artefact the
    spec's ``entry.compile`` block is run first; a module that failed to build is reported as
    a build failure rather than silently falling back.
    """
    candidate = Path(module_name)
    if candidate.suffix == ".py":
        path = candidate if candidate.is_absolute() else Path(directory) / candidate
    else:
        path = Path(directory) / f"{module_name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"no kernel module at {path}")
    return load_module_from_path(path.stem, path)


def device_config() -> Dict[str, Any]:
    """The local device description, or an empty mapping when the machine declares none."""
    try:
        return hardware.discover_device()
    except Exception:  # pragma: no cover - defensive: a broken file must not stop the CLI
        return {}
