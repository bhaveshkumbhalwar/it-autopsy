"""The diagnostic agent loop: Observe -> Analyze -> Decide -> Act -> Verify -> Learn.

These tests exercise the loop through the HTTP API the SPA calls, so a broken
route or a plan that cannot actually be executed fails here rather than in the
browser. They also pin the safety rules: an approval action cannot run without
approval, a recommendation-only action never runs at all, and nothing is called
"resolved" without a verification that says so.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agent import decide as decide_mod
from app.agent import remediate as remediate_mod
from app.agent import verify as verify_mod
from app.api import deps as api_deps


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(tmp_path_factory):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import database

    db_path = tmp_path_factory.mktemp("agent") / "agent.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    database.engine.dispose()
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    database.Base.metadata.create_all(engine)

    from app.core.config import settings as app_settings

    app_settings.rate_limit_requests = 100_000
    app_settings.rate_limit_analyze = 100_000
    app_settings.agent_max_attempts = 3
    api_deps._BUCKETS.clear()

    from app.main import app

    with TestClient(app) as c:
        yield c


def _trace(execution_id: str, *, step_error: str | None, tool: str = "fetch_page", status: str = "failed", extra_steps=None):
    steps = [
        {"type": "task", "input": "Fetch and summarise the paper", "status": "success"},
        {"type": "planning", "name": "plan", "status": "success"},
        {"type": "llm", "name": "reason", "status": "success"},
    ]
    if step_error:
        steps.append(
            {"type": "tool_call", "tool_name": tool, "status": status, "error": step_error}
        )
    else:
        steps.append({"type": "tool_call", "tool_name": tool, "status": "success", "result": "ok"})
    steps.append({"type": "output", "name": "answer", "status": "success" if not step_error else "failed"})
    if extra_steps:
        steps.extend(extra_steps)
    return {
        "execution_id": execution_id,
        "agent_name": "Paper Agent",
        "framework": "custom",
        "status": status,
        "task_description": "Fetch and summarise the paper",
        "steps": steps,
    }


TIMEOUT_ERROR = "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='arxiv.org', port=443): Read timed out. (read timeout=30)"
AUTH_ERROR = "openai.AuthenticationError: 401 Unauthorized - invalid api key"


def _ingest(client: TestClient, trace: dict) -> int:
    response = client.post("/api/ingest/paste", json={"text": json.dumps(trace), "use_llm": False})
    assert response.status_code == 200, response.text
    return response.json()["execution"]["id"]


def _start(client: TestClient, execution_pk: int) -> dict:
    response = client.post(f"/api/agent/executions/{execution_pk}/start", json={})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 1. a failing trace produces a decision with evidence
# --------------------------------------------------------------------------- #

def test_failing_trace_produces_a_decision_with_evidence(client):
    pk = _ingest(client, _trace("agent-001", step_error=TIMEOUT_ERROR))
    session = _start(client, pk)

    assert session["attempts_used"] == 1
    remediation = session["remediations"][0]
    decision = remediation["decision"]
    assert decision["verdict"] == "diagnosed"
    assert decision["failure_category"]
    assert decision["evidence"], "a diagnosis must carry the evidence it rests on"
    assert decision["root_cause"]
    assert decision["confidence"] > 0
    assert decision["root_cause_step"] == 4

    # The decision is also in the activity log, in order.
    phases = [event["phase"] for event in session["events"]]
    assert phases[:3] == ["observe", "analyze", "decide"]


# --------------------------------------------------------------------------- #
# 2. a plan carries every field the brief requires
# --------------------------------------------------------------------------- #

def test_plan_carries_problem_root_cause_solution_risk_and_criteria(client):
    pk = _ingest(client, _trace("agent-002", step_error=TIMEOUT_ERROR))
    plan = _start(client, pk)["remediations"][0]["plan"]

    for field in (
        "problem",
        "root_cause",
        "proposed_solution",
        "why_selected",
        "expected_outcome",
        "risk",
        "requires_approval",
        "verification_criteria",
        "action_type",
        "attempt",
    ):
        assert field in plan, field
    assert plan["risk"] in {"low", "medium", "high"}
    assert plan["verification_criteria"]
    assert plan["action_type"] in {"automatic", "approval", "recommendation"}


# --------------------------------------------------------------------------- #
# 3. an automatic action executes and is recorded
# --------------------------------------------------------------------------- #

def test_automatic_action_executes_and_is_recorded(client):
    pk = _ingest(client, _trace("agent-003", step_error=TIMEOUT_ERROR))
    session = _start(client, pk)
    remediation_id = session["remediations"][0]["id"]

    # The proposal is for a retry (recommendation-only); the automatic
    # re-analysis is a separate action the operator can pick up later. Execute
    # the proposed one and confirm it is refused rather than silently skipped.
    response = client.post(f"/api/agent/remediations/{remediation_id}/execute")
    assert response.status_code == 200, response.text
    body = response.json()
    remediation = body["remediation"]
    if remediation["action_type"] == "recommendation":
        assert remediation["status"] == "action_refused"
        assert remediation["execution_result"]["code"] == "recommendation-only"
    else:
        assert remediation["status"] == "executed"
        assert remediation["executed_at"]
    # Either way the activity log has an "act" entry that says what happened.
    acts = [e for e in body["session"]["events"] if e["phase"] == "act"]
    assert acts and acts[-1]["kind"].startswith("action-")


def test_automatic_reanalysis_runs_and_updates_the_analysis(client):
    from app.db.database import Analysis, Remediation, session_scope

    pk = _ingest(client, _trace("agent-004", step_error=TIMEOUT_ERROR))
    _start(client, pk)

    # Build an automatic plan the way the loop does, then execute it.
    from app.agent import session as agent_session

    with session_scope() as db:
        before = len(db.query(Analysis).filter(Analysis.execution_pk == pk).all())
        row = Remediation(
            execution_pk=pk,
            analysis_id=None,
            attempt=2,
            decision={"root_cause": "stale analysis"},
            action_id="reanalyze-rules",
            action_type="automatic",
            risk="low",
            status="proposed",
            plan={
                "action_id": "reanalyze-rules",
                "title": "Re-run the diagnosis with the rule engine",
                "action_type": "automatic",
                "risk": "low",
                "verification_criteria": "The re-analysis agrees with the current rules.",
            },
        )
        db.add(row)
        db.commit()
        remediation_id = row.id

    response = client.post(f"/api/agent/remediations/{remediation_id}/execute")
    assert response.status_code == 200, response.text
    remediation = response.json()["remediation"]
    assert remediation["status"] == "executed"
    assert remediation["execution_result"]["output"]["category"]

    with session_scope() as db:
        after = len(db.query(Analysis).filter(Analysis.execution_pk == pk).all())
    assert after == before + 1


# --------------------------------------------------------------------------- #
# 4. approval-required actions cannot run without approval
# --------------------------------------------------------------------------- #

def test_approval_action_cannot_execute_before_approval(client):
    from app.db.database import Remediation, session_scope

    pk = _ingest(client, _trace("agent-005", step_error=TIMEOUT_ERROR))
    _start(client, pk)

    with session_scope() as db:
        row = Remediation(
            execution_pk=pk,
            attempt=2,
            decision={},
            action_id="reanalyze-with-llm",
            action_type="approval",
            risk="low",
            status="awaiting_approval",
            requires_approval=True,
            plan={
                "action_id": "reanalyze-with-llm",
                "title": "Re-run the diagnosis with the LLM explanation layer",
                "action_type": "approval",
                "risk": "low",
            },
        )
        db.add(row)
        db.commit()
        remediation_id = row.id

    refused = client.post(f"/api/agent/remediations/{remediation_id}/execute")
    assert refused.status_code == 200, refused.text
    assert refused.json()["remediation"]["status"] == "action_refused"
    assert refused.json()["remediation"]["execution_result"]["code"] == "approval-required"

    approved = client.post(f"/api/agent/remediations/{remediation_id}/approve", json={"approved": True, "by": "test"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["remediation"]["approved"] is True
    assert approved.json()["remediation"]["approved_by"] == "test"

    # Approved now, so it runs (the LLM layer is off in tests and says so).
    executed = client.post(f"/api/agent/remediations/{remediation_id}/execute")
    assert executed.status_code == 200, executed.text
    assert executed.json()["remediation"]["status"] in {"executed", "execution_failed"}


# --------------------------------------------------------------------------- #
# 5. a rejected plan stops the session
# --------------------------------------------------------------------------- #

def test_rejection_stops_the_session(client):
    pk = _ingest(client, _trace("agent-006", step_error=TIMEOUT_ERROR))
    session = _start(client, pk)
    remediation_id = session["remediations"][0]["id"]

    response = client.post(
        f"/api/agent/remediations/{remediation_id}/approve",
        json={"approved": False, "note": "not now"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["remediation"]["status"] == "rejected"
    assert body["session"]["stop_reason"]
    assert any(e["kind"] == "session-stopped" for e in body["session"]["events"])

    # A rejected plan cannot be approved again or executed.
    again = client.post(f"/api/agent/remediations/{remediation_id}/approve", json={"approved": True})
    assert again.status_code == 409
    executed = client.post(f"/api/agent/remediations/{remediation_id}/execute")
    assert executed.status_code == 409


# --------------------------------------------------------------------------- #
# 6. successful verification marks the run resolved, with the evidence
# --------------------------------------------------------------------------- #

def test_successful_verification_resolves_the_run(client):
    pk = _ingest(client, _trace("agent-007", step_error=TIMEOUT_ERROR))
    session = _start(client, pk)
    remediation_id = session["remediations"][0]["id"]

    followup = _trace("agent-007-fixed", step_error=None, status="success")
    response = client.post(
        f"/api/agent/remediations/{remediation_id}/verify",
        json={"trace": json.dumps(followup)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    verification = body["verification"]
    assert verification["status"] == verify_mod.VERIFIED_RESOLVED
    assert verification["resolved"] is True
    assert verification["mode"] == "simulated"
    assert verification["evidence"]["followup"]["status"] == "success"
    assert body["session"]["state"] == "resolved"

    detail = client.get(f"/api/executions/{pk}").json()
    assert detail["execution"]["status"] == "recovered"
    assert detail["execution"]["resolution"]["action_id"] == body["remediation"]["action_id"]
    assert detail["agent"]["resolved"] is True


# --------------------------------------------------------------------------- #
# 7. unsuccessful verification: still failing, then a new attempt
# --------------------------------------------------------------------------- #

def test_unsuccessful_verification_starts_the_next_attempt(client):
    pk = _ingest(client, _trace("agent-008", step_error=TIMEOUT_ERROR))
    session = _start(client, pk)
    remediation_id = session["remediations"][0]["id"]

    response = client.post(
        f"/api/agent/remediations/{remediation_id}/verify",
        json={"trace": json.dumps(_trace("agent-008-again", step_error=TIMEOUT_ERROR))},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verification"]["status"] == verify_mod.STILL_FAILING
    assert body["session"]["attempts_used"] == 2, "an unresolved verification must start another attempt"

    # The action that failed is not proposed again.
    next_plan = body["session"]["remediations"][-1]
    assert next_plan["attempt"] == 2
    assert next_plan["plan"]["repeat_of_failed_action"] is False
    assert next_plan["action_id"] != body["remediation"]["action_id"]
    assert any(e["kind"] == "action-did-not-resolve" for e in body["session"]["events"])


def test_new_failure_is_reported_as_new_failure(client):
    pk = _ingest(client, _trace("agent-009", step_error=TIMEOUT_ERROR))
    remediation_id = _start(client, pk)["remediations"][0]["id"]

    followup = _trace("agent-009-other", step_error=None, status="success")
    followup["steps"].append(
        {"type": "tool_call", "tool_name": "summarise", "status": "failed", "error": "openai.BadRequestError: context length exceeded"}
    )
    followup["status"] = "failed"
    response = client.post(
        f"/api/agent/remediations/{remediation_id}/verify",
        json={"trace": json.dumps(followup)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["verification"]["status"] in {
        verify_mod.NEW_FAILURE,
        verify_mod.PARTIALLY_RESOLVED,
    }


# --------------------------------------------------------------------------- #
# 8. the attempt limit is enforced and reported
# --------------------------------------------------------------------------- #

def test_attempt_limit_is_enforced(client):
    pk = _ingest(client, _trace("agent-010", step_error=TIMEOUT_ERROR))
    _start(client, pk)
    for _ in range(3):
        current = client.get(f"/api/agent/executions/{pk}").json()
        open_rows = [r for r in current["remediations"] if r["status"] in {"proposed", "awaiting_approval"}]
        if not open_rows:
            break
        client.post(
            f"/api/agent/remediations/{open_rows[-1]['id']}/verify",
            json={"trace": json.dumps(_trace("agent-010-again", step_error=TIMEOUT_ERROR))},
        )

    final = client.get(f"/api/agent/executions/{pk}").json()
    assert final["attempts_used"] <= final["attempt_limit"]
    assert final["state"] in {"exhausted", "in_progress", "awaiting_approval"}
    if final["attempts_used"] >= final["attempt_limit"]:
        assert final["state"] == "exhausted"
        assert "attempt" in (final["stop_reason"] or "").lower()

    started = client.post(f"/api/agent/executions/{pk}/start", json={})
    assert started.status_code == 200
    if final["attempts_used"] >= final["attempt_limit"]:
        assert started.json()["started"] is False


# --------------------------------------------------------------------------- #
# 9. insufficient evidence is reported as such, not as a guess
# --------------------------------------------------------------------------- #

def test_insufficient_evidence_is_explicit(client):
    # A run that says it failed but carries no error text and no rule signal.
    trace = {
        "execution_id": "agent-011",
        "agent_name": "Quiet Agent",
        "status": "failed",
        "steps": [{"type": "task", "input": "do something", "status": "failed"}],
    }
    pk = _ingest(client, trace)
    session = _start(client, pk)
    decision = session["remediations"][0]["decision"]
    assert decision["verdict"] == "insufficient_evidence"
    assert decision["root_cause"] is None
    assert decision["uncertainty"], "an unsure decision must say what it is missing"
    assert decision["needed_evidence"]
    assert session["remediations"][0]["action_id"] == "collect-better-trace"


def test_healthy_run_is_not_remediated(client):
    trace = _trace("agent-012", step_error=None, status="success")
    pk = _ingest(client, trace)
    session = _start(client, pk)
    decision = session["remediations"][0]["decision"]
    assert decision["verdict"] == "insufficient_evidence"
    assert decision["retry_appropriate"] is False


# --------------------------------------------------------------------------- #
# 10. history shows the whole process
# --------------------------------------------------------------------------- #

def test_history_shows_the_whole_process(client):
    pk = _ingest(client, _trace("agent-013", step_error=TIMEOUT_ERROR))
    remediation_id = _start(client, pk)["remediations"][0]["id"]
    client.post(f"/api/agent/remediations/{remediation_id}/execute")
    client.post(
        f"/api/agent/remediations/{remediation_id}/verify",
        json={"trace": json.dumps(_trace("agent-013-fixed", step_error=None, status="success"))},
    )

    detail = client.get(f"/api/executions/{pk}").json()
    agent = detail["agent"]
    assert agent["attempts_used"] >= 1
    phases = [event["phase"] for event in agent["events"]]
    for phase in ("observe", "analyze", "decide", "act", "verify", "learn"):
        assert phase in phases, phase
    assert agent["resolved"] is True
    assert agent["remediations"][0]["verification"]["checks"], "verification keeps its checks"

    # The follow-up run exists in its own right, and the original points at it.
    followup_pk = agent["remediations"][0]["verification_execution_pk"]
    followup = client.get(f"/api/executions/{followup_pk}").json()
    assert followup["execution"]["execution_id"] == "agent-013-fixed"


# --------------------------------------------------------------------------- #
# safety: the allowlist, and the categories that must never be executed
# --------------------------------------------------------------------------- #

def test_action_catalogue_is_an_allowlist():
    response_actions = remediate_mod.all_actions()
    ids = {action["id"] for action in response_actions}
    assert "reanalyze-rules" in ids
    assert "verify-credentials" in ids
    # Every action declares a category and a verification criterion.
    for action in response_actions:
        assert action["action_type"] in {"automatic", "approval", "recommendation"}
        assert action["risk"] in {"low", "medium", "high"}
        assert action["verification"]
        assert action["why_this"]


def test_unknown_action_is_refused_not_executed():
    result = remediate_mod.execute(
        {"action_id": "rm -rf /", "action_type": "automatic"}, approved=True, context={}
    )
    assert result["status"] == "refused"
    assert result["code"] == "unknown-action"


def test_recommendation_actions_never_execute():
    for action_id in ("verify-credentials", "check-db-configuration", "bounded-retry", "escalate-to-human"):
        result = remediate_mod.execute(
            {"action_id": action_id, "action_type": "recommendation"}, approved=True, context={}
        )
        assert result["status"] == "refused"
        assert result["code"] == "recommendation-only"


def test_credential_failure_is_never_a_retry():
    observation = {
        "engine": {"categories": ["authentication_failure"], "evidence": [{"quote": AUTH_ERROR}]},
        "signals": {"status": "failed"},
        "tool_calls": [],
    }
    decision = decide_mod.decide(observation)
    assert decision["retry_appropriate"] is False
    assert decision["action_id"] == "verify-credentials"
    assert "credential" in decision["action_reason"].lower() or "auth" in decision["action_reason"].lower()


def test_plan_never_repeats_a_failed_action():
    decision = {"root_cause": "x", "root_cause_step": 3}
    plan = remediate_mod.build_plan("reanalyze-rules", decision, attempt=2, previous_actions=["reanalyze-rules"])
    assert plan["repeat_of_failed_action"] is True
    assert plan["executable"] is False


def test_error_signature_ignores_numbers():
    assert verify_mod.error_signature("read timeout=30 at step 4") == verify_mod.error_signature(
        "read timeout=60 at step 9"
    )
    assert verify_mod.error_signature("read timeout") != verify_mod.error_signature("connection refused")


def test_verification_requires_a_real_followup(client):
    pk = _ingest(client, _trace("agent-014", step_error=TIMEOUT_ERROR))
    remediation_id = _start(client, pk)["remediations"][0]["id"]

    empty = client.post(f"/api/agent/remediations/{remediation_id}/verify", json={"trace": "not a trace"})
    assert empty.status_code in {422, 409}, empty.text

    # A follow-up that never reached the failing step cannot confirm anything.
    partial = {
        "execution_id": "agent-014-partial",
        "agent_name": "Paper Agent",
        "status": "failed",
        "steps": [{"type": "task", "input": "x", "status": "success"}],
    }
    response = client.post(
        f"/api/agent/remediations/{remediation_id}/verify", json={"trace": json.dumps(partial)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["verification"]["status"] == verify_mod.INCONCLUSIVE
    assert response.json()["session"]["state"] != "resolved"


def test_secrets_in_a_followup_trace_are_not_returned(client):
    pk = _ingest(client, _trace("agent-015", step_error=TIMEOUT_ERROR))
    remediation_id = _start(client, pk)["remediations"][0]["id"]
    secret = "gsk_" + "a" * 48
    followup = _trace("agent-015-fixed", step_error=None, status="success")
    followup["steps"].append({"type": "llm", "name": "call", "status": "success", "error": f"key {secret}"})
    response = client.post(
        f"/api/agent/remediations/{remediation_id}/verify", json={"trace": json.dumps(followup)}
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text


# --------------------------------------------------------------------------- #
# the Approve button: approval state survives, and "approve and run" works
# --------------------------------------------------------------------------- #

def test_approval_gated_plan_keeps_its_approval_state(client):
    """An approved approval-action stays 'awaiting_approval' so it can then be run."""
    from app.db.database import Remediation, session_scope

    pk = _ingest(client, _trace("agent-016", step_error=TIMEOUT_ERROR))
    _start(client, pk)
    with session_scope() as db:
        row = Remediation(
            execution_pk=pk,
            attempt=2,
            decision={},
            action_id="reanalyze-with-llm",
            action_type="approval",
            risk="low",
            status="awaiting_approval",
            requires_approval=True,
            plan={"action_id": "reanalyze-with-llm", "title": "LLM re-analysis", "action_type": "approval", "risk": "low", "executable": True},
        )
        db.add(row)
        db.commit()
        remediation_id = row.id

    body = client.post(
        f"/api/agent/remediations/{remediation_id}/approve", json={"approved": True, "by": "tester"}
    ).json()
    assert body["remediation"]["approved"] is True
    assert body["remediation"]["status"] == "awaiting_approval", "approval must not clear the gate marker"
    assert body["executed"] is False

    run = client.post(f"/api/agent/remediations/{remediation_id}/execute")
    assert run.status_code == 200, run.text
    assert run.json()["remediation"]["status"] in {"executed", "execution_failed"}


def test_approve_and_run_executes_in_one_call(client):
    """One click does both: the action runs, and the response says so."""
    from app.db.database import Remediation, session_scope

    pk = _ingest(client, _trace("agent-017", step_error=TIMEOUT_ERROR))
    _start(client, pk)
    with session_scope() as db:
        row = Remediation(
            execution_pk=pk,
            attempt=2,
            decision={},
            action_id="reanalyze-rules",
            action_type="automatic",
            risk="low",
            status="proposed",
            requires_approval=False,
            plan={
                "action_id": "reanalyze-rules",
                "title": "Re-run the diagnosis",
                "action_type": "automatic",
                "risk": "low",
                "executable": True,
            },
        )
        db.add(row)
        db.commit()
        remediation_id = row.id

    body = client.post(
        f"/api/agent/remediations/{remediation_id}/approve",
        json={"approved": True, "execute_if_ready": True},
    ).json()
    assert body["executed"] is True
    assert body["remediation"]["approved"] is True
    assert body["remediation"]["status"] == "executed"
    assert body["remediation"]["executed_at"]
    assert any(e["phase"] == "act" for e in body["session"]["events"])


def test_approve_and_run_never_runs_a_recommendation(client):
    """A recommendation-only plan is approved and recorded, never executed."""
    pk = _ingest(client, _trace("agent-018", step_error=AUTH_ERROR))
    session = _start(client, pk)
    remediation_id = session["remediations"][0]["id"]
    assert session["remediations"][0]["action_type"] == "recommendation"

    body = client.post(
        f"/api/agent/remediations/{remediation_id}/approve",
        json={"approved": True, "execute_if_ready": True},
    ).json()
    assert body["executed"] is False
    assert body["remediation"]["approved"] is True
    assert body["remediation"]["status"] == "proposed"
    assert body["remediation"]["executed_at"] is None
    assert not any(e["phase"] == "act" for e in body["session"]["events"])


def test_rejecting_from_the_approve_endpoint_still_stops(client):
    pk = _ingest(client, _trace("agent-019", step_error=TIMEOUT_ERROR))
    remediation_id = _start(client, pk)["remediations"][0]["id"]
    body = client.post(
        f"/api/agent/remediations/{remediation_id}/approve",
        json={"approved": False, "execute_if_ready": True},
    ).json()
    assert body["remediation"]["status"] == "rejected"
    assert body["executed"] is False
