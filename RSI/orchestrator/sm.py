"""The campaign state machine.

Orchestration lives in code. Skills never call each other: this module decides which skill runs
next, how many retries are allowed and when to stop, so every step can be costed, replayed and
replaced without touching a prompt.

The state machine is written from the single-experiment workflow (W2) and the campaign
workflow (W3). Its two loops are the ones that make the system honest:

* A correctness failure or a ``not_implemented`` verdict goes back to the implementer, at most
  twice. If it still fails, the experiment is recorded as an implementation failure that counts
  toward no rule — it never becomes evidence.
* The timing result is sealed until the verdict exists. The verifier never sees it, so
  "it got faster" cannot bias "it was implemented".

A third loop is not a retry loop but it is what keeps the campaign honest about its own history:
before a direction is written off, ``revisit`` checks whether it was ever really tried. A stop
whose closures all failed to reach machine code is not a stop, and ``apply_plan`` refuses to
report one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from ._tooling import closures, knowledge, ledger, paths

#: W2's two retry ceilings. Raising them is a scaffold change, not a tuning knob.
IMPLEMENT_RETRIES = 2
CORRECTNESS_RETRIES = 2
#: Campaign stop conditions.
TARGET_ATTAINMENT_PCT = 85.0
NO_GAIN_ROUNDS = 5
REPROFILE_AFTER_STALLED_ROUNDS = 3
#: Closures that force a revisit before the campaign may call a direction exhausted.
REVISIT_AFTER_OPEN_CLOSURES = closures.MIN_OPEN_CLOSURES


class CampaignError(RuntimeError):
    """Raised when the state machine is asked to do something outside the workflow."""


# -- states ---------------------------------------------------------------

STATES = (
    "diagnose",          # collect-profile + diagnose-bottleneck
    "retrieve",          # retrieve-knowledge
    "hypothesize",       # propose-hypothesis
    "implement",         # implement-change
    "evaluate",          # run-evaluator
    "inspect",           # inspect-binary + fresh profile, feeding the verifier
    "verify",            # verify-mechanism
    "record",            # record-experiment
    "promote",           # the evaluator promotes a qualifying candidate into best/
    "gap",               # queue a microbenchmark design when prediction error is large
    "revisit",           # revisit-closures: were the written-off directions ever really tried?
    "plan",              # plan-campaign
    "report",            # report-campaign
    "done",
    "blocked",
)

#: Which skill runs in each state, and on which tier (routing supplies the tier).
STATE_SKILL = {
    "diagnose": "diagnose-bottleneck",
    "retrieve": "retrieve-knowledge",
    "hypothesize": "propose-hypothesis",
    "implement": "implement-change",
    "evaluate": "run-evaluator",
    "inspect": "inspect-binary",
    "verify": "verify-mechanism",
    "record": "record-experiment",
    "promote": "record-experiment",
    "gap": "design-microbench",
    "revisit": "revisit-closures",
    "plan": "plan-campaign",
    "report": "report-campaign",
}


@dataclass
class Campaign:
    """One campaign: an operator, a budget, and everything the state machine needs."""

    op: str
    hw_id: str = ""
    budget_experiments: int = 20
    target_attainment_pct: float = TARGET_ATTAINMENT_PCT
    state: str = "diagnose"
    round: int = 0
    experiments: int = 0
    implement_retries: int = 0
    correctness_retries: int = 0
    stalled_rounds: int = 0
    best_attainment_pct: Optional[float] = None
    current_attainment_pct: Optional[float] = None
    signature_tags: List[str] = field(default_factory=list)
    hypothesis_id: Optional[str] = None
    verdict: Optional[str] = None
    last_speedup: Optional[float] = None
    significant: Optional[bool] = None
    prediction_error: Optional[float] = None
    queued_tasks: List[Dict[str, Any]] = field(default_factory=list)
    closures: List[Dict[str, Any]] = field(default_factory=list)
    revisit_due: bool = False
    revisit_checked: bool = False
    reopen_direction: Optional[str] = None
    reopen_retry: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Campaign":
        known = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        return cls(**known)

    @classmethod
    def load(cls, path: Any) -> "Campaign":
        from pathlib import Path

        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: Any) -> None:
        from pathlib import Path

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


# -- transitions ----------------------------------------------------------

def next_step(campaign: Campaign) -> Dict[str, Any]:
    """The single decision point: which state comes next, which skill runs, on which tier."""
    from . import routing

    if campaign.state in {"done", "blocked"}:
        return {
            "state": campaign.state,
            "skill": None,
            "tier": None,
            "reason": campaign.note or "campaign finished",
        }
    if campaign.state == "implement" and campaign.implement_retries > IMPLEMENT_RETRIES:
        return _block(campaign, "implement_change_exhausted",
                      f"{campaign.implement_retries} implementation attempts failed: "
                      "recorded as an implementation failure that counts toward no rule")
    if campaign.state == "evaluate" and campaign.correctness_retries > CORRECTNESS_RETRIES:
        return _block(campaign, "correctness_exhausted",
                      "the candidate keeps failing correctness: no timing, no evidence")
    skill = STATE_SKILL.get(campaign.state)
    return {
        "state": campaign.state,
        "skill": skill,
        "tier": routing.tier_of(skill) if skill else None,
        "retries": routing.retries_for(skill) if skill else 0,
        "isolated": routing.is_isolated(skill) if skill else False,
        "reason": _state_reason(campaign),
    }


def _state_reason(campaign: Campaign) -> str:
    reasons = {
        "diagnose": "re-establish the signature before spending an experiment",
        "retrieve": "inject only the knowledge that matches this signature",
        "hypothesize": "one falsifiable card, with counters and a predicted speedup",
        "implement": "edit for exactly one card",
        "evaluate": "correctness first, and the timing result stays sealed",
        "inspect": "the verifier needs the binary summary and a fresh profile, not the timing",
        "verify": "did the mechanism on the card really land in this change",
        "record": "unseal the timing and append one experiment",
        "promote": "the evaluator promotes a confirmed and significant candidate",
        "gap": "prediction error is large: the hardware model has a hole here",
        "revisit": "check the closures before a direction is written off",
        "plan": "continue, change direction, combine confirmed changes, or stop",
        "report": "write the campaign report",
    }
    return reasons.get(campaign.state, campaign.state)


def _block(campaign: Campaign, code: str, reason: str) -> Dict[str, Any]:
    campaign.state = "blocked"
    campaign.note = reason
    campaign.history.append({"state": "blocked", "code": code, "reason": reason})
    return {"state": "blocked", "skill": None, "tier": None, "code": code, "reason": reason}


def advance(campaign: Campaign, outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Consume one step's outcome and move to the next state.

    This is the whole workflow in one function, which is what makes it replayable: the same
    function runs for an old scaffold and a new one on the same experiment budget.
    """
    state = campaign.state
    outcome = outcome or {}
    if state == "blocked":
        return next_step(campaign)
    campaign.history.append({"state": state, "outcome": _trim(outcome)})

    if state == "diagnose":
        signature = outcome.get("signature") or {}
        campaign.signature_tags = list(signature.get("tags") or [])
        roofline = signature.get("roofline") or {}
        if roofline.get("attained_pct") is not None:
            campaign.best_attainment_pct = _better(campaign.best_attainment_pct, roofline["attained_pct"])
            campaign.current_attainment_pct = roofline["attained_pct"]
        campaign.state = "retrieve"
        return next_step(campaign)

    if state == "retrieve":
        campaign.state = "hypothesize"
        return next_step(campaign)

    if state == "hypothesize":
        cards = outcome.get("cards") or []
        if not cards:
            return _block(campaign, "no_hypothesis",
                          "no falsifiable card was produced: nothing to spend an experiment on")
        campaign.hypothesis_id = (cards[0] or {}).get("id")
        campaign.state = "implement"
        campaign.implement_retries = 0
        campaign.correctness_retries = 0
        return next_step(campaign)

    if state == "implement":
        if not outcome.get("diff"):
            campaign.implement_retries += 1
            campaign.history.append({"state": "implement", "retry": campaign.implement_retries})
            return next_step(campaign)
        campaign.state = "evaluate"
        return next_step(campaign)

    if state == "evaluate":
        correctness = outcome.get("correctness") or {}
        if not correctness.get("passed", False):
            campaign.correctness_retries += 1
            campaign.state = "implement"
            campaign.note = "correctness failed: back to the implementer"
            return next_step(campaign)
        campaign.state = "inspect"
        return next_step(campaign)

    if state == "inspect":
        campaign.state = "verify"
        return next_step(campaign)

    if state == "verify":
        verdict = outcome.get("verdict")
        campaign.verdict = verdict
        if verdict == "not_implemented":
            campaign.implement_retries += 1
            campaign.state = "implement"
            campaign.note = "verdict was not_implemented: missing evidence, retry the implementation"
            return next_step(campaign)
        campaign.state = "record"
        return next_step(campaign)

    if state == "record":
        campaign.revisit_checked = False
        # One experiment was spent the moment a verdict existed, whether or not it qualified.
        campaign.experiments += 1
        if isinstance(outcome.get("closure"), dict):
            campaign.closures.append(dict(outcome["closure"]))
        campaign.last_speedup = outcome.get("speedup")
        campaign.significant = outcome.get("significant")
        campaign.prediction_error = outcome.get("prediction_error")
        # A round that produced no qualifying candidate is exactly the round the stall counters
        # exist for. Counting only the promote path meant a campaign of failed experiments never
        # advanced its stall count, so neither the re-profile nor the five-round stop ever fired —
        # the loop would have spent its whole budget walking in the same direction.
        if not outcome.get("qualifies"):
            campaign.stalled_rounds += 1
            campaign.history.append({"state": "record",
                                     "note": "no qualifying candidate: stalled round incremented"})
            campaign.state = "gap"
            return next_step(campaign)
        campaign.state = "promote"
        return next_step(campaign)

    if state == "promote":
        attainment = outcome.get("attained_pct")
        if attainment is not None:
            campaign.current_attainment_pct = attainment
        if attainment is not None and attainment > (campaign.best_attainment_pct or 0.0):
            campaign.best_attainment_pct = attainment
            campaign.stalled_rounds = 0
        else:
            campaign.stalled_rounds += 1
        campaign.state = "gap"
        return next_step(campaign)

    if state == "gap":
        if campaign.prediction_error is not None and campaign.prediction_error > knowledge.PREDICTION_ERROR_HOLE:
            campaign.queued_tasks.append({
                "task": "design-microbench",
                "reason": f"prediction error {campaign.prediction_error:.2f} exceeds the hole threshold",
                "op": campaign.op,
            })
        campaign.round += 1
        campaign.state = "revisit"
        return next_step(campaign)

    if state == "revisit":
        return record_revisit(campaign, outcome)

    if state == "plan":
        decision = outcome.get("decision", "continue")
        return apply_plan(campaign, decision, outcome)

    if state == "report":
        campaign.state = "done"
        return next_step(campaign)

    raise CampaignError(f"no transition from state {state!r}")


def unresolved_directions(campaign: Campaign) -> List[str]:
    """The directions on record that may not be called exhausted yet.

    The campaign stores a closure record after every failed experiment and the summarised rows
    after a revisit plan, so both shapes are read here: a record carries `evidence` and
    `points_tried`, a row carries the `retirable` verdict the plan already computed.
    """
    found: List[str] = []
    for item in campaign.closures:
        if not isinstance(item, dict):
            continue
        direction = str(item.get("direction") or "")
        retirable = item.get("retirable")
        if retirable is None:
            retirable = closures.is_retirable(item)
        if not retirable and direction and direction not in found:
            found.append(direction)
    return found


def record_revisit(campaign: Campaign, outcome: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Consume ``revisit-closures``' plan and decide whether the campaign owes a reopen.

    A closure that never reached machine code, a confirmed mechanism whose score did not follow,
    and a premise refuted at one point of its parameter are all *open*: they are not evidence that
    the direction does not pay. When any of them is open, the next card is that reopen rather than
    a new direction or a stop.
    """
    outcome = outcome or {}
    plan = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else outcome
    decision = str(plan.get("decision") or "not_triggered")
    if isinstance(plan.get("closures"), list):
        # A plan that carried no closure list must not erase the ones the record step collected.
        campaign.closures = list(plan["closures"])
    campaign.revisit_due = decision in {"reopen", "hold"}
    reopen = (plan.get("reopens") or [None])[0] if decision == "reopen" else None
    campaign.reopen_direction = str(reopen.get("direction")) if isinstance(reopen, dict) else None
    campaign.reopen_retry = str(reopen.get("retry")) if isinstance(reopen, dict) else None
    campaign.history.append({"state": "revisit", "decision": decision,
                             "reopen": campaign.reopen_direction,
                             "open": unresolved_directions(campaign)})
    if decision == "reopen" and campaign.reopen_direction:
        campaign.state = "hypothesize"
        campaign.note = (f"reopening {campaign.reopen_direction} ({campaign.reopen_retry}): the "
                         "closure it rests on is not evidence that the direction does not pay")
        return next_step(campaign)
    campaign.state = "plan"
    if decision in {"hold", "exhausted"}:
        campaign.note = str(plan.get("note") or campaign.note)
    return next_step(campaign)


def apply_plan(campaign: Campaign, decision: str, outcome: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Act on ``plan-campaign``'s decision, including every stop condition.

    A stop that rests on refuted directions also rests on their closures, so the closures are
    re-checked once before one of them is reported. ``stop_target`` and the budget ceiling are
    exempt: neither claims that the playbook is exhausted.
    """
    outcome = outcome or {}
    if decision in {"stop", "stop_plateau", "stop_budget"} and not campaign.revisit_checked:
        # One check per stop attempt chain: the revisit either produces a reopen (and the campaign
        # continues) or answers the question once, so a capped closure cannot loop here forever.
        campaign.revisit_checked = True
        campaign.state = "revisit"
        campaign.note = (f"stop proposed: {len(unresolved_directions(campaign))} direction(s) on "
                         "record are not settled; check whether they were ever really tried before "
                         "reporting the playbook exhausted")
        return next_step(campaign)
    if decision in {"stop_target", "stop_plateau", "stop_budget", "stop"}:
        campaign.state = "report"
        campaign.note = outcome.get("reason") or campaign.note or f"stopped: {decision}"
        return next_step(campaign)
    if campaign.experiments >= campaign.budget_experiments:
        campaign.state = "report"
        campaign.note = f"stopped: budget of {campaign.budget_experiments} experiments spent"
        return next_step(campaign)
    if campaign.current_attainment_pct is not None and \
            campaign.current_attainment_pct >= campaign.target_attainment_pct:
        campaign.state = "report"
        campaign.note = (f"stopped: attainment {campaign.current_attainment_pct:.1f}% reached the "
                         f"target of {campaign.target_attainment_pct:.1f}%")
        return next_step(campaign)
    if campaign.stalled_rounds >= NO_GAIN_ROUNDS:
        campaign.state = "report"
        campaign.note = (f"stopped: {campaign.stalled_rounds} rounds without a significant gain and "
                         "no remaining hypothesis predicts one above the noise floor")
        return next_step(campaign)
    if campaign.stalled_rounds >= REPROFILE_AFTER_STALLED_ROUNDS:
        # The bottleneck may have moved: re-profile and re-diagnose before spending more.
        campaign.state = "diagnose"
        return next_step(campaign)
    campaign.state = "diagnose" if decision == "reprofile" else "hypothesize"
    return next_step(campaign)


def stop_conditions(campaign: Campaign) -> Dict[str, Any]:
    """Every stop condition, evaluated together, so a plan decision is auditable."""
    return {
        "unresolved_closures": unresolved_directions(campaign),
        "target_reached": (
            campaign.current_attainment_pct is not None
            and campaign.current_attainment_pct >= campaign.target_attainment_pct
        ),
        "budget_spent": campaign.experiments >= campaign.budget_experiments,
        "no_gain_rounds": campaign.stalled_rounds,
        "no_gain_exhausted": campaign.stalled_rounds >= NO_GAIN_ROUNDS,
        "reprofile_due": campaign.stalled_rounds >= REPROFILE_AFTER_STALLED_ROUNDS,
        "attainment": campaign.current_attainment_pct,
        "budget": campaign.budget_experiments,
        "experiments": campaign.experiments,
    }


def new_campaign(op: str, budget: int = 20, hw_id: str = "",
                 target_attainment_pct: float = TARGET_ATTAINMENT_PCT) -> Campaign:
    campaign = Campaign(op=op, hw_id=hw_id, budget_experiments=int(budget),
                        target_attainment_pct=float(target_attainment_pct))
    campaign.state = "diagnose"
    return campaign


def _better(current: Optional[float], candidate: float) -> float:
    return float(candidate) if current is None else max(float(current), float(candidate))


def _trim(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the history small: signatures and verdicts, not whole profiles."""
    keys = ("signature", "verdict", "decision", "diff", "correctness", "speedup", "significant",
            "cards", "prediction_error", "qualifies", "attained_pct", "reason", "closure",
            "plan")
    return {key: outcome[key] for key in keys if key in outcome}
