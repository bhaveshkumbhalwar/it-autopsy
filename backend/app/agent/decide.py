"""Decide: turn observations into one structured diagnostic decision.

The engine (`engine.analyzer.diagnose`) already produced the category, evidence,
causality and severity. This module does not re-decide any of that. It answers
the questions the engine does not:

* Is there enough evidence to name a root cause at all, or should the agent
  say "insufficient evidence"?
* Which of the controlled remediation actions fits this failure, and why?
* Is a retry appropriate, and is the run recoverable?

Every answer carries its reason. Nothing here is a guess presented as a fact:
the decision names its evidence, and when the evidence is thin it says so.
"""

from __future__ import annotations

from typing import Any

from app.core.models import FailureCategory as F

# Below this the engine's own confidence is not enough to act on: the agent
# reports uncertainty and asks for a better trace instead of inventing a cause.
MIN_ACTIONABLE_CONFIDENCE = 0.3

# Categories where retrying is the wrong move, whatever the status says.
NO_RETRY_CATEGORIES = {
    F.SAFETY.value,
    F.AUTH.value,
    F.PLANNING.value,
    F.HALLUCINATION.value,
    F.REASONING.value,
    F.INCORRECT_OUTPUT.value,
}

RETRY_FRIENDLY_CATEGORIES = {
    F.TIMEOUT.value,
    F.API.value,
    F.TOOL_EXECUTION.value,
    F.DATABASE.value,
    F.INFINITE_LOOP.value,
}


def _why(text: str) -> str:
    return " ".join(text.split())


def assess_evidence(observation: dict[str, Any]) -> dict[str, Any]:
    """Whether the observation supports naming a root cause at all."""
    engine = observation.get("engine") or {}
    signals = observation.get("signals") or {}
    reasons: list[str] = []

    # A quoted evidence row is what makes a category a finding: a category with
    # no quote is the classifier's hint, and a bare step status is not evidence.
    has_quote = any((row.get("quote") or "").strip() for row in engine.get("evidence") or [])
    has_category = bool(engine.get("categories")) and has_quote
    confidence = engine.get("confidence") if isinstance(engine.get("confidence"), (int, float)) else 0.0

    if not has_category:
        reasons.append("No rule or classifier produced a failure category for this run.")
    if not has_quote:
        reasons.append("No step carries error text or a quoted evidence row.")
    if has_category and confidence and confidence < MIN_ACTIONABLE_CONFIDENCE:
        reasons.append(
            f"The engine's confidence ({confidence:.2f}) is below the {MIN_ACTIONABLE_CONFIDENCE:.2f} needed to act on a cause."
        )

    # A category with no quotable evidence is a hint, not a diagnosis. A rule
    # that fired but reported no confidence is still a deterministic finding.
    confident = confidence >= MIN_ACTIONABLE_CONFIDENCE or (has_category and confidence == 0.0 and has_quote)
    sufficient = has_category and has_quote and confident
    return {
        "sufficient": sufficient,
        "confidence": round(confidence, 2),
        "reasons": reasons,
        "needed": None
        if sufficient
        else "A trace whose failing step carries its error message, or a run the rule engine can quote.",
    }


def retry_advice(observation: dict[str, Any], categories: list[str]) -> dict[str, Any]:
    """Whether retrying the same work is appropriate, and why."""
    signals = observation.get("signals") or {}
    status = signals.get("status")

    if not categories:
        return {"appropriate": False, "reason": _why("There is no identified failure to retry.")}
    blocked = [c for c in categories if c in NO_RETRY_CATEGORIES]
    if blocked:
        return {
            "appropriate": False,
            "reason": _why(
                f"Retrying does not fix a {blocked[0].replace('_', ' ')}: the same call will fail the same way."
            ),
        }
    if status in {"success", "recovered"}:
        return {
            "appropriate": False,
            "reason": _why("The run already finished; there is nothing left to retry."),
        }
    friendly = [c for c in categories if c in RETRY_FRIENDLY_CATEGORIES]
    if friendly:
        return {
            "appropriate": True,
            "reason": _why(
                f"{friendly[0].replace('_', ' ')} is usually transient, so a bounded retry is the cheapest first move."
            ),
        }
    return {"appropriate": False, "reason": _why("The failure family is not one a retry is expected to clear.")}


def decide(observation: dict[str, Any]) -> dict[str, Any]:
    """The agent's decision: what failed, how sure, and what to do about it."""
    engine = observation.get("engine") or {}
    categories = [c for c in engine.get("categories") or [] if c]
    evidence = assess_evidence(observation)
    retry = retry_advice(observation, categories)

    failed_step = engine.get("failed_step_index")
    failed_number = failed_step + 1 if isinstance(failed_step, int) else None

    if not evidence["sufficient"]:
        return {
            "verdict": "insufficient_evidence",
            "failure_category": categories[0] if categories else None,
            "root_cause": None,
            "root_cause_step": failed_number,
            "evidence": engine.get("evidence") or [],
            "confidence": evidence["confidence"],
            "confidence_kind": "heuristic",
            "severity": engine.get("severity"),
            "recoverable": engine.get("recoverable"),
            "retry_appropriate": False,
            "retry_reason": retry["reason"],
            "action_id": "collect-better-trace",
            "action_reason": _why(
                "Without a quotable cause, any action would be a guess. Gather a trace with the failing step's error text."
            ),
            "uncertainty": evidence["reasons"],
            "needed_evidence": evidence["needed"],
            "ml": engine.get("ml") or {},
        }

    # Which action fits: the action catalogue owns the mapping, so the decision
    # and the remediation plan can never disagree about what fits a failure.
    from app.agent.remediate import select_action

    action = select_action(categories, observation, retry_appropriate=retry["appropriate"])

    return {
        "verdict": "diagnosed",
        "failure_category": categories[0],
        "root_cause": engine.get("root_cause"),
        "root_cause_step": failed_number,
        "evidence": engine.get("evidence") or [],
        "confidence": evidence["confidence"],
        "confidence_kind": "heuristic",
        "severity": engine.get("severity"),
        "recoverable": engine.get("recoverable"),
        "retry_appropriate": retry["appropriate"],
        "retry_reason": retry["reason"],
        "action_id": action["id"],
        "action_reason": action["why_this"],
        "uncertainty": [],
        "needed_evidence": None,
        "ml": engine.get("ml") or {},
    }
