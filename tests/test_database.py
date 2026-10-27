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


def test_dashboard_summary_and_conversation_round_trip(tmp_path):
    db = database.DB(path=tmp_path / "cases.db")
    db.save_case("case-1", "wifi drop", "macos", {"priority": "medium"}, "needs_user_confirmation")
    db.update_status("case-1", "awaiting_user")
    db.update_conversation("case-1", [{"role": "user", "content": "wifi drop"}])
    case = db.get_case("case-1")
    assert case is not None
    assert case["status"] == "awaiting_user"
    assert case["conversation"][0]["content"] == "wifi drop"

    summary = db.dashboard_summary()
    assert summary["total_cases"] == 1
    assert summary["status_counts"]["awaiting_user"] == 1
    assert summary["dashboard_summary" if False else "total_cases"] == 1
    assert db.dashboard_metrics() == db.get_dashboard_summary()


def test_dashboard_summary_counts_cases_and_tools(tmp_path):
    db = database.DB(path=tmp_path / "cases.db")
    db.save_case("c1", "wifi drop", "macos", {"priority": "high"}, "needs_user_confirmation", status="awaiting_user")
    db.log_event("c1", "tool_call", "sim_get_wifi_status", {}, "ok", "ok", [], {})
    db.log_event("c1", "tool_call", "sim_get_dns_config", {}, "failed", "boom", [], {})
    summary = db.dashboard_summary()
    assert summary["total_cases"] == 1
    assert summary["status_counts"]["awaiting_user"] == 1
    assert summary["tool_events"]["total"] == 2
    assert summary["tool_events"]["failed"] == 1
    assert 0.49 < summary["tool_events"]["failure_rate"] < 0.51


def test_legacy_database_migrates_without_crash(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE cases (
            id TEXT PRIMARY KEY,
            created_at TEXT DEFAULT (datetime('now')),
            issue_text TEXT,
            os TEXT,
            triage_json TEXT,
            outcome TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO cases (id, issue_text, os, triage_json, outcome) "
        "VALUES ('legacy', 'wifi drop', 'macos', '{\"category\": \"Support general\"}', 'needs_user_confirmation')"
    )
    con.commit()
    con.close()

    db = database.DB(path=path)
    case = db.get_case("legacy")
    assert case is not None
    assert case["issue_text"] == "wifi drop"
    assert case["status"] == "open"
    assert case["updated_at"] is not None
    assert case["conversation"] is None

    with db.conn() as con:
        columns = {row[1] for row in con.execute("PRAGMA table_info(cases)").fetchall()}
    assert {"status", "updated_at", "state_json", "diagnostics_json"} <= columns
