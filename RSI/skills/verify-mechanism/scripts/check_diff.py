#!/usr/bin/env python3
"""Decide whether a diff exists on the hot path and stays inside the card's scope.

Deterministic half of group A. It parses a unified diff and answers three questions — is it
non-empty, does it touch executable code rather than comments or formatting, and does it stay
within the scope the card declared — because "the change is on the hot path" is exactly the kind
of claim that should not be a model's opinion.

    python3 check_diff.py --diff change.diff --card hypothesis.yaml [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TOOLING = Path(__file__).resolve().parents[3] / "tooling"
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

from rsi import yamlite  # noqa: E402

#: Lines that change without changing behaviour.
COSMETIC = re.compile(r"^\s*(#|//|/\*|\*|--|;;)")
#: Statements that run on the host or in setup rather than in the kernel.
HOST_HINTS = re.compile(
    r"^\s*(printf|fprintf|std::cout|print\(|logging\.|logger\.|import |from \w+ import|"
    r"assert |raise |throw )"
)
#: A keyword that marks a loop or a work-distribution decision: the hot path.
HOT_PATH = re.compile(
    r"\b(for|while|do|loop|parallel|tile|stride|index|idx|lane|work_group|blockIdx|"
    r"threadIdx|grid|local_id|global_id|vectorize|unroll)\b",
    re.IGNORECASE,
)


def parse_diff(text: str) -> dict:
    """Split a unified diff into per-file hunks and count what changed."""
    files = []
    current = None
    for line in text.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            if name.startswith(("a/", "b/")):
                name = name[2:]
            current = {"file": name, "added": [], "removed": [], "context": 0}
            files.append(current)
        elif line.startswith("--- ") or line.startswith("diff ") or line.startswith("index "):
            continue
        elif current is not None:
            if line.startswith("+"):
                current["added"].append(line[1:])
            elif line.startswith("-"):
                current["removed"].append(line[1:])
            elif line.startswith(" "):
                current["context"] += 1
    return {"files": files}


def analyse(parsed: dict) -> dict:
    """Answer A1 (hot path), A2 (scope) and A4 (flags) from the diff alone."""
    added = [line for entry in parsed["files"] for line in entry["added"]]
    removed = [line for entry in parsed["files"] for line in entry["removed"]]
    substantive_added = [line for line in added if line.strip() and not COSMETIC.match(line)]
    substantive_removed = [line for line in removed if line.strip() and not COSMETIC.match(line)]
    host_only = all(HOST_HINTS.match(line) for line in substantive_added) if substantive_added else True
    hot = any(HOT_PATH.search(line) for line in substantive_added + substantive_removed)
    flags = [line for line in substantive_added + substantive_removed
             if re.search(r"(-O[0-9s]|--fast-math|nvcc|clang\+\+|gcc|compile_options|extra_flags)", line)]
    return {
        "non_empty": bool(substantive_added or substantive_removed),
        "files_touched": [entry["file"] for entry in parsed["files"]],
        "lines_added": len(substantive_added),
        "lines_removed": len(substantive_removed),
        "touches_hot_path": bool(hot),
        "host_side_only": bool(host_only),
        "compiler_flags_changed": bool(flags),
        "flag_lines": flags,
        "A1": "pass" if (substantive_added or substantive_removed) and hot and not host_only else "fail",
        "A4": "fail" if flags else "pass",
        "notes": _notes(hot, host_only, flags),
    }


def _notes(hot: bool, host_only: bool, flags: list) -> list:
    notes = []
    if not hot:
        notes.append("no loop, traversal or work-distribution line changed: the hot path was not touched")
    if host_only:
        notes.append("every changed line looks like host-side or setup code")
    if flags:
        notes.append("compiler flags changed, which makes this a different experiment")
    return notes


def scope_check(report: dict, card: dict) -> dict:
    """A2: does the diff stay inside the card's declared scope?"""
    scope = str(card.get("scope_of_change") or "").strip()
    if not scope:
        return {"A2": "unknown", "reason": "the card declares no scope_of_change"}
    lowered = scope.lower()
    allowed_files = card.get("scope_files")
    if allowed_files:
        outside = [name for name in report["files_touched"] if name not in allowed_files]
        if outside:
            return {"A2": "fail", "reason": f"files outside scope: {outside}"}
    if any(word in lowered for word in ("only", "single", "nothing else")):
        if report["lines_added"] + report["lines_removed"] > 60:
            return {"A2": "unknown",
                    "reason": (f"the scope claims a narrow change but the diff is "
                               f"{report['lines_added'] + report['lines_removed']} substantive lines")}
    return {"A2": "pass", "reason": f"scope statement: {scope}"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--diff", required=True)
    parser.add_argument("--card")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    diff_text = Path(args.diff).read_text(encoding="utf-8")
    parsed = parse_diff(diff_text)
    report = analyse(parsed)
    if args.card:
        card = yamlite.load(args.card) or {}
        report.update(scope_check(report, card))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["A1"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
