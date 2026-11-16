"""The diagnostic agent's API: the loop, its plans, approvals and verifications.

Every endpoint here is wired to the real loop in :mod:`app.agent.session`; there
is no endpoint that only pretends to act. A plan that needs approval cannot be
executed until it is approved, and a verification result always names the
follow-up trace it was computed from.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import remediate as remediate_mod
from app.agent import session as agent_session
from app.agent import verify as verify_mod
from app.api.deps import (
    agent_event_to_dict,
    rate_limit,
    remediation_to_dict,
    require_auth,
    sanitize,
)
from app.db.database import Execution, Remediation, get_session as db_session

router = APIRouter(tags=["agent"], prefix="/agent")


# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #

class StartRequest(BaseModel):
    attempt_limit: int | None = Field(default=None, ge=1, le=10)


class ApprovalRequest(BaseModel):
    approved: bool
    by: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    # Approve and, when the action may run without a human, run it right away.
    # One deliberate click instead of two for the same decision.
    execute_if_ready: bool = False


class VerificationRequest(BaseModel):
    trace: str = Field(..., max_length=2_000_000)
    agent_name: str | None = Field(default=None, max_length=200)
    framework: str | None = Field(default=None, max_length=80)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _execution_or_404(db: Session, execution_pk: int) -> Execution:
    execution = db.get(Execution, execution_pk)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found.")
    return execution


def _remediation_or_404(db: Session, remediation_id: int) -> Remediation:
    remediation = db.get(Remediation, remediation_id)
    if not remediation:
        raise HTTPException(status_code=404, detail="Remediation not found.")
    return remediation


def _session_payload(db: Session, execution_pk: int) -> dict[str, Any]:
    data = agent_session.summary(db, execution_pk)
    return {
        **{k: v for k, v in data.items() if k not in {"remediations", "events"}},
        "remediations": [remediation_to_dict(row) for row in data["remediations"]],
        "events": [agent_event_to_dict(event) for event in data["events"]],
    }


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #

@router.get("/actions")
def list_actions(request: Request, _: None = Depends(require_auth)):
    """The action allowlist. The UI shows what the agent is and is not allowed to do."""
    rate_limit(request)
    return sanitize(
        {
            "actions": remediate_mod.all_actions(),
            "categories": {
                "automatic": "Runs in this app, no external effect.",
                "approval": "Runs only after a human approves it.",
                "recommendation": "Never executed by the agent; instructions only.",
            },
        }
    )


@router.get("/executions/{execution_pk}")
def read_session(
    execution_pk: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_auth),
):
    """The whole loop for one execution: attempts, decisions and activity."""
    rate_limit(request)
    _execution_or_404(db, execution_pk)
    return sanitize(_session_payload(db, execution_pk))


@router.post("/executions/{execution_pk}/start")
def start_session(
    execution_pk: int,
    payload: StartRequest,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_auth),
):
    """Observe, analyse, decide, and propose the next plan."""
    rate_limit(request)
    execution = _execution_or_404(db, execution_pk)
    result = agent_session.start(db, execution_pk, attempt_limit=payload.attempt_limit)
    payload_out = _session_payload(db, execution_pk)
    payload_out["started"] = result["started"]
    payload_out["reason"] = result.get("reason")
    return sanitize(payload_out)


@router.get("/remediations/{remediation_id}")
def get_remediation(
    remediation_id: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_auth),
):
    rate_limit(request)
    return sanitize(remediation_to_dict(_remediation_or_404(db, remediation_id)))


@router.post("/remediations/{remediation_id}/approve")
def approve_remediation(
    remediation_id: int,
    payload: ApprovalRequest,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_auth),
):
    """Approve or reject a proposed plan. Rejection stops the session."""
    rate_limit(request)
    remediation = _remediation_or_404(db, remediation_id)
    try:
        agent_session.approve(
            db, remediation, approved=payload.approved, by=payload.by, note=payload.note
        )
        executed = False
        # "Approve and run": only for a plan a human may execute at all. A
        # recommendation-only action is approved (recorded) and stops there,
        # and a failed result is reported rather than hidden.
        if payload.approved and payload.execute_if_ready and (remediation.plan or {}).get("executable"):
            agent_session.execute(db, remediation)
            executed = True
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sanitize(
        {
            "remediation": remediation_to_dict(remediation),
            "executed": executed,
            "session": _session_payload(db, remediation.execution_pk),
        }
    )


@router.post("/remediations/{remediation_id}/execute")
def execute_remediation(
    remediation_id: int,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_auth),
):
    """Run an automatic action, or an approved one. Anything else is refused with a reason."""
    rate_limit(request)
    remediation = _remediation_or_404(db, remediation_id)
    try:
        agent_session.execute(db, remediation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sanitize(
        {
            "remediation": remediation_to_dict(remediation),
            "session": _session_payload(db, remediation.execution_pk),
        }
    )


@router.post("/remediations/{remediation_id}/verify")
def verify_remediation(
    remediation_id: int,
    payload: VerificationRequest,
    request: Request,
    db: Session = Depends(db_session),
    _: None = Depends(require_auth),
):
    """Submit a follow-up trace and get a graded verification verdict."""
    rate_limit(request)
    remediation = _remediation_or_404(db, remediation_id)
    try:
        result = agent_session.submit_verification(
            db,
            remediation,
            payload.trace,
            agent_name=payload.agent_name,
            framework=payload.framework,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # ParseError and friends: the trace is the problem
        from app.ingest.util import ParseError

        if isinstance(exc, ParseError):
            raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
        raise
    return sanitize(
        {
            "verification": result["verification"],
            "followup_execution_pk": result["followup_execution_pk"],
            "followup_analysis_id": result["followup_analysis_id"],
            "remediation": remediation_to_dict(remediation),
            "session": _session_payload(db, remediation.execution_pk),
        }
    )


@router.get("/verification-statuses")
def verification_statuses(request: Request, _: None = Depends(require_auth)):
    """What each verdict means, so the page never has to invent a label."""
    rate_limit(request)
    return sanitize(
        {
            "statuses": [
                {"id": verify_mod.VERIFIED_RESOLVED, "label": "Verified resolved", "resolved": True},
                {"id": verify_mod.PARTIALLY_RESOLVED, "label": "Partially resolved", "resolved": False},
                {"id": verify_mod.STILL_FAILING, "label": "Still failing", "resolved": False},
                {"id": verify_mod.NEW_FAILURE, "label": "New failure", "resolved": False},
                {"id": verify_mod.INCONCLUSIVE, "label": "Inconclusive", "resolved": False},
            ],
            "modes": {
                verify_mod.SIMULATED: "Verified against a follow-up trace you submitted.",
                verify_mod.REAL: "Verified by the agent's own runtime reporting back.",
            },
        }
    )
