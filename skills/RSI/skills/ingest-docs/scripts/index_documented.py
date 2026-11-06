#!/usr/bin/env python3
"""Rebuild the documented-layer index from the entry files.

The index is a cache: it says which topics exist and where, and it is regenerated rather than
hand-maintained. A documented entry that is not in the index is invisible to retrieval, which is
the same as not existing, so this runs as part of the ingest rather than as an afterthought.

    python3 index_documented.py [--root RSI] [--check]

``--check`` exits non-zero when the index is stale, so CI can catch an entry added without it,
and when an entry carries no source — an uncited hardware fact is the thing this layer prevents.
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

from rsi import paths, yamlite  # noqa: E402

#: Files that describe the store rather than record a fact about hardware.
NON_ENTRIES = {"index.md", "README.md", "index.yaml"}

HEADER_FIELD = re.compile(
    r"^(topic|title|source|accessed|hw_ids|last_reviewed)\s*:\s*(.+)$", re.MULTILINE)


def split_frontmatter(text: str) -> tuple:
    """Return (header mapping, body).

    An entry may carry a YAML frontmatter block or a plain header of ``key: value`` lines. Both
    are read, because the indexer's job is to cover the entries that exist rather than to force
    a rewrite of them.
    """
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                data = yamlite.loads(parts[1]) or {}
            except yamlite.YamlError:
                data = {}
            if isinstance(data, dict) and data:
                return data, parts[2]
    header: dict = {}
    for match in HEADER_FIELD.finditer(text):
        header[match.group(1)] = match.group(2).strip()
    return header, text


def _as_list(value) -> list:
    """Normalise a header field that may be a scalar or a list.

    A malformed source line parses as a mapping instead of a string; it is dropped here and then
    reported as an unsourced entry, which is the visible failure, rather than rendered into the
    index as a dict.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in (source.strip() if isinstance(source, str) else None
                                  for source in value) if item]
    text = str(value).strip()
    if text.startswith("- "):
        return [part.lstrip("- ").strip() for part in re.split(r"\n\s*-\s*", text)
                if part.lstrip("- ").strip()]
    return [item.strip() for item in text.split(",") if item.strip()]


def parse_entry(path: Path) -> dict:
    """Read the header of a documented entry plus its headline and reliability lines."""
    text = path.read_text(encoding="utf-8")
    header, body = split_frontmatter(text)
    title = str(header.get("title") or "").strip()
    if not title:
        title = next((line.lstrip("# ").strip() for line in body.splitlines()
                      if line.startswith("# ")), path.stem)
    sources = [source for source in _as_list(header.get("source")) if isinstance(source, str)]
    reliability = []
    for match in re.finditer(r"^\s*[-*]\s+\*\*(.+?)\*\*\s*[:\-]?\s*(.+)$", body, re.MULTILINE):
        reliability.append(f"{match.group(1)}: {match.group(2).strip()}")
    return {
        "topic": str(header.get("topic") or path.stem),
        "title": title,
        "path": paths.rel(path),
        "sources": sources,
        "accessed": str(header.get("accessed") or header.get("last_reviewed") or "").strip(),
        "hw_ids": _as_list(header.get("hw_ids")) or ["any"],
        "reliability": reliability,
        "has_source": bool(sources),
    }


def build(root: Path) -> dict:
    directory = root / "kb" / "hardware" / "documented"
    entries = [parse_entry(path) for path in sorted(directory.glob("*.md"))
               if path.name not in NON_ENTRIES and not path.name.startswith(".")]
    return {
        "topic_index": [
            {"topic": entry["topic"], "title": entry["title"], "path": entry["path"],
             "hw_ids": entry["hw_ids"], "source": entry["sources"], "accessed": entry["accessed"]}
            for entry in entries
        ],
        "unsourced": [entry["path"] for entry in entries if not entry["has_source"]],
        "topics": [entry["topic"] for entry in entries],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else paths.repo_root()
    index = build(root)
    target = root / "kb" / "hardware" / "documented" / "index.yaml"
    payload = {"topic_index": index["topic_index"], "unsourced": index["unsourced"]}
    rendered = yamlite.safe_dump_json_style(payload)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current.strip() != rendered.strip():
            print("the documented index is stale: rerun index_documented.py", file=sys.stderr)
            return 1
        if index["unsourced"]:
            print(f"documented entries without a source: {index['unsourced']}", file=sys.stderr)
            return 1
        return 0
    target.write_text(rendered, encoding="utf-8")
    print(json.dumps({"topics": index["topics"], "unsourced": index["unsourced"]},
                     indent=2, ensure_ascii=False))
    return 0 if not index["unsourced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
