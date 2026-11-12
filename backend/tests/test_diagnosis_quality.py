"""Diagnosis-quality tests: dedupe, policy precision, causal root cause,
category-specific recommendations, and explainable confidence.

Each test builds a realistic trace for one failure family and asserts both what
must be reported *and* what must not be reported (the false positives that
motivated this work).
"""

from __future__ import annotations

import json

import pytest

from app.core.models import FailureCategory, NormalizedTrace, Status, StepType, TraceStep
from app.engine.analyzer import diagnose
from app.engine.recommendations import CATALOGUE, for_categories


def _trace(steps: list[dict], status: str = "failed", **kwargs) -> NormalizedTrace:
    """Build a normalized trace the way the ingest layer would."""
    from app.ingest import ingest_text

    payload = {"agent_name": kwargs.pop("agent_name", "Test Agent"), "status": status, "steps": steps, **kwargs}
    trace, _fmt, _redactions = ingest_text(json.dumps(payload))
    return trace


def _diag(steps: list[dict], status: str = "failed", **kwargs) -> dict:
    return diagnose(_trace(steps, status, **kwargs), use_llm=False)


def _cats(result: dict) -> set[str]:
    return {c["category"] for c in result["categories"]}


def _rec_ids(result: dict) -> set[str]:
    return {r["id"] for r in result["recommendations"]}


# --------------------------------------------------------------------------- #
# 1. Duplicate evidence
# --------------------------------------------------------------------------- #

def test_same_event_recorded_three_times_appears_once():
    """A failed span, its error and a tool summary describe one event."""
    result = _diag(
        [
            {"type": "task", "input": "look up order 42"},
            {
                "type": "tool_call",
                "tool_name": "http_get",
                "step_id": "s1",
                "status": "failed",
                "error": "504 Gateway Timeout after 30s",
            },
            {"type": "error", "step_id": "s1", "error": "504 Gateway Timeout after 30s"},
        ]
    )
    quotes = [e for e in result["evidence"] if e["step_index"] is not None]
    assert len(quotes) == 1, result["evidence"]
    assert result["evidence"][0]["error_type"] == "timeout"


def test_evidence_rows_are_unique_per_event():
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "db_query", "status": "failed", "error": "psycopg OperationalError: connection refused"},
            {"type": "tool_call", "tool_name": "db_query", "status": "failed", "error": "psycopg OperationalError: connection refused"},
        ]
    )
    keys = [(e["step_index"], e["error_type"], e["quote"]) for e in result["evidence"]]
    assert len(keys) == len(set(keys))


def test_different_errors_on_different_steps_both_kept():
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "search", "status": "failed", "error": "Unknown tool 'websearch'"},
            {"type": "tool_call", "tool_name": "fetch", "status": "failed", "error": "504 Gateway Timeout"},
        ]
    )
    steps = {e["step_index"] for e in result["evidence"] if e["step_index"] is not None}
    assert len(steps) >= 2


# --------------------------------------------------------------------------- #
# 2. False-positive safety violations
# --------------------------------------------------------------------------- #

def test_database_failure_is_not_a_safety_violation():
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "db_write", "status": "failed",
             "error": "OperationalError: could not connect to server: Connection refused (port 5432)"},
            {"type": "error", "error": "task aborted because the database is unavailable"},
        ]
    )
    assert FailureCategory.SAFETY.value not in _cats(result)
    assert FailureCategory.DATABASE.value in _cats(result)
    assert result["primary_category"] == FailureCategory.DATABASE.value


def test_tool_failure_worded_with_policy_words_is_not_safety():
    """'blocked' / 'denied' in an infrastructure error must not read as policy."""
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "api", "status": "failed",
             "error": "connection blocked by upstream firewall; request denied (timeout)"},
        ]
    )
    assert FailureCategory.SAFETY.value not in _cats(result)


def test_explicit_policy_block_is_reported_as_safety():
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "send_email", "status": "failed",
             "error": "Blocked by policy: sending email to external domains requires human approval"},
        ]
    )
    assert result["primary_category"] == FailureCategory.SAFETY.value
    assert result["severity"] == "CRITICAL"
    # A policy block must not be dressed up as a tool failure.
    assert FailureCategory.TOOL_EXECUTION.value not in _cats(result)


def test_model_refusal_is_safety_not_tool_failure():
    result = _diag(
        [
            {"type": "llm", "status": "success",
             "output": "I can't help with creating a phishing page, as that violates the usage policy."},
        ],
        status="partial",
    )
    assert FailureCategory.SAFETY.value in _cats(result)


# --------------------------------------------------------------------------- #
# 3. Root cause accuracy
# --------------------------------------------------------------------------- #

def test_root_cause_is_earliest_failure_not_the_last_one():
    result = _diag(
        [
            {"type": "task", "input": "summarise the report"},
            {"type": "tool_call", "tool_name": "fetch_report", "status": "failed", "error": "503 Service Unavailable"},
            {"type": "llm", "status": "failed", "error": "cannot summarise: no document was retrieved"},
            {"type": "output", "status": "failed", "error": "ValueError: empty response"},
        ]
    )
    assert result["failed_step_index"] == 1
    assert result["causal_chain"][0]["role"] == "root_cause"
    downstream = [n for n in result["causal_chain"] if n["role"] == "downstream"]
    assert len(downstream) == 2
    assert all(n["caused_by"] == 1 for n in downstream)


def test_parent_span_links_decide_the_root_cause():
    """The child span's error is a consequence of the failed parent span."""
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "parent_op", "step_id": "p1", "status": "failed",
             "error": "OperationalError: deadlock detected"},
            {"type": "tool_call", "tool_name": "child_op", "step_id": "c1", "parent_id": "p1",
             "status": "failed", "error": "IntegrityError: transaction rolled back"},
        ]
    )
    chain = {n["step_index"]: n for n in result["causal_chain"]}
    assert chain[0]["role"] == "root_cause"
    assert chain[1]["caused_by"] == 0
    assert result["failed_step_index"] == 0


def test_repeated_identical_error_is_counted_once_as_downstream():
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "db", "status": "failed", "error": "connection refused"},
            {"type": "tool_call", "tool_name": "db", "status": "failed", "error": "connection refused"},
            {"type": "tool_call", "tool_name": "db", "status": "failed", "error": "connection refused"},
        ]
    )
    assert result["failed_step_index"] == 0
    assert sum(1 for n in result["causal_chain"] if n["role"] == "root_cause") == 1


def test_categories_carry_their_causal_role():
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "db", "status": "failed", "error": "psycopg OperationalError: connection refused"},
            {"type": "llm", "status": "failed", "error": "context window exceeded: maximum context length is 8192 tokens"},
        ]
    )
    roles = {c["category"]: c["causal_role"] for c in result["categories"]}
    assert roles[FailureCategory.DATABASE.value] == "root_cause"
    assert roles[FailureCategory.MEMORY_CONTEXT.value] == "downstream"


# --------------------------------------------------------------------------- #
# 4. Context-aware recommendations
# --------------------------------------------------------------------------- #

def test_database_errors_get_database_recommendations_only():
    result = _diag(
        [{"type": "tool_call", "tool_name": "db", "status": "failed", "error": "psycopg OperationalError: connection refused"}]
    )
    ids = _rec_ids(result)
    assert "db-connectivity" in ids
    for unrelated in ("safety-guardrails", "policy-approval-flow", "context-management", "grounded-answering"):
        assert unrelated not in ids, f"{unrelated} must not appear for a database failure"


def test_api_errors_get_api_recommendations_not_database_ones():
    result = _diag([{"type": "llm", "status": "failed", "error": "openai 503 Service Unavailable, upstream overloaded"}])
    ids = _rec_ids(result)
    assert "retry-with-backoff" in ids
    assert "provider-fallback" in ids
    assert not {i for i in ids if i.startswith("db-")}


def test_auth_errors_get_credential_recommendations():
    result = _diag([{"type": "llm", "status": "failed", "error": "401 Unauthorized: invalid api key"}])
    ids = _rec_ids(result)
    assert "credential-rotation" in ids
    assert "retry-with-backoff" not in ids, "auth failures must not be retried blindly"


def test_memory_errors_get_context_recommendations():
    result = _diag([{"type": "llm", "status": "failed", "error": "context window exceeded: too many tokens (8192 max)"}])
    ids = _rec_ids(result)
    assert "context-management" in ids
    assert not {i for i in ids if i.startswith("db-")}
    assert "safety-guardrails" not in ids


def test_policy_violations_get_security_recommendations():
    result = _diag(
        [{"type": "tool_call", "tool_name": "delete_repo", "status": "failed",
          "error": "Blocked by policy: destructive action requires human approval"}]
    )
    ids = _rec_ids(result)
    assert "safety-guardrails" in ids
    assert "policy-approval-flow" in ids
    assert not {i for i in ids if i.startswith("db-")}
    assert "retry-with-backoff" not in ids


def test_tool_failures_get_tool_recommendations():
    result = _diag([{"type": "tool_call", "tool_name": "calc", "status": "failed", "error": "invalid arguments: missing required parameter 'x'"}])
    ids = _rec_ids(result)
    assert "tool-error-handling" in ids
    assert "argument-validation" in ids
    assert "safety-guardrails" not in ids


@pytest.mark.parametrize(
    "category,forbidden_prefix",
    [
        (FailureCategory.DATABASE.value, "safety"),
        (FailureCategory.AUTH.value, "db-"),
        (FailureCategory.MEMORY_CONTEXT.value, "db-"),
        (FailureCategory.SAFETY.value, "db-"),
    ],
)
def test_catalogue_never_returns_unrelated_families(category, forbidden_prefix):
    ids = {r["id"] for r in for_categories([category])}
    assert not {i for i in ids if i.startswith(forbidden_prefix)}


def test_every_recommendation_declares_its_categories():
    for item in CATALOGUE:
        assert item["categories"], f"{item['id']} must declare the categories it serves"
        for required in ("id", "problem", "fix", "reason", "benefit", "priority"):
            assert item.get(required), f"{item['id']} missing {required}"


# --------------------------------------------------------------------------- #
# 5. Confidence and severity
# --------------------------------------------------------------------------- #

def test_confidence_is_explained_by_its_factors():
    result = _diag(
        [
            {"type": "tool_call", "tool_name": "db", "status": "failed", "error": "psycopg OperationalError: connection refused"},
            {"type": "llm", "status": "failed", "error": "cannot continue without the database"},
        ]
    )
    factors = result["confidence_factors"]
    assert factors and all({"factor", "detail", "weight"} <= set(f) for f in factors)
    assert 0.1 <= result["confidence"] <= 0.98
    # The arithmetic must add up: the score is the sum of its stated parts.
    assert abs(sum(f["weight"] for f in factors) - result["confidence"]) <= 0.011


def test_confidence_differs_when_evidence_is_weaker():
    strong = _diag(
        [{"type": "tool_call", "tool_name": "db", "status": "failed", "error": "psycopg OperationalError: connection refused"}]
    )
    weak = _diag([{"type": "task", "input": "do something"}, {"type": "output", "status": "success", "output": "done"}], status="unknown")
    assert weak["confidence"] < strong["confidence"]
    assert weak["categories"] == []


def test_severity_and_confidence_are_independent():
    """A certain diagnosis can be low impact, and vice versa."""
    recovered = _diag(
        [
            {"type": "tool_call", "tool_name": "api", "status": "failed", "error": "429 Too Many Requests"},
            {"type": "retry", "name": "retry 1"},
            {"type": "output", "status": "success", "output": "final answer"},
        ],
        status="success",
    )
    failed = _diag(
        [{"type": "tool_call", "tool_name": "db", "status": "failed", "error": "psycopg OperationalError: connection refused"}]
    )
    assert recovered["severity"] in ("LOW", "MODERATE")
    assert failed["severity"] in ("HIGH", "CRITICAL")
    # Both are well evidenced, so confidence is high in both cases.
    assert recovered["confidence"] >= 0.5
    assert failed["confidence"] >= 0.5


# --------------------------------------------------------------------------- #
# End-to-end through the API, to prove the UI receives the new fields
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import database

    engine = create_engine(f"sqlite:///{tmp_path / 'quality.db'}", connect_args={"check_same_thread": False})
    database.engine.dispose()
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    database.Base.metadata.create_all(engine)

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_api_returns_deduped_evidence_and_causal_chain(client):
    payload = {
        "agent_name": "DB Agent",
        "status": "failed",
        "steps": [
            {"type": "task", "input": "update the record"},
            {"type": "tool_call", "tool_name": "db_write", "status": "failed", "error": "psycopg OperationalError: connection refused"},
            {"type": "error", "error": "psycopg OperationalError: connection refused"},
            {"type": "llm", "status": "failed", "error": "unable to finish without the database"},
        ],
    }
    response = client.post("/api/ingest/paste", json={"text": json.dumps(payload), "use_llm": False})
    print(response.text[:2000])
    assert response.status_code == 200, response.text
    analysis = response.json()["analysis"]

    assert analysis["primary_category"] == FailureCategory.DATABASE.value
    # The task step is index 0; the database failure is the first failing step.
    assert analysis["failed_step_index"] == 1
    assert analysis["confidence_factors"]
    assert analysis["causal_chain"][0]["role"] == "root_cause"
    assert all("role" in e for e in analysis["evidence"])
    # One row for the duplicated database error.
    db_rows = [e for e in analysis["evidence"] if e.get("error_type") == "database"]
    assert len(db_rows) == 1
    assert not any(c["category"] == FailureCategory.SAFETY.value for c in analysis["categories"])
