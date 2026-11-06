"""Model routing.

Model tiers are not written inside skills. They live here, together, because swapping a model
is a scaffold change that goes through replay like any other.

The coordination is deliberately cheap and the execution is not: the planner's model made
little difference in the published ablations, while the executor's capability mattered more.
So the implementer's tier is the one to calibrate, using the not-implemented rate as the
signal, and the implementer is the one to upgrade first when that rate stays high.

The tiers named here are roles, not products. ``orchestrator/adapters/`` maps a role to
whatever model this deployment runs it with, so the same policy works whether the strong tier
is one vendor's frontier model or another's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ._tooling import paths, yamlite

#: The routing policy shipped with the framework. Editing it is a scaffold change.
DEFAULT_ROUTING: Dict[str, Dict[str, Any]] = {
    "orchestrate-campaign": {"tier": "script"},
    "run-evaluator": {"tier": "script"},
    "collect-profile": {"tier": "script"},
    "record-experiment": {"tier": "script"},
    "inspect-binary": {"tier": "script"},
    "calibrate-hardware": {"tier": "script"},
    "replay-benchmark": {"tier": "script"},
    "diagnose-bottleneck": {"tier": "mid", "fallback": "strong"},
    "retrieve-knowledge": {"tier": "cheap"},
    "propose-hypothesis": {"tier": "strong", "retries": 1},
    "implement-change": {"tier": "cheap", "retries": 2},
    "verify-mechanism": {"tier": "strong", "isolated_context": True},
    "plan-campaign": {"tier": "strong"},
    "revisit-closures": {"tier": "strong"},
    "distill-rules": {"tier": "strong"},
    "analyze-failures": {"tier": "strong"},
    "audit-knowledge": {"tier": "mid"},
    "edit-scaffold": {"tier": "strong"},
    "design-microbench": {"tier": "strong", "requires_approval": True},
    "capture-correction": {"tier": "mid"},
    "register-conjecture": {"tier": "mid"},
    "report-campaign": {"tier": "cheap"},
    "ingest-docs": {"tier": "mid"},
}

#: Retry ceilings per skill, unless the routing file overrides them.
DEFAULT_RETRIES = 0
#: The not-implemented rate above which the implementer's tier should be raised.
IMPLEMENTER_UPGRADE_RATE = 0.30


def models_file(root: Optional[Path] = None) -> Path:
    return (root or paths.repo_root()) / "orchestrator" / "models.yaml"


def load_routing(root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """The live routing policy: shipped defaults with the file's overrides applied."""
    routing = {name: dict(policy) for name, policy in DEFAULT_ROUTING.items()}
    path = models_file(root)
    if path.is_file():
        data = yamlite.load(path) or {}
        overrides = data.get("skills") or {}
        for name, policy in overrides.items():
            if isinstance(policy, dict):
                merged = routing.get(name, {})
                merged.update(policy)
                routing[name] = merged
    return routing


def policy_for(skill: str, root: Optional[Path] = None) -> Dict[str, Any]:
    routing = load_routing(root)
    if skill not in routing:
        raise KeyError(f"no model routing for {skill!r}; add it to orchestrator/models.yaml")
    return routing[skill]


def tier_of(skill: str, root: Optional[Path] = None) -> str:
    return str(policy_for(skill, root)["tier"])


def retries_for(skill: str, root: Optional[Path] = None) -> int:
    return int(policy_for(skill, root).get("retries", DEFAULT_RETRIES))


def is_isolated(skill: str, root: Optional[Path] = None) -> bool:
    return bool(policy_for(skill, root).get("isolated_context", False))


def implementer_should_upgrade(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Whether the implementer's tier should rise, and why.

    The not-implemented rate is the signal: it measures the implementer's capability and the
    clarity of the cards, and it is the metric the published ablations tied to executor
    quality. A high rate means the change is not landing, not that the hypothesis is wrong.
    """
    rate = metrics.get("not_implemented_rate")
    if rate is None:
        return {"upgrade": False, "reason": "no not-implemented rate yet"}
    if rate >= IMPLEMENTER_UPGRADE_RATE:
        return {
            "upgrade": True,
            "reason": (f"not-implemented rate {rate:.0%} is at or above "
                       f"{IMPLEMENTER_UPGRADE_RATE:.0%}: upgrade the implementer before the planner"),
        }
    return {"upgrade": False, "reason": f"not-implemented rate {rate:.0%} is healthy"}


def propose_routing_change(skill: str, tier: str, reason: str) -> Dict[str, Any]:
    """A model-routing change is proposed, never applied: it changes cost directly."""
    return {
        "kind": "model_routing",
        "target": f"orchestrator/models.yaml#{skill}",
        "title": f"Route {skill} to the {tier} tier",
        "rationale": reason,
        "gate": "human_only",
        "status": "needs_human",
        "diff": f"skills:\n  {skill}:\n    tier: {tier}\n",
    }


def summary(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    routing = load_routing(root)
    return [{"skill": name, **policy} for name, policy in sorted(routing.items())]
