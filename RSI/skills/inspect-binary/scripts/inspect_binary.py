#!/usr/bin/env python3
"""inspect-binary: reduce a disassembly dump to a canonical machine-level summary.

The toolchain lives outside this script. Everything toolchain-shaped arrives as an adapter file
(``adapters/generic-text.yaml`` by default): the argv that produces a dump, the line grammar, the
named symbol patterns and the instruction classes. Swap the adapter and the same code reads a
different machine, which is the whole reason the adapter is a file and not a branch.

Everything the adapter declares but the dump does not yield is reported in ``missing``. A register
count nobody could read is unknown; writing zero would read as "this kernel uses no registers".
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, read_request, yamlite  # noqa: E402

DEFAULT_ADAPTER = ROOT / "skills" / "inspect-binary" / "adapters" / "generic-text.yaml"
NUMERIC_FIELDS = ("registers_per_lane", "scratchpad_bytes_per_workgroup", "spill_bytes",
                  "work_group", "work_groups", "lane_count", "barrier_count")


def load_adapter(path: Optional[str]) -> Dict[str, Any]:
    """The device adapter, or the shipped generic one. A malformed adapter fails loudly."""
    target = Path(path) if path else DEFAULT_ADAPTER
    if not target.is_absolute() and not target.is_file():
        target = ROOT / target
    if not target.is_file():
        raise ValueError(f"no adapter file at {target}")
    data = yamlite.load(target)
    if not isinstance(data, dict):
        raise ValueError(f"{target}: an adapter must be a mapping")
    for key in ("name", "version", "line"):
        if key not in data:
            raise ValueError(f"{target}: adapter is missing {key!r}")
    if not isinstance(data.get("symbols"), dict):
        raise ValueError(f"{target}: adapter declares no symbols block")
    data["_path"] = str(target)
    return data


def obtain_dump(request: Dict[str, Any], adapter: Dict[str, Any]) -> Dict[str, Any]:
    """Read the saved dump, or run the adapter's own disassembly command over the artefact."""
    supplied = request.get("disassembly_file")
    if supplied:
        path = Path(str(supplied))
        if not path.is_absolute() and not path.is_file():
            path = ROOT / path
        if not path.is_file():
            return {"ok": False, "reason": f"no disassembly dump at {supplied}"}
        return {"ok": True, "text": path.read_text(encoding="utf-8", errors="replace"),
                "source": str(path), "command": []}

    command = (adapter.get("disassembly") or {}).get("command") or []
    if not command:
        return {"ok": False, "reason": "the adapter declares no disassembly command and no dump was supplied"}
    artifact = Path(str(request["artifact"]))
    if not artifact.is_absolute() and not artifact.is_file():
        artifact = ROOT / artifact
    if not artifact.is_file():
        return {"ok": False, "reason": f"no compiled artefact at {request['artifact']}"}
    argv = [part.replace("{artifact}", str(artifact)) for part in command]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False)
    if completed.returncode != 0:
        return {"ok": False, "reason": f"disassembly command exited {completed.returncode}: "
                                       f"{completed.stderr.strip()[-300:]}"}
    return {"ok": True, "text": completed.stdout, "source": "", "command": argv}


def instruction_lines(text: str, adapter: Dict[str, Any]) -> List[Dict[str, str]]:
    """Every line the adapter's grammar calls an instruction, with its mnemonic and operands."""
    grammar = adapter.get("line") or {}
    pattern = re.compile(str(grammar.get("instruction") or ""))
    skip_prefixes = tuple(grammar.get("skip_prefixes") or ())
    skip_mnemonics = {str(name).split(".")[0] for name in (grammar.get("skip_mnemonics") or [])}
    found: List[Dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(skip_prefixes):
            continue
        match = pattern.match(line)
        if not match:
            continue
        groups = match.groupdict()
        mnemonic = (groups.get("mnemonic") or "").strip()
        if not mnemonic or mnemonic.split(".")[0] in skip_mnemonics:
            continue
        found.append({"mnemonic": mnemonic, "operands": (groups.get("operands") or "").strip(),
                      "text": line})
    return found


def count_classes(instructions: List[Dict[str, str]], adapter: Dict[str, Any]) -> Dict[str, int]:
    """First matching class wins, so a wide load is not also counted as a compute op."""
    classes = []
    for entry in adapter.get("instruction_classes") or []:
        if not isinstance(entry, dict) or not entry.get("pattern"):
            continue
        classes.append((str(entry.get("name") or "other"), re.compile(str(entry["pattern"]))))
    fallback = str(adapter.get("fallback_class") or "other")
    mix: Dict[str, int] = {}
    for instruction in instructions:
        line = instruction["text"]
        chosen = fallback
        for name, compiled in classes:
            if compiled.search(line):
                chosen = name
                break
        mix[chosen] = mix.get(chosen, 0) + 1
    return mix


def extract_symbols(text: str, adapter: Dict[str, Any]) -> Dict[str, Any]:
    """Apply each declared symbol pattern; a numeric symbol with no match is absent, not zero."""
    values: Dict[str, Any] = {}
    missing: List[str] = []
    for name, rule in (adapter.get("symbols") or {}).items():
        if not isinstance(rule, dict) or not rule.get("pattern"):
            continue
        matches = re.findall(str(rule["pattern"]), text, flags=re.MULTILINE)
        if rule.get("count"):
            values[name] = len(matches)
            continue
        if not matches:
            missing.append(name)
            continue
        first = matches[0]
        try:
            values[name] = int(first)
        except (TypeError, ValueError):
            missing.append(name)
    return {"values": values, "missing": missing}


def derive_launch(request: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """Launch geometry from the launch request, with any dump figure taking precedence.

    ``source`` names where the geometry came from: ``dump`` when the disassembly declared it,
    ``derived`` when it was computed from the work-group size and the problem size. A derived
    work-group count is not a missing symbol, so it is removed from the missing list by the
    caller that knows the difference.
    """
    from_dump = any(values.get(key) is not None for key in ("work_group", "work_groups", "lane_count"))
    work_group = values.get("work_group") or request.get("work_group")
    lane_count = values.get("lane_count") or request.get("lane_count")
    work_groups = values.get("work_groups")
    launch: Dict[str, Any] = {"source": "dump" if from_dump else "derived"}
    if work_group:
        launch["work_group"] = int(work_group)
    if work_groups is not None:
        launch["work_groups"] = int(work_groups)
    elif work_group and lane_count:
        launch["work_groups"] = -(-int(lane_count) // int(work_group))
    if lane_count:
        launch["lane_count"] = int(lane_count)
    return launch


def summarize(request: Dict[str, Any]) -> Dict[str, Any]:
    notes: List[str] = []
    problems: List[str] = []
    try:
        adapter = load_adapter(request.get("adapter_file"))
    except (ValueError, OSError) as error:
        return {"status": "adapter_invalid", "summary": None, "problems": [f"adapter: {error}"],
                "notes": notes}

    dump = obtain_dump(request, adapter)
    if not dump["ok"]:
        return {"status": "unavailable", "summary": None, "problems": [f"unavailable: {dump['reason']}"],
                "notes": notes, "dump_ref": ""}

    instructions = instruction_lines(dump["text"], adapter)
    if not instructions:
        problems.append("adapter: the dump yielded no instruction lines; check the line grammar")
        return {"status": "adapter_invalid", "summary": None, "problems": problems, "notes": notes,
                "dump_ref": dump["source"]}
    mix = count_classes(instructions, adapter)
    symbols = extract_symbols(dump["text"], adapter)
    values = symbols["values"]
    launch = derive_launch(request, values)
    missing = [name for name in symbols["missing"]
               if not (name in launch and launch.get(name) is not None)]

    summary: Dict[str, Any] = {
        "artifact": str(request["artifact"]),
        "adapter": str(adapter["name"]),
        "adapter_version": str(adapter["version"]),
        "instruction_total": len(instructions),
        "instruction_mix": dict(sorted(mix.items())),
        "missing": sorted(missing),
        "launch": launch,
    }
    if request.get("variant"):
        summary["variant"] = str(request["variant"])
    for field in ("registers_per_lane", "scratchpad_bytes_per_workgroup", "spill_bytes", "barrier_count"):
        summary[field] = values.get(field)

    capacity = request.get("scratchpad_capacity_bytes")
    if capacity:
        summary["scratchpad_capacity_bytes"] = int(capacity)
        used = values.get("scratchpad_bytes_per_workgroup")
        summary["scratchpad_fraction"] = round(float(used) / float(capacity), 4) if used is not None else None
        if used is not None and float(used) / float(capacity) > 0.9:
            notes.append("scratchpad use is above nine tenths of the declared capacity; "
                         "occupancy may be limited by it")

    if missing:
        notes.append(f"{len(missing)} declared symbol(s) did not appear in the dump and could not be derived; "
                     "recorded as unknown rather than zero")
    return {"status": "ok", "summary": summary, "problems": [], "notes": notes,
            "dump_ref": dump["source"]}


def main(argv: List[str]) -> int:
    request = read_request(argv)
    outcome = summarize(request)
    if outcome.get("summary"):
        try:
            schema = jsonschema_lite.load_schema(
                str(ROOT / "skills" / "inspect-binary" / "contract" / "output.schema.json"))
            jsonschema_lite.check({"status": outcome["status"], "summary": outcome["summary"],
                                   "problems": outcome["problems"], "notes": outcome["notes"],
                                   "dump_ref": outcome.get("dump_ref", "")},
                                  schema, label="inspect-binary")
        except jsonschema_lite.ValidationError as error:
            outcome["status"] = "schema_invalid"
            outcome["problems"] = list(outcome["problems"]) + [f"schema: {line}" for line in error.errors]
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
