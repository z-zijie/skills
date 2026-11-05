#!/usr/bin/env python3
"""record-experiment: validate one finished experiment, archive it, append one ledger line.

Three refusals are structural rather than advisory, because they are the only things standing
between a measured experiment and a fabricated one:

* an evaluator result whose signature does not verify is never recorded;
* an ``exp_id`` that already exists is never written twice, since the ledger is append-only;
* a counter name that is not in the canonical registry is never stored, since no rule could be
  scoped to it later.

Everything else is derived: speedup and significance come from the signed result, the prediction
error from the card's predicted speedup, and the scaffold commit from the working tree. A request
may supply a ``root`` so the regression cases exercise the same code against a scratch ledger.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _shared import ROOT, emit, jsonschema_lite, ledger, metrics, paths, read_request, shared_schema, yamlite  # noqa: E402
from rsi import timing  # noqa: E402

REQUIRED_ARTIFACTS = {"diff_file": "diff.patch", "profile_file": "profile.json",
                      "binary_file": "binary.json"}


def resolve(root: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    if candidate.is_file():
        return candidate.resolve()
    return root / candidate


def load_document(root: Path, inline: Any, named: Any) -> Optional[Dict[str, Any]]:
    if isinstance(inline, dict):
        return inline
    if isinstance(named, str) and named:
        path = resolve(root, named)
        if not path.is_file():
            return None
        if path.suffix in {".yaml", ".yml"}:
            data = yamlite.load(path)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    return None


def check_schema(document: Any, schema_name: str, label: str) -> List[str]:
    try:
        jsonschema_lite.check(document, shared_schema(schema_name), label=label)
    except jsonschema_lite.ValidationError as error:
        return [f"schema: {line}" for line in error.errors]
    return []


def archive(root: Path, exp_id: str, request: Dict[str, Any]) -> Dict[str, List[str]]:
    """Copy each named artifact into ``ledger/artifacts/<exp_id>/``, keeping its base name."""
    archived: List[str] = []
    missing: List[str] = []
    for field, target in REQUIRED_ARTIFACTS.items():
        value = request.get(field)
        if not value:
            missing.append(field)
            continue
        source = resolve(root, str(value))
        if not source.is_file():
            missing.append(f"{field}={value}")
            continue
        destination = paths.artifacts_dir(root) / exp_id / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        archived.append(paths.rel(destination, root))
    return {"archived": archived, "missing": missing}


def build_entry(root: Path, request: Dict[str, Any], result: Dict[str, Any],
                exp_id: str) -> Dict[str, Any]:
    """Assemble the ledger entry. Every value is copied from an artifact or the environment."""
    card = load_document(root, request.get("hypothesis"), request.get("hypothesis_file")) or {}
    signature = load_document(root, request.get("signature"), request.get("signature_file"))
    timing_after = result.get("timing") or {}
    speedup = timing_after.get("speedup")
    predicted = ((card.get("predictions") or {}).get("speedup") or {}).get("expect")
    entry: Dict[str, Any] = {
        "exp_id": exp_id,
        "op": str(request.get("op") or result.get("op") or ""),
        "hw_id": str(request.get("hw_id") or result.get("hw_id") or ""),
        "env_fingerprint": str(result.get("env_fingerprint") or ""),
        "scaffold_commit": ledger.scaffold_commit(root),
        "parent": str(request.get("parent") or ""),
        "created": str(result.get("created") or ""),
        "hypothesis": card,
        "correctness": {
            "passed": bool((result.get("correctness") or {}).get("passed")),
            "failing_cases": [
                str(item.get("reason") or item.get("shape") or "case")
                for item in ((result.get("correctness") or {}).get("failures") or [])
            ],
        },
        "timing_after": {
            "median_us": timing_after.get("candidate_us"),
            "ci_low_us": timing_after.get("speedup_ci_low"),
            "ci_high_us": timing_after.get("speedup_ci_high"),
            "samples": int(timing_after.get("samples") or 0),
        },
        "verdict": str(request.get("verdict")),
        "speedup": speedup,
        "significant": timing_after.get("significant"),
        "implausible": bool((result.get("guards") or {}).get("implausible_speedup")),
        "prediction_error": timing.log_ratio_error(speedup, predicted if isinstance(predicted, (int, float)) else None),
        "cost": request.get("cost") or {},
        "evaluator_signature": json.dumps((result.get("signature") or {}), sort_keys=True),
    }
    if signature:
        entry["signature"] = signature
    if request.get("timing_before"):
        entry["timing_before"] = request["timing_before"]
    if request.get("counters_before"):
        entry["counters_before"] = metrics.normalise(request["counters_before"])
    if request.get("counters_after"):
        entry["counters_after"] = metrics.normalise(request["counters_after"])
    if request.get("verifier_notes") is not None:
        entry["verifier_notes"] = request["verifier_notes"]
    if request.get("dry_run"):
        entry["promoted"] = False
    return entry


def record(request: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(str(request["root"])).resolve() if request.get("root") else ROOT
    notes: List[str] = []
    problems: List[str] = []
    empty = {"status": "unavailable", "exp_id": None, "entry": None, "archived": [],
             "missing_artifacts": [], "problems": [], "notes": []}

    result_path = resolve(root, str(request.get("result_file") or ""))
    if not result_path.is_file():
        return dict(empty, problems=[f"unavailable: no evaluator result at {request.get('result_file')}"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if isinstance(result.get("result"), dict):
        result = result["result"]

    try:
        ledger.verify(result, root=root)
    except (ledger.SignatureError, OSError, ValueError) as error:
        return dict(empty, problems=[f"signature: {error}"])

    problems.extend(check_schema(result, "result.schema.json", "result"))
    card = load_document(root, request.get("hypothesis"), request.get("hypothesis_file"))
    if card is not None:
        problems.extend(check_schema(card, "hypothesis.schema.json", "hypothesis"))
    signature = load_document(root, request.get("signature"), request.get("signature_file"))
    if signature is not None:
        problems.extend(check_schema(signature, "signature.schema.json", "signature"))
    if problems:
        return dict(empty, problems=problems, notes=notes)

    existing = ledger.read_entries(root)
    exp_id = str(request.get("exp_id") or ledger.next_exp_id(existing, root))
    if any(item.get("exp_id") == exp_id for item in existing):
        return dict(empty, exp_id=exp_id,
                    problems=[f"duplicate: {exp_id} already exists; the ledger is append-only"])

    entry = build_entry(root, request, result, exp_id)
    unknown = []
    for field in ("counters_before", "counters_after"):
        unknown.extend(metrics.check_names(list((entry.get(field) or {}).keys())))
    if unknown:
        return dict(empty, exp_id=exp_id,
                    problems=[f"unregistered_metric: {sorted(set(unknown))} are not canonical; "
                              "add them to docs/vocabulary.md before recording"])
    try:
        ledger.validate_entry(entry, root)
    except jsonschema_lite.ValidationError as error:
        return dict(empty, exp_id=exp_id, problems=[f"schema: {line}" for line in error.errors])
    except ledger.LedgerError as error:
        return dict(empty, exp_id=exp_id, problems=[f"schema: {error}"])

    if request.get("dry_run"):
        return {"status": "dry_run", "exp_id": exp_id, "entry": entry, "archived": [],
                "missing_artifacts": [], "problems": [], "notes": ["validated; nothing appended"]}

    archived = archive(root, exp_id, request)
    refs = {field: paths.rel(paths.artifacts_dir(root) / exp_id / target, root)
            for field, target in REQUIRED_ARTIFACTS.items()
            if (paths.artifacts_dir(root) / exp_id / target).is_file()}
    entry.update({field.replace("_file", "_ref"): ref for field, ref in refs.items()})
    entry["result_ref"] = paths.rel(result_path, root)
    ledger.archive_artifact(exp_id, "result.json", result, root)

    try:
        ledger.append(entry, root)
    except ledger.LedgerError as error:
        return dict(empty, exp_id=exp_id, problems=[f"duplicate: {error}"])
    if archived["missing"]:
        notes.append(f"artifacts named but absent, recorded rather than invented: {archived['missing']}")
    return {"status": "ok", "exp_id": exp_id, "entry": entry,
            "archived": archived["archived"] + [entry["result_ref"]],
            "missing_artifacts": archived["missing"], "problems": [], "notes": notes}


def main(argv: List[str]) -> int:
    outcome = record(read_request(argv))
    status = outcome["status"]
    if outcome["problems"]:
        first = outcome["problems"][0].split(":", 1)[0]
        outcome["status"] = {
            "signature": "refused_unsigned",
            "duplicate": "refused_duplicate",
            "unregistered_metric": "refused_unregistered_metric",
            "schema": "schema_invalid",
            "unavailable": "unavailable",
        }.get(first, status)
    return emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
