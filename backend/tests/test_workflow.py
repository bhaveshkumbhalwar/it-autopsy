"""End-to-end workflow tests: analyze → history → details → persistence.

These exercise the exact routes the SPA calls (all under /api) and assert the
contract the frontend depends on, so a route rename cannot silently break the UI.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api import deps as api_deps

from app.samples.demos import DEMOS

VALID_TRACE = {
    "execution_id": "wf-001",
    "agent_name": "Workflow Agent",
    "status": "failed",
    "steps": [
        {"type": "task", "input": "Fetch the pricing page", "status": "success"},
        {"type": "tool_call", "tool_name": "http_get", "status": "failed", "error": "504 Gateway Timeout after 30s"},
    ],
}

JSONL_TRACE = "\n".join(
    json.dumps(run)
    for run in [
        {"id": "r1", "trace_id": "wf-jsonl", "name": "Agent", "run_type": "chain", "start_time": "2024-05-01T10:00:00Z", "end_time": "2024-05-01T10:00:03Z", "inputs": {"q": "x"}},
        {"id": "r2", "trace_id": "wf-jsonl", "parent_run_id": "r1", "name": "search", "run_type": "tool", "start_time": "2024-05-01T10:00:01Z", "error": "ToolException: quota"},
    ]
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import database

    db_path = tmp_path_factory.mktemp("workflow") / "wf.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    database.engine.dispose()
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    database.Base.metadata.create_all(engine)

    # The rate limiter is per-process; raise the ceiling so the suite can make
    # many calls, and reset the counters.
    from app.core.config import settings as app_settings

    app_settings.rate_limit_requests = 10_000
    app_settings.rate_limit_analyze = 10_000
    api_deps._BUCKETS.clear()

    from app.main import app

    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Issue 1 — analyze (paste/upload) must be POST /api/ingest/*
# --------------------------------------------------------------------------- #

def test_paste_route_is_post_only(client):
    """GET on the paste route is rejected with JSON that says why — never the SPA."""
    response = client.get("/api/ingest/paste")
    assert response.status_code in (404, 405), response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "No API route matches" in response.json()["detail"] or "method" in response.json()["detail"].lower()


def test_unknown_api_path_never_returns_the_spa(client):
    """An unknown /api path is JSON, not index.html with 200 (which hid real 404s)."""
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_paste_analyze_saves_and_returns_analysis(client):
    response = client.post("/api/ingest/paste", json={"text": json.dumps(VALID_TRACE), "use_llm": False})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis"]["primary_category"] in {"timeout_failure", "tool_execution_failure"}
    assert body["execution"]["id"] > 0
    assert body["execution"]["trace"]["steps"]


def test_upload_route_accepts_jsonl(client):
    response = client.post(
        "/api/ingest/upload",
        files={"file": ("trace.jsonl", JSONL_TRACE, "application/x-ndjson")},
        data={"use_llm": "false", "agent_name": "JSONL Agent"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["format"] == "langsmith"


# --------------------------------------------------------------------------- #
# Issue 2 — history listing must exist at GET /api/executions
# --------------------------------------------------------------------------- #

def test_history_listing_route_exists(client):
    response = client.get("/api/executions?limit=5")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body and "total" in body
    assert body["total"] >= 1
    row = body["items"][0]
    for key in ("id", "execution_id", "agent_name", "created_at", "status", "primary_category", "severity", "is_demo"):
        assert key in row


def test_history_filters_and_search(client):
    assert client.get("/api/executions?status=failed").status_code == 200
    assert client.get("/api/executions?category=timeout_failure").status_code == 200
    assert client.get("/api/executions?severity=HIGH").status_code == 200
    assert client.get("/api/executions?q=workflow").status_code == 200


def test_detail_route_and_persistence(client):
    created = client.post("/api/ingest/paste", json={"text": json.dumps(VALID_TRACE), "use_llm": False}).json()
    pk = created["execution"]["id"]

    detail = client.get(f"/api/executions/{pk}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["analyses"], "analysis must be persisted with the execution"
    assert payload["execution"]["trace"]

    # Re-fetch (simulates a page refresh) and confirm it is still there.
    again = client.get(f"/api/executions/{pk}")
    assert again.status_code == 200
    assert again.json()["execution"]["execution_id"] == VALID_TRACE["execution_id"]


def test_unknown_execution_is_404_with_detail(client):
    response = client.get("/api/executions/999999")
    assert response.status_code == 404, response.text
    assert "detail" in response.json()


# --------------------------------------------------------------------------- #
# Issue 3 — supported formats endpoint the Analyze page loads
# --------------------------------------------------------------------------- #

def test_formats_endpoint(client):
    response = client.get("/api/formats")
    assert response.status_code == 200
    body = response.json()
    assert len(body["formats"]) == 6
    assert ".jsonl" in body["allowed_extensions"]


# --------------------------------------------------------------------------- #
# Issue 5 — input scenarios
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "text",
    [
        "",  # empty
        "   ",  # whitespace
        "{not json",  # invalid JSON
        '{"a": 1}\n{"b": 2,,}',  # invalid JSONL line
        "plain text that is not json",  # text log, no JSON
    ],
)
def test_invalid_inputs_return_meaningful_422(client, text):
    response = client.post("/api/ingest/paste", json={"text": text})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict) and detail.get("message")


def test_validation_error_is_readable(client):
    """A body missing `text` must explain which field is wrong."""
    response = client.post("/api/ingest/paste", json={"use_llm": False})
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], dict)
    assert "text" in body["detail"]["message"]


def test_multiple_tool_calls_trace(client):
    trace = {
        "agent_name": "Multi Tool",
        "status": "success",
        "steps": [
            {"type": "task", "input": "plan a trip"},
            {"type": "tool_call", "tool_name": "flights", "tool_arguments": {"to": "DEL"}, "status": "success", "tool_result": "3 options"},
            {"type": "tool_call", "tool_name": "hotels", "tool_arguments": {"city": "DEL"}, "status": "success", "tool_result": "12 hotels"},
            {"type": "tool_call", "tool_name": "weather", "tool_arguments": {"city": "DEL"}, "status": "success", "tool_result": "31C"},
            {"type": "output", "output": "Here is your plan."},
        ],
    }
    response = client.post("/api/ingest/paste", json={"text": json.dumps(trace), "use_llm": False})
    assert response.status_code == 200
    body = response.json()
    assert body["execution"]["tool_call_count"] == 3
    assert body["analysis"]["status"] == "success"


def test_repeated_tool_calls_are_infinite_loop(client):
    steps = [{"type": "task", "input": "get price"}]
    for _ in range(4):
        steps.append({"type": "tool_call", "tool_name": "http_get", "tool_arguments": {"url": "https://x.dev/p"}, "status": "success"})
    steps.append({"type": "error", "error": "recursion limit reached"})
    response = client.post(
        "/api/ingest/paste",
        json={"text": json.dumps({"agent_name": "Looper", "status": "failed", "steps": steps}), "use_llm": False},
    )
    assert response.status_code == 200
    assert response.json()["analysis"]["primary_category"] == "infinite_loop"


def test_missing_information_trace_is_honest(client):
    """A trace with no steps must still succeed but say what is missing."""
    response = client.post(
        "/api/ingest/paste",
        json={"text": json.dumps({"agent_name": "Empty", "status": "unknown"}), "use_llm": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert any("steps" in w.lower() for w in body["warnings"])


def test_demo_endpoint_matches_frontend_contract(client):
    response = client.post("/api/ingest/demo", json={"demo_id": "infinite-loop", "use_llm": False})
    assert response.status_code == 200
    body = response.json()
    assert body["execution"]["is_demo"] is True
    assert body["analysis"]["timeline"]


def test_report_downloads_for_saved_analysis(client):
    listing = client.get("/api/executions?limit=1").json()["items"]
    analysis_id = client.get(f"/api/executions/{listing[0]['id']}").json()["analyses"][0]["id"]
    assert client.get(f"/api/reports/{analysis_id}.json").status_code == 200
    pdf = client.get(f"/api/reports/{analysis_id}.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_dashboard_reflects_saved_executions(client):
    dash = client.get("/api/dashboard").json()
    listing = client.get("/api/executions?limit=1").json()
    assert dash["totals"]["executions"] == listing["total"]


def test_demo_corpus_all_analyse(client):
    for demo_id in DEMOS:
        response = client.post("/api/ingest/demo", json={"demo_id": demo_id, "use_llm": False})
        assert response.status_code == 200, (demo_id, response.text)
