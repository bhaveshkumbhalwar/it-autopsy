"""Persistence tests for the SQLite case store."""

from __future__ import annotations

import json

from src import database


def test_schema_created(tmp_path):
    db = database.DB(path=tmp_path / "cases.db")
    with db.conn() as con:
        case_cols = {r[1] for r in con.execute("PRAGMA table_info(cases)")}
        event_cols = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    assert {"id", "created_at", "issue_text", "os", "triage_json", "outcome"} <= case_cols
    assert {"case_id", "event_type", "tool_name", "status", "source_ids"} <= event_cols


def test_save_case_and_read_back(tmp_path):
    db = database.DB(path=tmp_path / "cases.db")
    db.save_case(
        "case-1",
        "wifi drop",
        "macos",
        {"category": "Support general", "priority": "medium"},
        "needs_user_confirmation",
    )
    with db.conn() as con:
        row = con.execute("SELECT * FROM cases WHERE id = ?", ("case-1",)).fetchone()
    assert row["issue_text"] == "wifi drop"
    assert json.loads(row["triage_json"])["category"] == "Support general"


def test_log_event_and_index(tmp_path):
    db = database.DB(path=tmp_path / "cases.db")
    db.save_case("case-2", "issue", "windows", {}, "escalated")
    db.log_event(
        "case-2",
        "tool_call",
        "sim_get_dns_config",
        {"host": "example.com"},
        "ok",
        "SIMULATED: DNS present",
        ["ms-dns-client-ipconfig"],
        {},
    )
    with db.conn() as con:
        rows = con.execute(
            "SELECT * FROM events WHERE case_id = ? ORDER BY id", ("case-2",)
        ).fetchall()
        indexes = {r[1] for r in con.execute("PRAGMA index_list(events)")}
    assert len(rows) == 1
    assert json.loads(rows[0]["source_ids"]) == ["ms-dns-client-ipconfig"]
    assert "idx_events_case" in indexes


def test_events_are_redacted_at_persistence_boundary(tmp_path):
    db = database.DB(path=tmp_path / "cases.db")
    db.log_event(
        "c",
        "tool_call",
        "t",
        {"host": "a.b@example.com", "mac": "00:11:22:33:44:55"},
        "ok",
        "contact a.b@example.com from 00:11:22:33:44:55",
        [],
        {},
    )
    with db.conn() as con:
        row = con.execute("SELECT args_redacted, result_summary FROM events").fetchone()
    assert "a.b@example.com" not in row["args_redacted"]
    assert "00:11:22:33:44:55" not in row["args_redacted"]
    assert "a.b@example.com" not in row["result_summary"]


def test_event_summary_is_truncated(tmp_path):
    db = database.DB(path=tmp_path / "cases.db")
    db.log_event("c", "tool_call", "t", {}, "ok", "x" * 2000, [], {})
    with db.conn() as con:
        row = con.execute("SELECT result_summary FROM events").fetchone()
    assert len(row["result_summary"]) == 500


def test_db_is_idempotent_on_reopen(tmp_path):
    path = tmp_path / "cases.db"
    database.DB(path=path).save_case("c", "issue", "unknown", {}, None)
    db2 = database.DB(path=path)  # must not drop data
    with db2.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
