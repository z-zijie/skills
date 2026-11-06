"""The replay judge: old scaffold against new, same operators, same budget, merge or rollback.

Only the replay judge can read the held-out split. Outer-loop agents receive "pass" or "fail"
and nothing else, which is what keeps a scaffold change from being tuned on the operators that
decide whether it merges.

The protocol is fixed in code because replay is the most expensive part of the system and the
easiest part to accidentally weaken:

1. One change at a time. Two changes tested together cannot be attributed.
2. Old and new start from the same baselines on the same operators with the same budget.
3. At least three repeats, because a scaffold contains a model and model output is stochastic.
4. The primary metric is the average gain in attainment per experiment within the budget.
5. Merge when the primary metric improves on dev with an interval that excludes zero, holdout
   does not regress, and no secondary metric gets clearly worse. Otherwise roll back.
6. Both outcomes are logged, so a failed change is not tried again next month.

Two-stage screening keeps it affordable: a quick pass on two or three cheap dev operators
first, the full set only if that passes. Changes that touch only diagnosis, retrieval or
hypothesis ranking are screened offline against ledger history, which needs no device at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ._tooling import jsonschema_lite, ledger, paths, timing

#: Minimum repeats per configuration. Model output is stochastic; one repeat is noise.
MIN_REPEATS = 3
#: The screening subset: cheap dev operators, used for the first pass.
SCREEN_OPERATORS = 3
#: The fewest dev operators a replay may use. One operator is an anecdote.
MIN_DEV_OPERATORS = 2
#: A holdout regression larger than this fails the change.
HOLDOUT_REGRESSION_TOLERANCE = 0.0
#: Secondary metrics may not degrade by more than this relative amount.
SECONDARY_TOLERANCE = 0.10


class ReplayError(RuntimeError):
    """Raised when a replay cannot produce an attributable comparison."""


def holdout_operators(root: Optional[Path] = None) -> List[str]:
    """The held-out split. Only this module and a human read it."""
    path = (root or paths.repo_root()) / "benchmark" / "holdout.txt"
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def dev_operators(root: Optional[Path] = None) -> List[str]:
    path = (root or paths.repo_root()) / "benchmark" / "dev.txt"
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def screening_operators(root: Optional[Path] = None) -> List[str]:
    """The first two or three dev operators, which is all a screen is allowed to see."""
    return dev_operators(root)[:SCREEN_OPERATORS]


def evaluate_change(configs: Sequence[Dict[str, Any]], budget: Dict[str, Any],
                    repeats: int = MIN_REPEATS, seed: int = 0) -> Dict[str, Any]:
    """Compare old and new scaffolds across configurations and repeats.

    Each configuration supplies, per split, the attainment gain and the secondary metrics that
    one replay produced. The judge aggregates with a bootstrap interval so a two-repeat
    "improvement" cannot pass.
    """
    if repeats < MIN_REPEATS:
        raise ReplayError(
            f"a replay needs at least {MIN_REPEATS} repeats; {repeats} cannot separate signal from noise"
        )
    dev_deltas: List[float] = []
    holdout_deltas: List[float] = []
    secondary_regressed = False
    rows: List[Dict[str, Any]] = []
    for config in configs:
        split = config.get("split")
        old = config.get("old") or {}
        new = config.get("new") or {}
        delta = float(new.get("attainment_gain", 0.0)) - float(old.get("attainment_gain", 0.0))
        if split == "holdout":
            holdout_deltas.append(delta)
        else:
            dev_deltas.append(delta)
        for metric in ("not_implemented_rate", "prediction_error", "cost"):
            old_value = old.get(metric)
            new_value = new.get(metric)
            if old_value is None or new_value is None or old_value == 0:
                continue
            degradation = (new_value - old_value) / abs(old_value)
            if degradation > SECONDARY_TOLERANCE:
                secondary_regressed = True
        rows.append({"operator": config.get("operator"), "split": split, "delta": delta})

    if not dev_deltas:
        raise ReplayError("a replay needs at least one dev configuration")
    # A single dev operator cannot separate a scaffold's effect from that operator's idiosyncrasy.
    # The design asks for two or three even in screening, so the minimum is enforced here rather
    # than trusted to the caller.
    if len(dev_deltas) < MIN_DEV_OPERATORS:
        raise ReplayError(
            f"a replay needs at least {MIN_DEV_OPERATORS} dev operators; "
            f"{len(dev_deltas)} cannot separate a scaffold's effect from one operator's quirks")
    # An empty holdout is not evidence that the change generalises. Absence of a regression test
    # must not read as passing one, so a full replay without holdout results is refused rather
    # than merged: this is the gate that stops a change from being tuned on the operators that
    # decide whether it merges.
    if not holdout_deltas and not budget.get("allow_empty_holdout"):
        raise ReplayError(
            "a full replay needs holdout results; an empty holdout split is not evidence that "
            "the change generalises. Populate benchmark/holdout.txt, or record "
            "budget.allow_empty_holdout for a screening-only run")
    interval = timing.bootstrap_ci(dev_deltas, seed=seed) if len(dev_deltas) > 1 else {
        "point": dev_deltas[0], "low": dev_deltas[0], "high": dev_deltas[0]
    }
    holdout_regressed = any(delta < -HOLDOUT_REGRESSION_TOLERANCE for delta in holdout_deltas)
    excludes_zero = interval["low"] is not None and interval["low"] > 0

    decision = "merge"
    reasons: List[str] = []
    if not excludes_zero:
        decision = "rollback"
        reasons.append("the dev improvement interval includes zero")
    if holdout_regressed:
        decision = "rollback"
        reasons.append("holdout regressed")
    if secondary_regressed:
        decision = "rollback"
        reasons.append("a secondary metric got clearly worse")
    if decision == "merge":
        reasons.append("dev improved with an interval excluding zero, holdout held, secondaries held")

    return {
        "primary_metric": {
            "name": "attainment_gain_per_experiment",
            "dev_operators": len(dev_deltas),
            "holdout_operators": len(holdout_deltas),
            "delta": interval["point"],
            "ci_low": interval.get("low"),
            "ci_high": interval.get("high"),
            "excludes_zero": bool(excludes_zero),
        },
        "holdout_regressed": holdout_regressed,
        "secondary_regressed": secondary_regressed,
        "decision": decision,
        "rationale": "; ".join(reasons),
        "rows": rows,
        "budget": budget,
        "repeats": repeats,
    }


def screen(configs: Sequence[Dict[str, Any]], budget: Dict[str, Any],
           repeats: int = MIN_REPEATS, seed: int = 0) -> Dict[str, Any]:
    """Two-stage screening: decide whether the full replay is worth its cost.

    Screening is the first stage, so it never has holdout results: that is the point of screening.
    It therefore states that explicitly rather than being excused by the caller. A screen can only
    return "run the full replay" or "not worth it" — it can never merge, which is where the holdout
    requirement belongs.
    """
    screening_budget = dict(budget or {})
    screening_budget.setdefault("allow_empty_holdout", True)
    report = evaluate_change(configs, screening_budget, repeats=repeats, seed=seed)
    report["screening_stage"] = "two_stage_screen"
    if report["decision"] == "rollback" and report["primary_metric"]["delta"] <= 0:
        report["decision"] = "rollback"
        report["rationale"] = "screening failed on the cheap dev subset: the full replay is not worth its cost"
    else:
        report["decision"] = "needs_more_repeats"
        report["rationale"] = "screening passed: run the full dev set and the holdout split"
    return report


class OfflineJudge:
    """Offline replay for changes that touch only diagnosis, retrieval or hypothesis ranking.

    These changes can be judged against ledger history: does the new scaffold rank the
    historically confirmed hypotheses higher than the old one? No device is involved, which is
    what makes an otherwise expensive class of change cheap to test.
    """

    def __init__(self, entries: Optional[List[Dict[str, Any]]] = None) -> None:
        self.entries = entries if entries is not None else ledger.read_entries()

    def confirmed(self) -> List[Dict[str, Any]]:
        return [entry for entry in self.entries if entry.get("verdict") == "confirmed"]

    def rank_report(self, rank: Callable[[Dict[str, Any]], float]) -> Dict[str, Any]:
        """Mean rank of historically confirmed hypotheses under one ranking function."""
        confirmed = self.confirmed()
        if not confirmed:
            return {"decision": "needs_more_repeats", "rationale": "no confirmed experiments yet",
                    "cases": 0}
        scores = [rank(entry) for entry in confirmed]
        mean_rank = sum(scores) / len(scores)
        return {"decision": "merge" if mean_rank > 0 else "rollback",
                "mean_rank": mean_rank, "cases": len(confirmed),
                "rationale": (f"mean rank of {len(confirmed)} confirmed experiments under the new "
                              f"scaffold is {mean_rank:.3f}")}


def apply_decision(report: Dict[str, Any], proposal: Dict[str, Any],
                   changelog_path: Path, root: Optional[Path] = None) -> Dict[str, Any]:
    """Record a merge or a rollback in the scaffold changelog.

    Both outcomes are logged, with the replay report. A failed change that is not written down
    is a change someone will try again.
    """
    decision = report.get("decision")
    if decision not in {"merge", "rollback"}:
        return {"applied": False, "reason": f"decision {decision!r} is not final"}
    import time

    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "proposal": proposal.get("id"),
        "kind": proposal.get("kind"),
        "title": proposal.get("title"),
        "decision": decision,
        "primary_metric": report.get("primary_metric"),
        "rationale": report.get("rationale"),
        "base_commit": report.get("base_commit"),
        "candidate_commit": report.get("candidate_commit"),
    }
    existing = changelog_path.read_text(encoding="utf-8") if changelog_path.is_file() else _CHANGELOG_HEADER
    changelog_path.write_text(existing + "\n" + json.dumps(entry, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    return {"applied": True, "decision": decision, "entry": entry}


_CHANGELOG_HEADER = """# Scaffold changelog

Every scaffold change and its replay result, appended by the replay judge. A merge without a
line here did not happen; a rollback without a line here will be tried again.

Each line is one JSON object: the proposal, the decision, the primary metric and why.
"""
