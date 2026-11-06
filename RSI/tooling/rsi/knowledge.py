"""Knowledge base: six stores, retrieval with exact-then-relaxed tag matching, and the rule
life cycle applied mechanically.

Six stores differ in where their evidence comes from:

===================  ==============================================================
``hardware/documented``  specs and profiler metric definitions, each with a source
``hardware/measured``    parameters measured by the calibration suite
``patterns``             how to do an optimization, its mechanism, counters that move
``rules``                "under condition C, transformation T moves metric M by delta"
``antipatterns``         refuted hypotheses and known traps
``conjectures``          unverified ideas, quarantined
===================  ==============================================================

The knowledge base changes only through proposal and merge; the inner loop appends
experiments, never entries. The life cycle in ``RULES.md`` is applied by ``lifecycle()`` here,
so a status change is a mechanical decision over evidence counts rather than a judgement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import jsonschema_lite, paths, signature as signature_mod, yamlite

STORES = (
    "hardware/documented", "hardware/measured", "patterns", "rules", "antipatterns",
    "conjectures", "proposals",
)

#: Starting values from the build plan; adjust with data, not with taste.
ESTABLISHED_MIN_CONFIRMATIONS = 3
ESTABLISHED_MIN_OPERATORS = 2
ESTABLISHED_MIN_CONFIRM_RATE = 0.70
DEPRECATED_BELOW_CONFIRM_RATE = 0.40
#: Prediction error above this means the hardware model has a hole (|log ratio| > 0.2).
PREDICTION_ERROR_HOLE = 0.2

#: Retrieval caps. Stuffing the whole knowledge base into a context costs more and dilutes
#: attention, so each retrieval returns a few relevant entries.
RETRIEVAL_CAPS = {"rules": 5, "antipatterns": 3, "experiments": 3, "patterns": 3}


def store_dir(store: str, root: Optional[Path] = None) -> Path:
    return paths.kb_dir(root) / store


def load_store(store: str, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every entry file under a store, each carrying its own path under ``$path``.

    The path is attached here so a caller can point at the file it came from, and it is dropped
    again by ``without_provenance`` before validation: an annotation added for convenience must
    not leak into the entry that a schema checks.
    """
    directory = store_dir(store, root)
    if not directory.is_dir():
        return []
    entries: List[Dict[str, Any]] = []
    for path in sorted(directory.rglob("*.yaml")):
        if path.name.startswith(("thresholds", "device")):
            continue
        data = yamlite.load(path)
        if isinstance(data, dict):
            data.setdefault("$path", paths.rel(path, root))
            entries.append(data)
    return entries


def without_provenance(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The entry as it was written, without the path annotation load_store attaches."""
    return {key: value for key, value in entry.items() if not key.startswith("$")}


def rule_schema(root: Optional[Path] = None) -> Dict[str, Any]:
    return jsonschema_lite.load_schema(str((root or paths.repo_root()) / "schemas" / "rule.schema.json"))


def validate_rule(rule: Dict[str, Any], root: Optional[Path] = None) -> List[str]:
    errors = jsonschema_lite.validate(without_provenance(rule), rule_schema(root))
    for metric in [rule.get("effect", {}).get("metric")] + list(rule.get("expected_counters") or []):
        if metric and metric not in _known_metrics():
            errors.append(f"${rule.get('id')}: unknown canonical metric {metric!r}")
    return errors


def _known_metrics() -> Iterable[str]:
    from . import metrics

    return metrics.METRICS


# -- retrieval ------------------------------------------------------------

def retrieve(sig: Dict[str, Any], root: Optional[Path] = None,
             caps: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Rules, anti-patterns, patterns and similar experiments matching a signature.

    Exact tag match first, then relaxation: when nothing matches exactly we retry with the
    hardware constraint relaxed, and finally with tags relaxed to "any". Every returned entry
    carries the match level that produced it, so a thin match is visibly thin.
    """
    limits = dict(RETRIEVAL_CAPS)
    limits.update(caps or {})
    rules = load_store("rules", root)
    antipatterns = load_store("antipatterns", root)
    patterns = load_store("patterns", root)

    matched_rules, level_rules = _match_with_relaxation(rules, sig)
    matched_anti, level_anti = _match_with_relaxation(antipatterns, sig)
    matched_patterns, level_patterns = _match_with_relaxation(patterns, sig)

    return {
        "signature": sig,
        "match_level": {"rules": level_rules, "antipatterns": level_anti, "patterns": level_patterns},
        "rules": _annotate_status(matched_rules[: limits["rules"]]),
        "antipatterns": matched_anti[: limits["antipatterns"]],
        "patterns": matched_patterns[: limits["patterns"]],
    }


def _annotate_status(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for entry in entries:
        copy = dict(entry)
        status = copy.get("status")
        if status == "candidate":
            copy["_warning"] = "thin evidence: confirmed at least once, not yet established"
        elif status == "under_review":
            copy["_warning"] = "under review: calibration changed, a counterexample appeared, or prediction error is high"
        elif status == "conjecture":
            copy["_warning"] = "unverified: may inspire hypotheses, cannot be evidence"
        out.append(copy)
    return out


def _match_with_relaxation(entries: List[Dict[str, Any]], sig: Dict[str, Any]
                           ) -> Tuple[List[Dict[str, Any]], str]:
    levels = [
        ("exact", lambda scope: scope),
        ("hw_relaxed", lambda scope: {key: value for key, value in scope.items() if key != "hw"}),
        ("tags_any", lambda scope: {key: value for key, value in scope.items()
                                    if key not in {"tags_all", "classes", "hw"}}),
    ]
    for name, relax in levels:
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for entry in entries:
            scope = relax(entry.get("scope") or {})
            score = signature_mod.match_score(sig, scope)
            if score:
                scored.append((score, entry))
        if scored:
            scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
            return [entry for _, entry in scored], name
    return [], "none"


def similar_experiments(sig: Dict[str, Any], entries: List[Dict[str, Any]],
                        limit: int = 3) -> List[Dict[str, Any]]:
    """The most similar past experiments, by signature overlap then same operator."""
    scored: List[Tuple[int, Dict[str, Any]]] = []
    tags = set(sig.get("tags") or [])
    for entry in entries:
        entry_sig = entry.get("signature") or {}
        overlap = len(tags & set(entry_sig.get("tags") or []))
        score = 2 * overlap
        if entry.get("op") == sig.get("op"):
            score += 3
        if entry_sig.get("class") == sig.get("class"):
            score += 1
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("exp_id", ""))))
    return [entry for _, entry in scored[:limit]]


def measured_parameters(sig: Dict[str, Any], root: Optional[Path] = None) -> Dict[str, Any]:
    """The measured hardware parameters relevant to a signature's class."""
    from . import hardware

    try:
        measured = hardware.load_measured(str(sig.get("hw_id", "")), root)
    except hardware.HardwareError:
        return {}
    keep = ["peak_bandwidth_gbps", "peak_flops", "cache", "launch", "noise_floor", "env_fingerprint",
            "measured_at", "drift"]
    selected = {key: measured[key] for key in keep if key in measured}
    return selected


# -- rule life cycle ------------------------------------------------------

def lifecycle(rule: Dict[str, Any], operator_of_exp: Dict[str, str],
              hardware_changed: bool = False) -> Dict[str, Any]:
    """Decide a rule's status from its evidence, and why.

    The thresholds are the suggested starting values from the build plan. The function returns
    a status and a reason so the decision is auditable and reproducible.
    """
    evidence = rule.get("evidence") or {}
    confirmed = list(evidence.get("confirmed") or [])
    refuted = list(evidence.get("refuted") or [])
    operators = {operator_of_exp.get(exp_id) for exp_id in confirmed + refuted}
    operators.discard(None)
    attempts = len(confirmed) + len(refuted)
    rate = (len(confirmed) / attempts) if attempts else None

    if not attempts:
        return {"status": "conjecture", "reason": "no experimental evidence yet"}

    if hardware_changed:
        return {
            "status": "under_review",
            "reason": "bound hardware changed: demoted to candidate and re-verified",
        }

    if rate is not None and rate < DEPRECATED_BELOW_CONFIRM_RATE:
        return {
            "status": "deprecated",
            "reason": f"confirmation rate {rate:.2f} is below {DEPRECATED_BELOW_CONFIRM_RATE:.2f}",
        }

    error = rule.get("mean_prediction_error")
    if error is not None and error > PREDICTION_ERROR_HOLE:
        return {
            "status": "under_review",
            "reason": f"mean prediction error {error:.2f} exceeds {PREDICTION_ERROR_HOLE}: scope is probably too wide",
        }

    if (len(confirmed) >= ESTABLISHED_MIN_CONFIRMATIONS
            and len(operators) >= ESTABLISHED_MIN_OPERATORS
            and rate is not None and rate >= ESTABLISHED_MIN_CONFIRM_RATE):
        return {
            "status": "established",
            "reason": (f"{len(confirmed)} confirmations across {len(operators)} operators, "
                       f"rate {rate:.2f}"),
        }

    return {
        "status": "candidate",
        "reason": (f"{len(confirmed)} confirmation(s) across {len(operators)} operator(s): "
                   f"needs {ESTABLISHED_MIN_CONFIRMATIONS} across {ESTABLISHED_MIN_OPERATORS}"),
    }


def confirm_rate(rule: Dict[str, Any]) -> Optional[float]:
    evidence = rule.get("evidence") or {}
    confirmed = len(evidence.get("confirmed") or [])
    refuted = len(evidence.get("refuted") or [])
    total = confirmed + refuted
    return (confirmed / total) if total else None


def reservoir_signature_scope(tag: str, hw: str) -> Dict[str, Any]:
    """The minimal scope a distilled rule starts with."""
    return {"hw": [hw], "tags_all": [tag] if tag else []}
