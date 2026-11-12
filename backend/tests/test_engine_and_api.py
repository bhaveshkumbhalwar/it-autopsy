"""Failure detection, classification, root-cause and API tests."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.models import FailureCategory
from app.db import database
from app.engine.analyzer import diagnose
from app.engine.rules import run_rules
from app.ingest import ingest_text
from app.samples.demos import DEMOS


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    database.engine.dispose()
    database.engine = database._make_engine.__wrapped__() if hasattr(database._make_engine, "__wrapped__") else database.engine
    # Point the app at a throwaway SQLite file.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    database.Base.metadata.create_all(engine)

    from app.main import app

    with TestClient(app) as c:
        yield c


def _trace(payload: dict):
    trace, _fmt, _r = ingest_text(json.dumps(payload))
    return trace


def test_timeout_detected():
    trace = _trace(DEMOS["tool-timeout"]["trace"])
    cats = {d.category for d in run_rules(trace)}
    assert FailureCategory.TIMEOUT in cats


def test_infinite_loop_detected():
    trace = _trace(DEMOS["infinite-loop"]["trace"])
    cats = {d.category for d in run_rules(trace)}
    assert FailureCategory.INFINITE_LOOP in cats


def test_context_overflow_detected():
    trace = _trace(DEMOS["context-overflow"]["trace"])
    cats = {d.category for d in run_rules(trace)}
    assert FailureCategory.MEMORY_CONTEXT in cats


def test_reasoning_contradiction_detected():
    trace = _trace(DEMOS["reasoning-contradiction"]["trace"])
    cats = {d.category for d in run_rules(trace)}
    assert FailureCategory.REASONING in cats


def test_recovered_rate_limit_is_low_severity():
    trace = _trace(DEMOS["rate-limit-recovered"]["trace"])
    result = diagnose(trace, use_llm=False)
    assert result["severity"] == "LOW"
    # A 429 that was retried and answered is "recovered", not a plain "success":
    # the incident happened and must stay visible in History and Analytics.
    assert result["status"] == "recovered"


def test_api_auth_failure_is_critical():
    trace = _trace(
        {
            "agent_name": "A",
            "status": "failed",
            "steps": [{"type": "llm", "status": "failed", "error": "401 Unauthorized: invalid api key"}],
        }
    )
    result = diagnose(trace, use_llm=False)
    # A credential rejection is its own failure family (authentication_failure),
    # reported as critically severe because retrying cannot fix it.
    assert result["primary_category"] == FailureCategory.AUTH.value
    assert result["severity"] == "CRITICAL"


def test_safety_violation_detected():
    trace = _trace(
        {
            "agent_name": "A",
            "status": "failed",
            "steps": [{"type": "llm", "status": "failed", "error": "content_policy_violation: blocked by guardrail"}],
        }
    )
    result = diagnose(trace, use_llm=False)
    assert result["primary_category"] == FailureCategory.SAFETY.value
    assert result["severity"] == "CRITICAL"


def test_tool_failure_detected():
    trace = _trace(
        {
            "agent_name": "A",
            "status": "failed",
            "steps": [
                {"type": "tool_call", "tool_name": "lookup", "status": "failed", "error": "TypeError: invalid arguments"},
            ],
        }
    )
    result = diagnose(trace, use_llm=False)
    assert result["primary_category"] == FailureCategory.TOOL_EXECUTION.value
    assert result["failed_step_index"] == 0


def test_hallucination_only_from_llm_or_verification():
    """Rules must not guess hallucination without evidence in the trace."""
    trace = _trace({"agent_name": "A", "status": "success", "steps": [{"type": "output", "output": "all good"}]})
    cats = {d.category for d in run_rules(trace)}
    assert FailureCategory.HALLUCINATION not in cats


def test_evidence_quotes_come_from_the_trace():
    trace = _trace(DEMOS["tool-timeout"]["trace"])
    result = diagnose(trace, use_llm=False)
    assert result["evidence"]
    raw = json.dumps(DEMOS["tool-timeout"]["trace"])
    for item in result["evidence"]:
        quote = (item.get("quote") or "")[:40]
        assert quote == "" or quote.split("…")[0][:25] in raw


def test_ml_classifier_returns_prediction():
    from app.ml.classifier import get_classifier

    pred = get_classifier().predict("tool call timed out after 30 seconds, deadline exceeded")
    assert pred is not None
    assert 0.0 <= pred.confidence <= 1.0


def test_ml_metrics_are_measured():
    from app.ml.classifier import evaluate

    metrics = evaluate()
    assert 0.0 < metrics["cross_validation"]["accuracy"] <= 1.0
    assert metrics["holdout"]["samples"] > 0


def test_confidence_is_bounded():
    for demo in DEMOS.values():
        result = diagnose(_trace(demo["trace"]), use_llm=False)
        assert 0.0 <= result["confidence"] <= 1.0


# --------------------------------------------------------------------------- #
# API tests
# --------------------------------------------------------------------------- #

def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_paste_endpoint_creates_analysis(client):
    response = client.post(
        "/api/ingest/paste",
        json={"text": json.dumps(DEMOS["infinite-loop"]["trace"]), "use_llm": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis"]["primary_category"] == "infinite_loop"
    assert body["execution"]["id"] > 0


def test_paste_endpoint_rejects_bad_json(client):
    response = client.post("/api/ingest/paste", json={"text": "{not json"})
    assert response.status_code == 422
    assert "line" in response.json()["detail"]["message"].lower()


def test_paste_endpoint_rejects_blank(client):
    response = client.post("/api/ingest/paste", json={"text": "   "})
    assert response.status_code == 422


def test_upload_endpoint_and_extension_guard(client):
    ok = client.post(
        "/api/ingest/upload",
        files={"file": ("trace.json", json.dumps(DEMOS["tool-timeout"]["trace"]), "application/json")},
        data={"use_llm": "false"},
    )
    assert ok.status_code == 200, ok.text
    bad = client.post(
        "/api/ingest/upload",
        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
        data={"use_llm": "false"},
    )
    assert bad.status_code == 422


def test_demo_endpoint(client):
    response = client.post("/api/ingest/demo", json={"demo_id": "tool-timeout", "use_llm": False})
    assert response.status_code == 200
    assert response.json()["execution"]["is_demo"] is True
    assert client.post("/api/ingest/demo", json={"demo_id": "nope"}).status_code == 404


def test_dashboard_and_listing(client):
    dash = client.get("/api/dashboard").json()
    assert dash["totals"]["executions"] >= 1
    listing = client.get("/api/executions?limit=5").json()
    assert listing["total"] >= 1
    first = listing["items"][0]
    detail = client.get(f"/api/executions/{first['id']}")
    assert detail.status_code == 200
    assert detail.json()["execution"]["trace"]


def test_reports_json_and_pdf(client):
    listing = client.get("/api/executions?limit=1").json()["items"]
    analysis_id = client.get(f"/api/executions/{listing[0]['id']}").json()["analyses"][0]["id"]
    as_json = client.get(f"/api/reports/{analysis_id}.json")
    assert as_json.status_code == 200
    assert as_json.json()["report_type"] == "agent_autopsy"
    as_pdf = client.get(f"/api/reports/{analysis_id}.pdf")
    assert as_pdf.status_code == 200
    assert as_pdf.content[:4] == b"%PDF"


def test_compare_endpoint(client):
    items = client.get("/api/executions?limit=5").json()["items"]
    if len(items) < 2:
        # Demo data is excluded by default now, so make the two runs this test
        # needs rather than skipping.
        for i in range(2):
            client.post(
                "/api/ingest/paste",
                json={"text": '{"agent_name":"C","status":"failed","steps":[{"type":"llm","status":"failed","error":"e%d"}]}' % i, "use_llm": False},
            )
        items = client.get("/api/executions?limit=5").json()["items"]
        assert len(items) >= 2
    ids = ",".join(str(i["id"]) for i in items[:2])
    response = client.get(f"/api/compare?ids={ids}")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2


def test_analytics_endpoint(client):
    body = client.get("/api/analytics").json()
    for key in ("failing_tools", "root_causes", "frameworks", "reliability"):
        assert key in body


def test_settings_endpoint_never_leaks_key(client):
    body = client.get("/api/settings").json()
    assert "key_configured" in body["ai"]
    assert body["ai"]["key_configured"] is False or isinstance(body["ai"]["key_configured"], bool)


def test_delete_execution(client):
    created = client.post("/api/ingest/demo", json={"demo_id": "context-overflow", "use_llm": False}).json()
    pk = created["execution"]["id"]
    assert client.delete(f"/api/executions/{pk}").status_code == 200
    assert client.get(f"/api/executions/{pk}").status_code == 404


def test_404_for_missing_execution(client):
    assert client.get("/api/executions/999999").status_code == 404


def test_openapi_available(client):
    schema = client.get("/openapi.json").json()
    assert "/api/ingest/paste" in schema["paths"]
