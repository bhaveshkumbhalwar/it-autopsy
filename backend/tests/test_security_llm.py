"""Security and LLM-configuration tests (Phase 1).

The important one is the last block: a configured API key must never appear in
any API response or any stored row, whatever path produced the text.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core import redact
from app.core.config import PROVIDER_DEFAULTS, llm_config_problem, resolve_llm, settings

FAKE_GROQ_KEY = "gsk_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"


# --------------------------------------------------------------------------- #
# Redaction patterns
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "text",
    [
        f"Invalid API key provided: {FAKE_GROQ_KEY}",
        "Bearer abcdef1234567890abcdef",
        "sk-proj-abcdefghijklmnop1234",
        "sk-ant-api03-abcdefghijklmnop",
        f"https://api.groq.com/openai/v1?api_key={FAKE_GROQ_KEY}",
        "token hf_abcdefghijklmnopqrst",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_secret_shapes_are_masked(text):
    cleaned, count = redact.redact_text(text)
    assert count >= 1
    assert "REDACTED" in cleaned
    for fragment in (FAKE_GROQ_KEY, "abcdef1234567890abcdef", "sk-proj-", "sk-ant-", "hf_", "BEGIN RSA PRIVATE KEY"):
        assert fragment not in cleaned


def test_email_and_phone_are_masked():
    cleaned, count = redact.redact_text("contact ops@example.com or +91 98765 43210")
    assert count == 2
    assert "ops@example.com" not in cleaned
    assert "98765" not in cleaned


def test_configured_key_is_masked_even_when_shape_changed(monkeypatch):
    """A provider may quote a reformatted fragment; the exact value is still removed."""
    monkeypatch.setattr(settings, "llm_api_key", "abcd1234efgh5678ijkl", raising=False)
    cleaned, count = redact.redact_secret("the server rejected abcd1234efgh5678ijkl.", "abcd1234efgh5678ijkl")
    assert count >= 1
    assert "abcd1234efgh5678ijkl" not in cleaned


def test_redact_obj_walks_nested_structures():
    payload = {"a": [{"b": f"key {FAKE_GROQ_KEY}"}], "c": "plain"}
    cleaned = redact.redact_obj(payload)
    assert FAKE_GROQ_KEY not in json.dumps(cleaned)
    assert cleaned["c"] == "plain"


# --------------------------------------------------------------------------- #
# Provider configuration
# --------------------------------------------------------------------------- #

def test_provider_defaults_cover_openai_and_groq():
    assert PROVIDER_DEFAULTS["groq"]["base_url"] == "https://api.groq.com/openai/v1"
    assert PROVIDER_DEFAULTS["openai"]["base_url"] == "https://api.openai.com/v1"


def test_provider_inferred_from_key_prefix(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "", raising=False)
    provider, base_url, _model = resolve_llm(api_key=FAKE_GROQ_KEY)
    assert provider == "groq"
    assert base_url.endswith("/openai/v1")


def test_mismatched_provider_is_reported_without_the_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", FAKE_GROQ_KEY, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    problem = llm_config_problem()
    assert problem is not None
    assert "groq" in problem.lower()
    # The message must never echo the key or a long fragment of it.
    assert FAKE_GROQ_KEY not in problem
    assert FAKE_GROQ_KEY[4:20] not in problem


def test_matching_provider_has_no_problem(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", FAKE_GROQ_KEY, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "groq", raising=False)
    monkeypatch.setattr(settings, "llm_model", "openai/gpt-oss-20b", raising=False)
    assert llm_config_problem() is None


# --------------------------------------------------------------------------- #
# Health check states (no network: the provider call is stubbed)
# --------------------------------------------------------------------------- #

def _health(monkeypatch, key: str, provider: str = "groq", model: str = "llama-3.3-70b-versatile", error=None):
    from app.engine import llm

    monkeypatch.setattr(settings, "llm_api_key", key, raising=False)
    monkeypatch.setattr(settings, "llm_provider", provider, raising=False)
    monkeypatch.setattr(settings, "llm_model", model, raising=False)
    if error is not None:
        def boom(_messages, json_mode=False):
            raise error
        monkeypatch.setattr(llm, "_post_chat", boom)
    else:
        monkeypatch.setattr(llm, "_post_chat", lambda messages, json_mode=False: ({"status": "ok"}, '{"status": "ok"}'))
    return llm.check_health(force=True)


def test_health_key_missing(monkeypatch):
    assert _health(monkeypatch, "")["state"] == "key_missing"


def test_health_misconfigured(monkeypatch):
    assert _health(monkeypatch, FAKE_GROQ_KEY, provider="openai")["state"] == "misconfigured"


def test_health_ready(monkeypatch):
    result = _health(monkeypatch, FAKE_GROQ_KEY)
    assert result["state"] == "ready"


@pytest.mark.parametrize(
    "status,expected",
    [(401, "key_invalid"), (403, "key_invalid"), (404, "model_unavailable"), (429, "rate_limited"), (500, "unreachable")],
)
def test_health_maps_provider_errors(monkeypatch, status, expected):
    from app.engine.llm import LLMError

    result = _health(monkeypatch, FAKE_GROQ_KEY, error=LLMError("provider said no", status=status))
    assert result["state"] == expected
    assert FAKE_GROQ_KEY not in result["message"]


def test_health_is_cached_between_calls(monkeypatch):
    from app.engine import llm

    calls = {"n": 0}

    def counting(messages, json_mode=False):
        calls["n"] += 1
        return {"status": "ok"}, '{"status": "ok"}'

    monkeypatch.setattr(settings, "llm_api_key", FAKE_GROQ_KEY, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "groq", raising=False)
    monkeypatch.setattr(settings, "llm_model", "llama-3.3-70b-versatile", raising=False)
    monkeypatch.setattr(llm, "_post_chat", counting)
    llm.check_health(force=True)
    second = llm.check_health()
    assert calls["n"] == 1
    assert second["cached"] is True


# --------------------------------------------------------------------------- #
# The key never leaves the API — response or database
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client_and_db(tmp_path, monkeypatch):
    from app.db import database
    from app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'redaction.db'}", connect_args={"check_same_thread": False})
    database.engine.dispose()
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # Tables must exist in the fresh file *and* in the module-level engine that
    # get_session() captured at import time.
    database.Base.metadata.create_all(database.engine)

    monkeypatch.setattr(settings, "llm_api_key", FAKE_GROQ_KEY, raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    # The ingest limiter is per process and this fixture runs many ingests.
    monkeypatch.setattr(settings, "rate_limit_analyze", 10_000, raising=False)
    with TestClient(app) as client:
        yield client, database.SessionLocal


def _trace_with_key() -> str:
    return json.dumps(
        {
            "agent_name": "Leaky Agent",
            "status": "failed",
            "steps": [
                {"type": "task", "input": "call the API"},
                {
                    "type": "llm",
                    "status": "failed",
                    "error": f"401 Unauthorized: Incorrect API key provided: {FAKE_GROQ_KEY}. "
                    "Contact support@example.com",
                },
            ],
        }
    )


def test_no_key_in_any_api_response_or_stored_row(client_and_db):
    client, session_factory = client_and_db

    responses: list[str] = []
    ingest = client.post("/api/ingest/paste", json={"text": _trace_with_key(), "use_llm": False})
    assert ingest.status_code == 200, ingest.text
    responses.append(ingest.text)
    execution_pk = ingest.json()["execution"]["id"]
    analysis_id = ingest.json()["analysis"]["id"]

    for path in (
        "/api/executions",
        f"/api/executions/{execution_pk}",
        f"/api/analyses/{analysis_id}",
        "/api/dashboard",
        "/api/analytics",
        "/api/settings",
        "/api/health",
        "/api/formats",
        f"/api/reports/{analysis_id}.json",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        responses.append(response.text)

    # The stored row must be clean too.
    from app.db.database import Analysis, Execution

    with session_factory() as db:
        for row in db.scalars(select(Execution)).all():
            responses.append(json.dumps(row.raw_trace, default=str))
            responses.append(str(row.output))
        for row in db.scalars(select(Analysis)).all():
            for field in ("root_cause", "what_happened", "impact", "llm_error", "llm_prompt", "llm_raw_response"):
                responses.append(str(getattr(row, field)))
            for field in ("evidence", "categories", "recommendations", "timeline", "detection_notes"):
                responses.append(json.dumps(getattr(row, field), default=str))

    combined = "\n".join(responses)
    assert FAKE_GROQ_KEY not in combined, "the configured key leaked"
    assert "sk-" + "a1b2" not in combined


def test_llm_health_endpoint_reports_a_state(client_and_db, monkeypatch):
    from app.engine import llm

    client, _ = client_and_db
    monkeypatch.setattr(llm, "_post_chat", lambda messages, json_mode=False: ({"status": "ok"}, '{"status": "ok"}'))
    monkeypatch.setattr(settings, "llm_provider", "groq", raising=False)
    monkeypatch.setattr(settings, "llm_model", "llama-3.3-70b-versatile", raising=False)
    body = client.get("/api/llm/health?refresh=true").json()
    assert body["state"] in {"ready", "key_invalid", "key_missing", "misconfigured", "unreachable", "model_unavailable", "rate_limited"}
    assert FAKE_GROQ_KEY not in json.dumps(body)


def test_settings_ai_block_never_contains_the_key(client_and_db):
    client, _ = client_and_db
    ai = client.get("/api/settings").json()["ai"]
    assert "api_key" not in json.dumps(ai).replace("key_configured", "")
    assert ai["key_configured"] is True
    assert FAKE_GROQ_KEY not in json.dumps(ai)


# --------------------------------------------------------------------------- #
# Data migration: backup before merge, then a real merge
# --------------------------------------------------------------------------- #

def test_duplicate_merge_backs_up_first_and_reports_rows(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.db import database
    from app.db import migrations, migrations_data

    db_path = tmp_path / "dup.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    database.engine.dispose()
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}", raising=False)
    database.Base.metadata.create_all(engine)

    trace = {
        "agent_name": "A",
        "status": "failed",
        "steps": [{"index": 0, "type": "task", "status": "failed", "error": "boom"}],
    }
    with database.SessionLocal() as db:
        for _ in range(3):
            db.add(database.Execution(execution_id="exec-dup", agent_name="A", raw_trace=dict(trace), step_count=1))
        db.commit()

    report = migrations.run_migrations()
    assert report["duplicates"]["rows_removed"] == 2
    assert report["backup"]["rows"]["executions"] == 3
    assert (db_path.parent / "backups").is_dir()
    assert list((db_path.parent / "backups").glob("rows-*.json"))

    with database.SessionLocal() as db:
        assert len(db.scalars(select(database.Execution)).all()) == 1

    # Idempotent: a second run merges nothing and writes no new backup.
    second = migrations.run_migrations()
    assert second["duplicates"]["rows_removed"] == 0
    assert second["backup"]["rows"] is None


# --------------------------------------------------------------------------- #
# Ingestion dedupe: the same run submitted twice is one execution
# --------------------------------------------------------------------------- #

DUP_TRACE = '{"agent_name":"Dupe","status":"failed","steps":[{"type":"llm","status":"failed","error":"same"}]}'


def test_same_trace_twice_creates_one_execution(client_and_db):
    client, _ = client_and_db
    first = client.post("/api/ingest/paste", json={"text": DUP_TRACE, "use_llm": False})
    second = client.post("/api/ingest/paste", json={"text": DUP_TRACE, "use_llm": False})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["execution"]["id"] == second.json()["execution"]["id"]
    assert second.json()["execution"]["analysis_count"] == 2
    assert len(client.get("/api/executions").json()["items"]) == 1


def test_different_traces_are_kept_apart(client_and_db):
    client, _ = client_and_db
    other = DUP_TRACE.replace("same", "different")
    a = client.post("/api/ingest/paste", json={"text": DUP_TRACE, "use_llm": False}).json()
    b = client.post("/api/ingest/paste", json={"text": other, "use_llm": False}).json()
    assert a["execution"]["id"] != b["execution"]["id"]
    assert len(client.get("/api/executions").json()["items"]) == 2


def test_recovered_status_is_reported(client_and_db):
    client, _ = client_and_db
    text = (
        '{"agent_name":"Recovered","status":"success","steps":['
        '{"type":"llm","status":"failed","error":"429 Too Many Requests"},'
        '{"type":"retry","status":"success"}]}'
    )
    body = client.post("/api/ingest/paste", json={"text": text, "use_llm": False}).json()
    assert body["analysis"]["status"] == "recovered"


# --------------------------------------------------------------------------- #
# A failing provider call that echoes the key must be stored redacted
# --------------------------------------------------------------------------- #

def test_provider_error_echoing_the_key_is_stored_redacted(monkeypatch):
    """Providers do quote the key back ('Incorrect API key provided: gsk_…').

    That message becomes ``llm_error`` and is shown in the UI, so it has to be
    masked at the source — before it can reach a database or a response.
    """
    import httpx

    from app.engine import llm
    from app.engine.analyzer import diagnose
    from app.ingest import ingest_text

    echo = (
        '{"error": {"message": "Incorrect API key provided: '
        + FAKE_GROQ_KEY
        + '. You can find your API key at https://console.groq.com/keys", "type": "invalid_request_error"}}'
    )

    def fake_post(url, headers=None, json=None, **_kwargs):
        return httpx.Response(401, text=echo, request=httpx.Request("POST", url))

    monkeypatch.setattr(settings, "llm_api_key", FAKE_GROQ_KEY, raising=False)
    monkeypatch.setattr(settings, "llm_provider", "groq", raising=False)
    monkeypatch.setattr(settings, "llm_model", "llama-3.3-70b-versatile", raising=False)
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, **kw: fake_post(url, **kw))

    trace, _, _ = ingest_text(DUP_TRACE)
    result = diagnose(trace, use_llm=True, capture_llm=True)

    assert result["llm_used"] is False
    error = result["llm_error"] or ""
    assert error, "the failure must still be reported"
    assert "rejected the credentials" in error or "401" in error
    assert FAKE_GROQ_KEY not in error
    assert FAKE_GROQ_KEY[4:20] not in error, "a long fragment of the key leaked"
    assert "[REDACTED]" in error

    # And the same text survives persistence redaction unchanged (idempotent).
    from app.core.redact import redact_secret

    assert redact_secret(error, FAKE_GROQ_KEY)[0] == error


# --------------------------------------------------------------------------- #
# Phase 2: demo data is out of the numbers unless asked for; low-sample tools
# --------------------------------------------------------------------------- #

def _ingest(client, agent: str, error: str = "boom", status: str = "failed", extra: list | None = None):
    import json as _json

    text = _json.dumps(
        {
            "agent_name": agent,
            "status": status,
            "steps": [{"type": "llm", "status": "failed", "error": error}, *(extra or [])],
        }
    )
    return client.post("/api/ingest/paste", json={"text": text, "use_llm": False}).json()


def test_demo_executions_are_excluded_by_default(client_and_db):
    client, _ = client_and_db
    _ingest(client, "Real Agent")
    demo = client.post("/api/ingest/demo", json={"demo_id": "tool-timeout", "use_llm": False})
    assert demo.status_code == 200

    default = client.get("/api/dashboard").json()
    assert default["totals"]["executions"] == 1
    assert default["totals"]["demo_executions"] == 0
    assert default["totals"]["include_demo"] is False
    assert [r["agent_name"] for r in default["recent"]] == ["Real Agent"]

    with_demo = client.get("/api/dashboard?include_demo=true").json()
    assert with_demo["totals"]["executions"] == 2
    assert with_demo["totals"]["demo_executions"] == 1
    assert with_demo["totals"]["include_demo"] is True

    assert len(client.get("/api/executions").json()["items"]) == 1
    assert len(client.get("/api/executions?include_demo=true").json()["items"]) == 2


def test_analytics_ignores_demo_analyses_too(client_and_db):
    client, _ = client_and_db
    _ingest(client, "Real Agent")
    client.post("/api/ingest/demo", json={"demo_id": "infinite-loop", "use_llm": False})

    default = client.get("/api/analytics").json()
    counted = sum(row["count"] for row in default["root_causes"])
    assert counted == 1, "only the real run's root cause should be counted"

    with_demo = client.get("/api/analytics?include_demo=true").json()
    assert sum(row["count"] for row in with_demo["root_causes"]) == 2


def test_low_sample_tool_rates_are_flagged(client_and_db):
    client, _ = client_and_db
    # One execution whose single tool call failed -> n=1, below the threshold.
    body = client.post(
        "/api/ingest/paste",
        json={
            "text": '{"agent_name":"Tool Agent","status":"failed","tool_calls":[{"tool":"search_docs","status":"failed"}],'
            '"steps":[{"type":"tool","tool_name":"search_docs","status":"failed","error":"timeout"}]}',
            "use_llm": False,
        },
    ).json()
    assert body["execution"]["id"]

    tools = client.get("/api/analytics").json()["failing_tools"]
    row = next(t for t in tools if t["tool"] == "search_docs")
    assert row["calls"] == 1
    assert row["low_sample"] is True
    assert client.get("/api/analytics").json()["low_sample_threshold"] == 3


def test_recovered_status_is_visible_and_filterable(client_and_db):
    client, _ = client_and_db
    text = (
        '{"agent_name":"Recovered Agent","status":"success","steps":['
        '{"type":"llm","status":"failed","error":"429 Too Many Requests"},'
        '{"type":"retry","status":"success"}]}'
    )
    assert client.post("/api/ingest/paste", json={"text": text, "use_llm": False}).json()["analysis"]["status"] == "recovered"

    listing = client.get("/api/executions?status=recovered").json()
    assert listing["total"] == 1
    assert listing["items"][0]["status"] == "recovered"

    totals = client.get("/api/dashboard").json()["totals"]
    assert totals["recovered"] == 1

    counts = client.get("/api/analytics").json()["status_counts"]
    assert counts["recovered"] == 1
    assert counts["success"] == 0
