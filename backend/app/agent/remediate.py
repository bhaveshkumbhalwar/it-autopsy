"""Remediate: the controlled set of actions the agent may take.

Three categories, and the category is a property of the *action*, not of the
caller — there is no code path that can promote a recommendation into an
execution:

``automatic``
    Re-run the diagnosis, re-parse the stored trace, look the tool up in the
    local registry. In-process only, no I/O outside the app's own database.

``approval``
    Something a human must say yes to first (a re-diagnosis with the LLM layer,
    a controlled retry of the agent). Until it is approved the action cannot be
    executed at all.

``recommendation``
    Not executable by design: rotating a credential, changing a database
    connection string, editing the agent's code. The agent produces the
    instructions and the verification plan; it does not touch the system.

Hard rules, enforced in :func:`execute`:

* the action id must be in the catalogue (an allowlist, not a blocklist);
* only ``automatic`` and approved ``approval`` actions can run;
* nothing is read from the trace except as *data* — no trace text is ever
  evaluated, imported or passed to a shell;
* an action that cannot run returns instructions, never an exception that hides
  why.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.core.models import FailureCategory as F

logger = logging.getLogger("agent_autopsy.agent.remediate")

AUTOMATIC = "automatic"
APPROVAL = "approval"
RECOMMENDATION = "recommendation"

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# --------------------------------------------------------------------------- #
# The catalogue. Every entry declares exactly the categories it is valid for;
# an action can never be selected for a failure it does not address.
# --------------------------------------------------------------------------- #

CATALOGUE: dict[str, dict[str, Any]] = {
    "collect-better-trace": {
        "title": "Collect a trace with the failing step's error",
        "categories": [],
        "action_type": RECOMMENDATION,
        "risk": RISK_LOW,
        "why_this": (
            "No quotable cause was found, so the only safe next step is more evidence: "
            "re-export the run with the failing step's error text and tool result included."
        ),
        "instructions": [
            "Re-run the agent with error logging on the failing step (or export the trace from your framework with step errors included).",
            "Paste the new trace here; the diagnosis will be re-run against it.",
        ],
        "verification": "The new trace yields a failure category with at least one quoted evidence row.",
    },
    "reanalyze-rules": {
        "title": "Re-run the diagnosis with the rule engine",
        "categories": [],  # valid for any diagnosed failure
        "action_type": AUTOMATIC,
        "risk": RISK_LOW,
        "why_this": (
            "Re-running the deterministic rules is free and side-effect free, and clears a stale "
            "analysis (an older engine version, or a row analysed before a rule was added)."
        ),
        "instructions": [],
        "verification": "The re-analysis agrees with the current rules and still names the same root-cause step.",
    },
    "reanalyze-with-llm": {
        "title": "Re-run the diagnosis with the LLM explanation layer",
        "categories": [],
        "action_type": APPROVAL,
        "risk": RISK_LOW,
        "why_this": (
            "The LLM layer can add nuance the rules miss, but it costs credits and sends trace text "
            "to a provider, so it needs an explicit yes."
        ),
        "instructions": [],
        "verification": "The analysis still names the same failure category, now with an LLM explanation attached.",
    },
    "check-tool-availability": {
        "title": "Check the tool is registered and reachable",
        "categories": [F.TOOL_EXECUTION.value],
        "action_type": AUTOMATIC,
        "risk": RISK_LOW,
        "why_this": (
            "A tool that failed to execute is either missing from the registry or refusing the call; "
            "a local check separates the two without touching the agent's environment."
        ),
        "instructions": [],
        "verification": "The tool call succeeds in the next trace, or the failure changes to a reachable error.",
    },
    "verify-credentials": {
        "title": "Verify the credential used by the failing step",
        "categories": [F.AUTH.value],
        "action_type": RECOMMENDATION,
        "risk": RISK_HIGH,
        "why_this": (
            "Auth failures are permanent until a human rotates or re-scopes the key. The agent must not "
            "read, print or rotate a credential, so it hands over the steps instead."
        ),
        "instructions": [
            "Identify which credential the failing step used (provider key, tool token, database user).",
            "Check it is present, not expired, and has the required scope for this call.",
            "Rotate or re-scope it in your secret store, then re-run the agent and submit the new trace.",
        ],
        "verification": "A new trace reaches the same step without an authentication error.",
    },
    "check-db-configuration": {
        "title": "Check the database connection configuration",
        "categories": [F.DATABASE.value],
        "action_type": RECOMMENDATION,
        "risk": RISK_HIGH,
        "why_this": (
            "The agent cannot test or change a production connection string, and must not try. "
            "The failing host/port/credentials are the operator's to check."
        ),
        "instructions": [
            "Confirm the host, port and database name in the connection string are reachable from the agent's network.",
            "Confirm the user has permission for the statement that failed, and that the pool is not exhausted.",
            "Add a bounded connect timeout so the failure surfaces in seconds rather than hanging the run.",
        ],
        "verification": "A new trace completes the same database step without a connection error.",
    },
    "bounded-retry": {
        "title": "Recommend a bounded retry with backoff",
        "categories": [F.TIMEOUT.value, F.API.value, F.TOOL_EXECUTION.value, F.DATABASE.value],
        "action_type": RECOMMENDATION,
        "risk": RISK_MEDIUM,
        "why_this": (
            "The failure family is usually transient, so one bounded retry with backoff is the cheapest "
            "first move. The agent does not run your agent, so it hands over the retry policy and asks "
            "for the next trace."
        ),
        "instructions": [
            "Wrap the failing call in a retry with exponential backoff and jitter (3 attempts, 0.5s → 4s).",
            "Cap the total time and fail loudly with the last error rather than silently returning a partial result.",
            "Re-run the agent and submit the new trace for verification.",
        ],
        "verification": "The next trace completes the step that failed, or fails with a different, later error.",
    },
    "rate-limit-backoff": {
        "title": "Recommend backoff that honours the rate limit",
        "categories": [F.API.value],
        "action_type": RECOMMENDATION,
        "risk": RISK_MEDIUM,
        "why_this": (
            "A rate-limit rejection is resolved by waiting the window out, not by retrying immediately; "
            "the trace shows the provider's own guidance."
        ),
        "instructions": [
            "Honour the Retry-After header (or the trace's stated retry delay) before the next attempt.",
            "Add jitter and a per-provider concurrency cap so parallel steps cannot re-trigger the limit.",
        ],
        "verification": "The next trace gets a non-429 response for the same call.",
    },
    "prepare-arguments": {
        "title": "Prepare corrected tool arguments",
        "categories": [F.TOOL_EXECUTION.value, F.INCORRECT_OUTPUT.value],
        "action_type": RECOMMENDATION,
        "risk": RISK_MEDIUM,
        "why_this": (
            "The call failed on what it was asked to do (a missing or invalid argument). Correcting the "
            "arguments is the fix; the agent shows the shape to send rather than sending it."
        ),
        "instructions": [
            "Validate tool arguments against the tool's schema before the call, and name the missing field in the error.",
            "Re-run with the corrected arguments and submit the new trace.",
        ],
        "verification": "The next trace's failing step reaches the tool with valid arguments.",
    },
    "escalate-to-human": {
        "title": "Escalate to a human reviewer",
        "categories": [F.SAFETY.value],
        "action_type": RECOMMENDATION,
        "risk": RISK_LOW,
        "why_this": (
            "A policy block is a decision, not a defect: retrying or re-engineering around it is the wrong "
            "move, and only a person can decide whether the action should be allowed."
        ),
        "instructions": [
            "Review the blocked action against the agent's policy.",
            "Either narrow the agent's instructions, or add the action to the approved allowlist with a justification.",
        ],
        "verification": "The next trace either avoids the blocked action or is approved by policy, with no violation recorded.",
    },
}


def get(action_id: str) -> dict[str, Any] | None:
    """The catalogue entry, carrying its own id so callers never have to guess it."""
    entry = CATALOGUE.get(action_id)
    return None if entry is None else {"id": action_id, **entry}


def all_actions() -> list[dict[str, Any]]:
    return [{"id": key, **value} for key, value in CATALOGUE.items()]


# --------------------------------------------------------------------------- #
# Selection: the one place that maps a failure to an action
# --------------------------------------------------------------------------- #

def select_action(
    categories: list[str],
    observation: dict[str, Any],
    *,
    retry_appropriate: bool,
) -> dict[str, Any]:
    """Pick the action that fits, with the reason it was picked."""
    primary = categories[0] if categories else None

    # Auth and safety come first: their advice must never be a retry.
    if primary == F.AUTH.value:
        return get("verify-credentials")
    if primary == F.SAFETY.value:
        return get("escalate-to-human")
    if primary == F.DATABASE.value:
        return get("check-db-configuration")
    if primary == F.TOOL_EXECUTION.value:
        # A tool that never answered is an availability question; one that
        # answered with an error is an argument question.
        tool_failed = any(call["status"] == "failed" for call in observation["tool_calls"])
        return get("check-tool-availability") if tool_failed else get("prepare-arguments")
    if primary == F.API.value:
        status = (observation.get("engine") or {}).get("evidence") or []
        limited = any("429" in (row.get("quote") or "") or "rate limit" in (row.get("quote") or "").lower() for row in status)
        return get("rate-limit-backoff") if limited else get("bounded-retry")
    if primary in RETRYABLE and retry_appropriate:
        return get("bounded-retry")
    return get("bounded-retry") if retry_appropriate else get("reanalyze-rules")


RETRYABLE = {
    F.TIMEOUT.value,
    F.API.value,
    F.TOOL_EXECUTION.value,
    F.DATABASE.value,
    F.INFINITE_LOOP.value,
}


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #

def build_plan(
    action_id: str,
    decision: dict[str, Any],
    *,
    attempt: int = 1,
    previous_actions: list[str] | None = None,
) -> dict[str, Any]:
    """A remediation plan: what, why, how risky, who must approve, how we check.

    ``previous_actions`` is the attempt history: an action that already failed to
    fix the problem is never proposed a second time in the same session.
    """
    action = get(action_id) or get("escalate-to-human")
    previous_actions = previous_actions or []
    repeat = action_id in previous_actions

    plan = {
        "action_id": action_id,
        "title": action["title"],
        "action_type": action["action_type"],
        "risk": action["risk"],
        "problem": (decision.get("root_cause") or "").split(" Evidence:")[0].strip() or None,
        "root_cause": decision.get("root_cause"),
        "root_cause_step": decision.get("root_cause_step"),
        "proposed_solution": action["title"],
        "why_selected": action["why_this"],
        "expected_outcome": action["verification"],
        "verification_criteria": action["verification"],
        "requires_approval": action["action_type"] == APPROVAL,
        "instructions": list(action["instructions"]),
        "attempt": attempt,
        "repeat_of_failed_action": repeat,
        "executable": action["action_type"] in {AUTOMATIC, APPROVAL} and not repeat,
    }
    if repeat:
        plan["why_selected"] = (
            f"{action['title']} was already tried in this session and did not resolve the problem, "
            "so it is not proposed again. Escalating instead."
        )
        plan["action_type"] = RECOMMENDATION
        plan["requires_approval"] = False
        plan["executable"] = False
    return plan


# --------------------------------------------------------------------------- #
# Execution — the only place an action can run
# --------------------------------------------------------------------------- #

def execute(
    plan: dict[str, Any],
    *,
    approved: bool,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Run a safe action, or explain why it cannot run.

    ``context`` carries what an automatic action needs and nothing more:
    ``db``, ``execution``, ``analysis``, ``reanalyze`` (a callable). The action
    never receives the trace text as anything but data.
    """
    action_id = plan.get("action_id")
    action = get(action_id)
    if action is None:
        return _refused("unknown-action", f"'{action_id}' is not in the action allowlist, so it was not run.")
    if action["action_type"] == RECOMMENDATION:
        return _refused(
            "recommendation-only",
            "This action is recommendation-only: the agent cannot execute it, by design.",
        )
    if action["action_type"] == APPROVAL and not approved:
        return _refused("approval-required", "This action requires human approval before it can run.")
    if plan.get("repeat_of_failed_action"):
        return _refused("already-tried", "This action already failed to resolve the problem in this session.")

    handler: Callable[..., dict[str, Any]] | None = HANDLERS.get(action_id)
    if handler is None:
        return _refused("no-handler", "This action has no executor; it can only be recommended.")
    try:
        return handler(context)
    except Exception as exc:  # pragma: no cover - an action must never crash the API
        logger.warning("Action %s failed: %s", action_id, type(exc).__name__)
        return {
            "status": "failed",
            "detail": f"The action could not complete ({type(exc).__name__}).",
            "output": {},
        }


def _refused(code: str, detail: str) -> dict[str, Any]:
    return {"status": "refused", "code": code, "detail": detail, "output": {}}


def _act_reanalyze_rules(context: dict[str, Any]) -> dict[str, Any]:
    reanalyze = context.get("reanalyze")
    if reanalyze is None:
        return {"status": "failed", "detail": "No re-analysis hook was provided.", "output": {}}
    result = reanalyze(False)
    return {
        "status": "executed",
        "detail": "The diagnosis was re-run with the rule engine.",
        "output": {
            "category": result.get("primary_category"),
            "status": result.get("status"),
            "confidence": result.get("confidence"),
        },
    }


def _act_reanalyze_with_llm(context: dict[str, Any]) -> dict[str, Any]:
    reanalyze = context.get("reanalyze")
    if reanalyze is None:
        return {"status": "failed", "detail": "No re-analysis hook was provided.", "output": {}}
    result = reanalyze(True)
    return {
        "status": "executed",
        "detail": "The diagnosis was re-run with the LLM explanation layer.",
        "output": {
            "category": result.get("primary_category"),
            "llm_used": result.get("llm_used"),
            "llm_error": result.get("llm_error"),
        },
    }


def _act_check_tool_availability(context: dict[str, Any]) -> dict[str, Any]:
    """Is the failing tool known to this process?

    A deliberately local check: the tool registry here is the set of tools the
    app itself knows how to describe. A tool that is absent is not necessarily
    broken — it belongs to the agent under test — so the result says which of
    the two cases applies instead of pretending to reach the agent's runtime.
    """
    from app.engine.snippets import SAFE_IDENTIFIER

    tool = context.get("tool_name")
    if not tool:
        return {"status": "failed", "detail": "The trace names no tool for the failing step.", "output": {}}
    safe = bool(SAFE_IDENTIFIER.fullmatch(str(tool)))
    return {
        "status": "executed",
        "detail": (
            f"'{tool}' is a well-formed tool name, but it belongs to the agent under test, "
            "so reachability has to be checked in that environment."
        ),
        "output": {"tool_name": tool, "well_formed": safe, "reachable": "unknown", "scope": "local-registry"},
    }


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "reanalyze-rules": _act_reanalyze_rules,
    "reanalyze-with-llm": _act_reanalyze_with_llm,
    "check-tool-availability": _act_check_tool_availability,
}
