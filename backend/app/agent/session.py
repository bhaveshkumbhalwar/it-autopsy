"""The agent loop: Observe -> Analyze -> Decide -> Act -> Verify -> Learn.

One *session* is the whole loop for one execution. It is persisted as a list of
:class:`~app.db.database.Remediation` rows (one per attempt) plus an
:class:`~app.db.database.AgentEvent` per step, so the process is inspectable
after the fact instead of living in a response body.

What makes this a loop rather than a pipeline:

* verification feeds back — ``STILL_FAILING``, ``PARTIALLY_RESOLVED`` and
  ``NEW_FAILURE`` start the next attempt with the new trace as the observation,
  and the failed action is remembered so it is not proposed again;
* the loop stops for a stated reason: verified, out of attempts, waiting for
  approval, or nothing left that the agent is allowed to do;
* what was learned is written back onto the execution and shown as a lesson.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import decide as decide_mod
from app.agent import observe as observe_mod
from app.agent import remediate as remediate_mod
from app.agent import verify as verify_mod
from app.api.deps import redact_agent_row
from app.core.config import settings
from app.core.models import Status
from app.core.redact import redact_obj
from app.db.database import AgentEvent, Analysis, Execution, Remediation
from app.engine.analyzer import diagnose
from app.ingest import ingest_text
from app.ingest.util import ParseError

logger = logging.getLogger("agent_autopsy.agent.session")

# Terminal states for one remediation turn.
PROPOSED = "proposed"
AWAITING_APPROVAL = "awaiting_approval"
REJECTED = "rejected"
EXECUTED = "executed"
EXECUTION_FAILED = "execution_failed"
ACTION_REFUSED = "action_refused"
VERIFIED = "verified"
NOT_VERIFIED = "not_verified"

# Verification statuses that mean "the problem is not gone, keep going".
CONTINUE_STATUSES = {
    verify_mod.STILL_FAILING,
    verify_mod.PARTIALLY_RESOLVED,
    verify_mod.NEW_FAILURE,
}

# The status the execution ends up in once verification resolves it.
RESOLVED_STATUS = Status.RECOVERED.value


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def max_attempts() -> int:
    return max(1, int(settings.agent_max_attempts))


def _key(value: Any) -> int:
    """The execution's row id, whether an id or the row itself was passed."""
    if isinstance(value, dict):
        value = value.get("id")
    return int(getattr(value, "id", value))


def _row_id(row: Any) -> int:
    """The remediation's primary key, read defensively (a detached row can drop it)."""
    value = getattr(row, "id", None)
    if value is None:
        value = getattr(getattr(row, "dict", None), "get", lambda _k: None)("id")
    if value is None:
        raise ValueError("The remediation row has no id.")
    return int(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    db: Session,
    execution_pk: Any,
    phase: str,
    kind: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    remediation_id: int | None = None,
) -> AgentEvent:
    event = AgentEvent(
        execution_pk=_key(execution_pk),
        remediation_id=remediation_id,
        phase=phase,
        kind=kind,
        summary=" ".join(summary.split())[:600],
        detail=redact_obj(detail or {}, settings.llm_api_key),
    )
    db.add(event)
    return event


def _latest_analysis(db: Session, execution_pk: int) -> Analysis | None:
    return db.scalars(
        select(Analysis).where(Analysis.execution_pk == execution_pk).order_by(Analysis.id.desc()).limit(1)
    ).first()


def _trace_of(execution: Execution):
    """Re-parse a stored execution into a NormalizedTrace (it is stored verbatim)."""
    trace, _fmt, _red = ingest_text(
        json.dumps(execution.raw_trace or {}),
        agent_name=execution.agent_name,
        framework=execution.framework,
    )
    return trace


def _observe_execution(execution: Execution, analysis: Analysis | None) -> dict[str, Any]:
    payload = None
    if analysis is not None:
        payload = {
            "status": analysis.status,
            "primary_category": analysis.primary_category,
            "categories": analysis.categories or [],
            "root_cause": analysis.root_cause,
            "failed_step_index": analysis.failed_step_index,
            "confidence": analysis.confidence,
            "severity": analysis.severity,
            "recoverable": analysis.recoverable,
            "evidence": analysis.evidence or [],
            "ml": {
                "prediction": analysis.ml_prediction,
                "confidence": analysis.ml_confidence,
                "hint_only": True,
                "agrees": None,
            },
        }
    return observe_mod.observe_dict(execution.raw_trace or {}, payload)


def session_attempts(db: Session, execution_pk: Any) -> list[Remediation]:
    return list(
        db.scalars(
            select(Remediation)
            .where(Remediation.execution_pk == _key(execution_pk))
            .order_by(Remediation.attempt)
        ).all()
    )


def attempts_used(db: Session, execution_pk: Any, until: Any = None) -> int:
    # ``.scalars()`` yields rows, so take the first of one column rather than
    # the row itself (which ``.scalar()`` would hand back).
    statement = select(func.count(Remediation.id)).where(Remediation.execution_pk == _key(execution_pk))
    if until is not None:
        # The loop counts the attempts made *before* this turn: its own row is
        # inserted before verification runs, and must not be counted twice.
        statement = statement.where(Remediation.id < int(until))
    return int(db.scalars(statement).first() or 0)


def events_for(db: Session, execution_pk: Any) -> list[AgentEvent]:
    return list(
        db.scalars(
            select(AgentEvent).where(AgentEvent.execution_pk == _key(execution_pk)).order_by(AgentEvent.id)
        ).all()
    )


# A plan the operator can still act on. ``not_verified`` is *not* one: that
# attempt is finished, and the loop's own next step replaces it.
OPEN_STATUSES = {PROPOSED, AWAITING_APPROVAL, EXECUTED, ACTION_REFUSED}

# Declared after the statuses above so the helper reads naturally.
def _has_open_attempt(db: Session, execution_pk: Any) -> bool:
    """Is there a plan the operator can still act on?"""
    return bool(
        db.scalars(
            select(Remediation.id).where(
                Remediation.execution_pk == _key(execution_pk),
                Remediation.status.in_(tuple(OPEN_STATUSES)),
            )
        ).first()
    )


def tried_actions(db: Session, execution_pk: Any) -> list[str]:
    """Actions that were executed (or executed and failed to verify)."""
    return [
        row.action_id
        for row in session_attempts(db, execution_pk)
        if row.status in {EXECUTED, EXECUTION_FAILED, VERIFIED, NOT_VERIFIED, ACTION_REFUSED}
    ]


def _reanalyze(db: Session, execution: Execution, use_llm: bool) -> dict[str, Any]:
    """Re-run the diagnosis for an execution, storing the new analysis."""
    execution_pk = execution.id
    trace = _trace_of(execution)
    result = diagnose(trace, use_llm=use_llm)
    analysis = Analysis(
        execution_pk=execution.id,
        engine_version=result["engine_version"],
        status=result["status"],
        severity=result["severity"],
        categories=result["categories"],
        primary_category=result["primary_category"],
        failed_step_index=result["failed_step_index"],
        root_cause=result["root_cause"],
        what_happened=result["what_happened"],
        impact=result["impact"],
        recoverable=result["recoverable"],
        confidence=result["confidence"],
        evidence=result["evidence"],
        recommendations=result["recommendations"],
        prevention=result["prevention"],
        timeline=result["timeline"],
        llm_used=result["llm_used"],
        llm_model=result["llm_model"],
        llm_error=result["llm_error"],
        detection_notes=result["detection_notes"],
        primary_title=result.get("primary_title"),
        fixes=result.get("fixes", []),
        ml_prediction=result["ml"]["prediction"],
        ml_confidence=result["ml"]["confidence"],
        ml_top=result["ml"]["top"],
        llm_prompt=result.get("llm_prompt"),
        llm_raw_response=result.get("llm_raw_response"),
        confidence_factors=result.get("confidence_factors", []),
        causal_chain=result.get("causal_chain", []),
        severity_detail=result.get("severity_detail", {}),
    )
    from app.api.deps import redact_analysis_on_write

    redact_analysis_on_write(analysis)
    db.add(analysis)
    db.flush()
    execution.analysis_count = int(
        db.scalars(select(func.count(Analysis.id)).where(Analysis.execution_pk == _key(execution_pk))).first() or 0
    )
    return {"id": analysis.id, **{k: result.get(k) for k in ("primary_category", "status", "confidence", "llm_used", "llm_error")}}


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #

def start(
    db: Session,
    execution_pk: int,
    *,
    analysis: Analysis | None = None,
    attempt_limit: int | None = None,
) -> dict[str, Any]:
    """Start a session: observe, analyse, decide, and propose the first plan."""
    execution = db.get(Execution, execution_pk)
    if execution is None:
        raise ValueError("Execution not found.")
    limit = attempt_limit or max_attempts()
    used = attempts_used(db, _key(execution_pk))
    if used >= limit:
        return {
            "started": False,
            "reason": f"This session has used its {limit} attempt(s); no further remediation will be proposed.",
            "attempts_used": used,
            "attempt_limit": limit,
        }

    latest = analysis or _latest_analysis(db, execution.id)
    if latest is None:
        # A session needs a diagnosis to act on; produce one now.
        result = _reanalyze(db, execution, use_llm=False)
        latest = db.get(Analysis, result["id"])
        _event(db, execution.id, "analyze", "diagnosis-created", "No analysis existed; the rule engine was run.")

    observation = _observe_execution(execution, latest)
    _event(
        db,
        execution.id,
        "observe",
        "trace-observed",
        f"Observed {observation['execution']['step_count']} step(s), "
        f"{len(observation['failed_steps'])} failed, status {observation['signals']['status']}.",
        {"signals": observation["signals"]},
    )

    decision = decide_mod.decide(observation)
    _event(
        db,
        execution.id,
        "analyze",
        "decision-made",
        f"Decision: {decision['verdict']}"
        + (f" - {decision['failure_category']}" if decision["failure_category"] else "")
        + f" (confidence {decision['confidence']:.2f}).",
        {"verdict": decision["verdict"], "uncertainty": decision["uncertainty"]},
    )

    previous = tried_actions(db, execution.id)
    plan = remediate_mod.build_plan(
        decision["action_id"], decision, attempt=used + 1, previous_actions=previous
    )
    _event(
        db,
        execution.id,
        "decide",
        "plan-proposed",
        f"Proposed '{plan['title']}' ({plan['action_type']}, risk {plan['risk']}, attempt {plan['attempt']}).",
        {"action_id": plan["action_id"], "why": plan["why_selected"]},
    )

    remediation = Remediation(
        execution_pk=execution.id,
        analysis_id=latest.id,
        attempt=used + 1,
        decision=decision,
        action_id=plan["action_id"],
        action_type=plan["action_type"],
        risk=plan["risk"],
        status=AWAITING_APPROVAL if plan["requires_approval"] else PROPOSED,
        plan=plan,
        requires_approval=plan["requires_approval"],
    )
    redact_agent_row(remediation)
    db.add(remediation)
    db.commit()
    db.refresh(remediation)
    return {
        "started": True,
        "attempts_used": used + 1,
        "attempt_limit": limit,
        "remediation": remediation,
    }


def approve(
    db: Session,
    remediation: Remediation,
    *,
    approved: bool,
    by: str | None = None,
    note: str | None = None,
) -> Remediation:
    """Record a human's decision about a plan. Refuses anything not waiting."""
    # A refused attempt (an approval action tried before it was approved) leaves
    # the plan open: the operator can approve it and then run it.
    if remediation.status not in {PROPOSED, AWAITING_APPROVAL, ACTION_REFUSED}:
        raise ValueError(f"This plan is '{remediation.status}' and can no longer be approved or rejected.")
    remediation.approved = bool(approved)
    remediation.approved_by = (by or "operator")[:120]
    remediation.approval_note = note
    remediation.approved_at = _now()
    remediation.status = PROPOSED if approved else REJECTED
    _event(
        db,
        remediation.execution_pk,
        "decide",
        "plan-approved" if approved else "plan-rejected",
        f"{'Approved' if approved else 'Rejected'} '{remediation.plan.get('title')}' by {remediation.approved_by}.",
        {"note": note},
        remediation_id=remediation.id,
    )
    if not approved:
        _event(
            db,
            remediation.execution_pk,
            "learn",
            "session-stopped",
            "The operator rejected the proposed action, so the session stopped here.",
            remediation_id=remediation.id,
        )
    db.commit()
    db.refresh(remediation)
    return remediation


def execute(db: Session, remediation: Remediation) -> Remediation:
    """Run the plan if it is allowed to run, and record exactly what happened."""
    execution = db.get(Execution, remediation.execution_pk)
    if execution is None:
        raise ValueError("The execution this plan belongs to no longer exists.")
    if remediation.status not in {PROPOSED, AWAITING_APPROVAL, ACTION_REFUSED}:
        raise ValueError(f"This plan is '{remediation.status}' and cannot be executed again.")

    plan = remediation.plan or {}
    context = {
        "db": db,
        "execution": execution,
        "tool_name": (plan.get("tool_name") or _failing_tool(execution)),
        "reanalyze": lambda use_llm: _reanalyze(db, execution, use_llm),
    }
    result = remediate_mod.execute(plan, approved=bool(remediation.approved), context=context)

    remediation.execution_result = result
    remediation.executed_at = _now()
    if result["status"] == "refused":
        remediation.status = ACTION_REFUSED
    elif result["status"] == "failed":
        remediation.status = EXECUTION_FAILED
    else:
        remediation.status = EXECUTED
    redact_agent_row(remediation)
    _event(
        db,
        remediation.execution_pk,
        "act",
        f"action-{result['status']}",
        f"Action '{plan.get('title')}': {result.get('detail')}",
        {"action_id": remediation.action_id, "code": result.get("code"), "output": result.get("output")},
        remediation_id=remediation.id,
    )
    db.commit()
    db.refresh(remediation)
    return remediation


def _failing_tool(execution: Execution) -> str | None:
    for raw in (execution.raw_trace or {}).get("steps") or []:
        if str(raw.get("status")) == "failed" and raw.get("tool_name"):
            return raw.get("tool_name")
    return None


def submit_verification(
    db: Session,
    remediation: Remediation,
    trace_text: str,
    *,
    agent_name: str | None = None,
    framework: str | None = None,
) -> dict[str, Any]:
    """Verify a remediation against a follow-up trace, and continue the loop.

    The follow-up is ingested as its own execution (it is a real run, and the
    history should show it), then compared with the original.
    """
    original = db.get(Execution, remediation.execution_pk)
    if original is None:
        raise ValueError("The execution this plan belongs to no longer exists.")
    if remediation.status in {REJECTED}:
        raise ValueError("This plan was rejected, so there is nothing to verify.")
    if remediation.status == AWAITING_APPROVAL:
        raise ValueError("This plan still needs approval before it can be verified.")

    trace, fmt, redactions = ingest_text(
        trace_text,
        agent_name=agent_name or original.agent_name,
        framework=framework or original.framework,
    )
    # The follow-up gets its own diagnosis, so the comparison is between two
    # analysed runs rather than between a run and a raw trace.
    result = diagnose(trace, use_llm=False)
    followup = Execution(
        execution_id=trace.execution_id,
        agent_name=trace.agent_name,
        framework=trace.framework,
        source_format=fmt,
        status=trace.status.value,
        duration_ms=trace.duration_ms,
        started_at=trace.started_at,
        task_description=(trace.task_description or "")[:8000] or None,
        output=str(trace.output)[:20000] if trace.output is not None else None,
        step_count=len(trace.steps),
        tool_call_count=len(trace.tool_calls),
        redactions=redactions,
        content_hash=trace.content_hash,
        raw_trace=trace.to_dict(),
    )
    db.add(followup)
    db.flush()

    follow_analysis = Analysis(
        execution_pk=followup.id,
        engine_version=result["engine_version"],
        status=result["status"],
        severity=result["severity"],
        categories=result["categories"],
        primary_category=result["primary_category"],
        failed_step_index=result["failed_step_index"],
        root_cause=result["root_cause"],
        what_happened=result["what_happened"],
        impact=result["impact"],
        recoverable=result["recoverable"],
        confidence=result["confidence"],
        evidence=result["evidence"],
        recommendations=result["recommendations"],
        prevention=result["prevention"],
        timeline=result["timeline"],
        llm_used=result["llm_used"],
        llm_model=result["llm_model"],
        llm_error=result["llm_error"],
        detection_notes=result["detection_notes"],
        primary_title=result.get("primary_title"),
        fixes=result.get("fixes", []),
        ml_prediction=result["ml"]["prediction"],
        ml_confidence=result["ml"]["confidence"],
        ml_top=result["ml"]["top"],
        confidence_factors=result.get("confidence_factors", []),
        causal_chain=result.get("causal_chain", []),
        severity_detail=result.get("severity_detail", {}),
    )
    from app.api.deps import redact_analysis_on_write

    redact_analysis_on_write(follow_analysis)
    db.add(follow_analysis)
    followup.status = result["status"]
    followup.severity = result["severity"]
    followup.primary_category = result["primary_category"]
    followup.categories = [c["category"] for c in result["categories"]]
    followup.analysis_count = 1
    db.flush()

    original_analysis = _latest_analysis(db, original.id)
    original_obs = _observe_execution(original, original_analysis)
    follow_obs = _observe_execution(followup, follow_analysis)

    verdict = verify_mod.verify(original_obs, follow_obs, remediation.plan or {}, mode=verify_mod.SIMULATED)
    remediation.verification = verdict
    remediation.verification_status = verdict["status"]
    remediation.verification_mode = verdict["mode"]
    remediation.verified_at = _now()
    remediation.verification_execution_pk = followup.id
    remediation.status = VERIFIED if verdict["resolved"] else NOT_VERIFIED
    db.flush()
    redact_agent_row(remediation)


    _event(
        db,
        original.id,
        "verify",
        f"verification-{verdict['status'].lower()}",
        f"Verification {verdict['status']}: " + (verdict["reasons"][0] if verdict["reasons"] else verdict["criteria"]),
        {
            "checks": verdict["checks"],
            "mode": verdict["mode"],
            "followup_execution_pk": followup.id,
        },
        remediation_id=remediation.id,
    )

    # ---- Learn
    if verdict["resolved"]:
        _learn_resolved(db, original, remediation, verdict)
    elif verdict["status"] in CONTINUE_STATUSES:
        _learn_continue(db, original, remediation, verdict, followup, follow_analysis)
    else:
        _event(
            db,
            original.id,
            "learn",
            "verification-inconclusive",
            "The follow-up could not confirm or deny the fix, so nothing was changed on the original run.",
            {"status": verdict["status"]},
            remediation_id=remediation.id,
        )
        _event(
            db,
            original.id,
            "learn",
            "session-stopped",
            f"The session stopped: {verdict['reasons'][0] if verdict['reasons'] else 'inconclusive verification'}",
            remediation_id=remediation.id,
        )

    db.commit()
    db.refresh(remediation)
    return {
        "verification": verdict,
        "followup_execution_pk": followup.id,
        "followup_analysis_id": follow_analysis.id,
        "remediation": remediation,
    }


def _learn_resolved(db: Session, original: Execution, remediation: Remediation, verdict: dict[str, Any]) -> None:
    """Write the outcome back: the run is now recovered, and why."""
    action = remediate_mod.get(remediation.action_id) or {}
    lesson = (
        f"'{action.get('title', remediation.action_id)}' resolved this failure. "
        f"Verification: {verdict['reasons'][0] if verdict['reasons'] else verdict['criteria']}"
    )
    # The original execution now has a verified resolution: it is recorded as
    # recovered (it did not complete on its own, a fix did it), and the reason
    # is kept so the status is never unexplained.
    original.status = RESOLVED_STATUS
    original.severity = "NONE"
    original.resolution = {
        "remediation_id": remediation.id,
        "action_id": remediation.action_id,
        "verified_at": _now().isoformat(),
        "verification_mode": verdict["mode"],
        "lesson": lesson,
    }
    _event(
        db,
        original.id,
        "learn",
        "resolution-recorded",
        lesson,
        {"action_id": remediation.action_id, "status": RESOLVED_STATUS},
        remediation_id=remediation.id,
    )
    _event(
        db,
        original.id,
        "learn",
        "session-stopped",
        "The session stopped: the fix was verified against a follow-up trace.",
        remediation_id=remediation.id,
    )


def _learn_continue(
    db: Session,
    original: Execution,
    remediation: Remediation,
    verdict: dict[str, Any],
    followup: Execution,
    follow_analysis: Analysis,
) -> None:
    """The fix did not work: record the lesson and start the next attempt."""
    reason = verdict["reasons"][0] if verdict["reasons"] else "The follow-up still fails."
    _event(
        db,
        original.id,
        "learn",
        "action-did-not-resolve",
        f"'{remediation.action_id}' did not resolve the failure: {reason}",
        {"action_id": remediation.action_id, "status": verdict["status"]},
        remediation_id=remediation.id,
    )

    limit = max_attempts()
    # Anything live (or in a state that can still move) means the loop already
    # has somewhere to go; proposing another plan would stack up.
    if _has_open_attempt(db, original.id):
        _event(
            db,
            original.id,
            "learn",
            "session-stopped",
            "The session stopped: an earlier plan is still open, so no new attempt was proposed.",
            remediation_id=remediation.id,
        )
        return
    used = attempts_used(db, original.id)
    if used >= limit:
        _event(
            db,
            original.id,
            "learn",
            "session-stopped",
            f"The session stopped: {used} of {limit} attempts used, and the problem is not resolved.",
            remediation_id=remediation.id,
        )
        return

    # Continue the loop from the follow-up trace: that is the newest observation.
    decision = decide_mod.decide(_observe_execution(followup, follow_analysis))
    previous = tried_actions(db, original.id)
    # If the action we would pick has already failed here, take the next
    # applicable one instead of re-proposing a dead end; with nothing left, the
    # escalation action is the honest answer.
    action_id = decision["action_id"]
    if action_id in previous:
        alternatives = remediate_mod.alternatives_for(decision.get("failure_category"), previous)
        action_id = alternatives[0] if alternatives else "reanalyze-rules"
        decision = {**decision, "action_reason": "The action that fits first was already tried without success, so the next applicable one is proposed."}
    attempt_no = used + 1
    plan = remediate_mod.build_plan(action_id, decision, attempt=attempt_no, previous_actions=previous)
    next_row = Remediation(
        execution_pk=original.id,
        analysis_id=follow_analysis.id,
        attempt=attempt_no,
        decision=decision,
        action_id=plan["action_id"],
        action_type=plan["action_type"],
        risk=plan["risk"],
        status=AWAITING_APPROVAL if plan["requires_approval"] else PROPOSED,
        plan=plan,
        requires_approval=plan["requires_approval"],
    )
    redact_agent_row(next_row)
    db.add(next_row)
    db.flush()
    _event(
        db,
        original.id,
        "decide",
        "plan-proposed",
        f"Attempt {plan['attempt']} of {limit}: proposed '{plan['title']}' ({plan['action_type']}).",
        {"action_id": plan["action_id"], "why": plan["why_selected"]},
        remediation_id=next_row.id,
    )


def stop(db: Session, execution_pk: int, reason: str) -> None:
    """Record an explicit stop, so a session never just trails off."""
    _event(db, execution_pk, "learn", "session-stopped", reason)
    db.commit()


def summary(db: Session, execution_pk: int) -> dict[str, Any]:
    """The whole session, for the API and the page."""
    rows = session_attempts(db, execution_pk)
    limit = max_attempts()
    events = events_for(db, execution_pk)
    resolved = any(row.verification_status == verify_mod.VERIFIED_RESOLVED for row in rows)
    stopped = [e for e in events if e.kind == "session-stopped"]
    return {
        "execution_pk": _key(execution_pk),
        "attempts_used": len(rows),
        "attempt_limit": limit,
        "attempts_left": max(0, limit - len(rows)),
        "resolved": resolved,
        "state": (
            "resolved"
            if resolved
            else "exhausted"
            if len(rows) >= limit
            else "awaiting_approval"
            if any(row.status == AWAITING_APPROVAL for row in rows)
            else "in_progress"
            if rows
            else "not_started"
        ),
        "stop_reason": stopped[-1].summary if stopped else None,
        "remediations": rows,
        "events": events,
    }


__all__ = [
    "PROPOSED",
    "AWAITING_APPROVAL",
    "REJECTED",
    "EXECUTED",
    "EXECUTION_FAILED",
    "ACTION_REFUSED",
    "VERIFIED",
    "NOT_VERIFIED",
    "approve",
    "attempts_used",
    "events_for",
    "execute",
    "max_attempts",
    "session_attempts",
    "start",
    "stop",
    "submit_verification",
    "summary",
    "tried_actions",
]
