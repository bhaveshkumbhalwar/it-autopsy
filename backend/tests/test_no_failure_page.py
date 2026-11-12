"""A run with no failure is described as a run with no failure.

Covers the detail-page payload (observations, no fix), the dashboard counters
and the compare sentence.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.diff import build_comparison
from app.api.timeline import build_observations
from app.db import database
from tests.test_healthy_traces import CLEAN, PARTIAL, RECOVERED


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'nofailure.db'}", connect_args={"check_same_thread": False})
    previous = (database.engine, database.SessionLocal)
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    database.Base.metadata.create_all(engine)

    from app.main import app

    with TestClient(app) as c:
        yield c
    database.engine, database.SessionLocal = previous


def _ingest(client: TestClient, trace: dict) -> dict:
    response = client.post("/api/ingest/paste", json={"text": json.dumps(trace), "use_llm": False})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Detail payload
# --------------------------------------------------------------------------- #

def test_a_clean_run_reports_no_failure_and_no_fix(client):
    body = _ingest(client, CLEAN)
    detail = client.get(f"/api/executions/{body['execution']['id']}").json()
    analysis = detail["analyses"][0]
    assert analysis["status"] == "success"
    assert analysis["categories"] == []
    assert analysis["severity"] == "NONE"
    assert analysis["fixes"] == []
    # The timeline is kept: a healthy run still has steps to read.
    assert len(detail["timeline"]["items"]) == len(CLEAN["steps"])


def test_a_recovered_run_says_how_many_retries_it_needed(client):
    body = _ingest(client, RECOVERED)
    detail = client.get(f"/api/executions/{body['execution']['id']}").json()
    assert detail["analyses"][0]["status"] == "recovered"
    assert detail["execution"]["retry_count"] == 1
    retries = [o for o in detail["observations"] if o["kind"] == "retry"]
    assert retries and retries[0]["title"] == "Recovered after 1 retry"


def test_a_clean_run_has_no_retry_observation(client):
    body = _ingest(client, CLEAN)
    detail = client.get(f"/api/executions/{body['execution']['id']}").json()
    assert [o for o in detail["observations"] if o["kind"] == "retry"] == []


def test_a_failed_run_gets_no_observations(client):
    """Observations are for healthy runs only; a failure has a root cause."""
    body = _ingest(
        client,
        {
            "execution_id": "failing-1",
            "agent_name": "Failing Agent",
            "status": "failed",
            "steps": [
                {"type": "task", "name": "task received", "status": "success"},
                {"type": "tool_call", "name": "fetch", "tool_name": "fetch", "status": "failed",
                 "error": "httpx.ConnectError: connection refused"},
            ],
        },
    )
    detail = client.get(f"/api/executions/{body['execution']['id']}").json()
    assert detail["observations"] == []


def test_observations_are_only_non_failure_facts():
    trace = {"steps": [
        {"index": 0, "type": "tool_call", "tool_name": "slow", "status": "success", "duration_ms": 9000},
        {"index": 1, "type": "tool_call", "tool_name": "big", "status": "success", "tool_result": "x" * 9000},
    ]}
    observations = build_observations(trace, {"categories": [], "status": "success"})
    kinds = [o["kind"] for o in observations]
    assert "slow_step" in kinds
    assert "large_payload" in kinds
    assert all("fix" not in o and "recommendation" not in o for o in observations)


def test_a_partial_run_keeps_its_cause(client):
    body = _ingest(client, PARTIAL)
    detail = client.get(f"/api/executions/{body['execution']['id']}").json()
    analysis = detail["analyses"][0]
    assert analysis["status"] == "partial"
    assert [c["category"] for c in analysis["categories"]] == ["tool_execution_failure"]
    assert detail["observations"] == []


# --------------------------------------------------------------------------- #
# Dashboard counters
# --------------------------------------------------------------------------- #

def test_the_dashboard_counts_healthy_runs_and_never_shows_a_rate_of_nothing(client):
    empty = client.get("/api/dashboard").json()
    assert empty["totals"]["healthy"] == 0
    assert empty["totals"]["healthy_rate"] is None

    _ingest(client, CLEAN)
    _ingest(client, RECOVERED)
    _ingest(client, PARTIAL)
    _ingest(
        client,
        {
            "execution_id": "failing-2",
            "agent_name": "Failing Agent",
            "status": "failed",
            "steps": [{"type": "tool_call", "name": "fetch", "tool_name": "fetch", "status": "failed",
                       "error": "httpx.ConnectError: connection refused"}],
        },
    )
    totals = client.get("/api/dashboard").json()["totals"]
    assert totals["executions"] == 4
    assert totals["healthy"] == 3
    assert totals["failed"] == 1
    assert totals["healthy_rate"] == 0.75
    assert totals["success"] == 1
    assert totals["recovered"] == 1
    assert totals["partial"] == 1


# --------------------------------------------------------------------------- #
# Compare sentence
# --------------------------------------------------------------------------- #

def _compare_run(name: str, task: str, root_cause: str, category: str) -> dict:
    return {
        "name": name,
        "agent_name": name,
        "status": "failed",
        "categories": [category],
        "root_cause": root_cause,
        "steps": [{"index": 0, "type": "task", "status": "success", "input": task}],
    }


def test_the_compare_summary_reads_as_one_sentence():
    left = _compare_run(
        "Data Analyst Agent",
        "Analyse the attached 4000-row CSV and report the top 5 products by revenue.",
        "The model ran out of usable context at step 2 ('analyse rows'). Evidence: prompt is too long",
        "memory_context_failure",
    )
    right = _compare_run(
        "Support Agent",
        "Answer the customer question about the refund window.",
        "The final answer is inconsistent with the information the agent collected during execution. Evidence: said 30 days",
        "reasoning_failure",
    )
    summary = build_comparison([left, right])["summary"]

    # Lowercase mid-sentence clause after "because".
    assert "Data Analyst Agent failed because the model ran out of usable context at step 2" in summary
    assert "Support Agent failed because the final answer is inconsistent" in summary
    # The inputs are quoted, not pasted bare after the word "args".
    assert '"Analyse the attached 4000-row CSV and report the top 5 products by revenue."' in summary
    assert "args " not in summary
    # One sentence per run, joined by a semicolon: no stray capitals mid-sentence.
    assert summary.count(";") == 2
    assert summary.endswith(".")


def test_a_partial_run_is_named_as_partial_in_the_compare_summary():
    run = _compare_run("Report Agent", "summarise q1", "A tool call at step 2 ('read_csv') failed to execute successfully.", "tool_execution_failure")
    run["status"] = "partial"
    other = _compare_run("Support Agent", "answer the question", "The final answer is inconsistent.", "reasoning_failure")
    summary = build_comparison([run, other])["summary"]
    assert "Report Agent returned a partial result because a tool call at step 2" in summary


# --------------------------------------------------------------------------- #
# One HTTP library per page
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "error, expected, forbidden",
    [
        ("httpx.ReadTimeout: timed out (read timeout=10)", "import httpx", "import requests"),
        ("requests.exceptions.ReadTimeout: Read timed out. (read timeout=10)", "import requests", "import httpx"),
    ],
)
def test_every_snippet_on_the_page_uses_the_library_the_trace_named(client, error, expected, forbidden):
    body = _ingest(
        client,
        {
            "execution_id": f"library-{expected.split()[-1]}",
            "agent_name": "Scraper Agent",
            "status": "failed",
            "steps": [
                {"type": "task", "name": "task received", "status": "success"},
                {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "failed",
                 "error": error},
            ],
        },
    )
    detail = client.get(f"/api/executions/{body['execution']['id']}").json()
    fixes = detail["analyses"][0]["fixes"]
    assert fixes
    for fix in fixes:
        code = fix["snippet"]["code"]
        assert forbidden not in code, fix["family"]
        assert expected in code or fix["family"] != "timeout_failure"
        # Every snippet still compiles: it is shown to be copied, not run.
        compile(code, "<snippet>", "exec")
