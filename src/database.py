"""SQLite persistence for cases and events.

Uses parameterized SQL. Schema created/migrated safely.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src import config, tools


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY,
  created_at TEXT DEFAULT (datetime('now')),
  issue_text TEXT,
  os TEXT,
  triage_json TEXT,
  outcome TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT,
  ts TEXT DEFAULT (datetime('now')),
  event_type TEXT,
  tool_name TEXT,
  args_redacted TEXT,
  status TEXT,
  result_summary TEXT,
  source_ids TEXT,
  approval_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id);
"""


class DB:
    def __init__(self, path: Path | None = None):
        self.path = path or config.get_db_path()
        config.ensure_directories()
        self.init_db()

    @contextmanager
    def conn(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def init_db(self):
        with self.conn() as con:
            con.executescript(SCHEMA)

    def save_case(self, case_id: str, issue_text: str, os: str, triage: dict, outcome: str | None):
        # The issue is stored for the local case history, but identifiers are
        # redacted at this boundary just like event arguments and summaries.
        safe_issue = tools.redact(issue_text or "")[0][:2000]
        with self.conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO cases(id, issue_text, os, triage_json, outcome) VALUES(?,?,?,?,?)",
                (case_id, safe_issue, os, json.dumps(triage), outcome),
            )

    def update_outcome(self, case_id: str, outcome: str) -> None:
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET outcome = ? WHERE id = ?", (outcome, case_id)
            )

    def list_cases(self, limit: int = 20) -> list:
        with self.conn() as con:
            return con.execute(
                "SELECT id, created_at, issue_text, os, outcome FROM cases "
                "ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()

    def list_events(self, case_id: str) -> list:
        with self.conn() as con:
            return con.execute(
                "SELECT ts, event_type, tool_name, status, result_summary "
                "FROM events WHERE case_id = ? ORDER BY id",
                (case_id,),
            ).fetchall()

    def log_event(self, case_id: str, event_type: str, tool_name: str | None, args: dict | None, status: str, summary: str, source_ids: list[str] | None, approval: dict | None):
        # Redact at the persistence boundary as defence in depth: call sites
        # cannot accidentally write identifiers or tokens to disk.
        safe_args = tools.redact_obj(args or {})
        safe_summary = tools.redact(summary or "")[0]
        with self.conn() as con:
            con.execute(
                "INSERT INTO events(case_id, event_type, tool_name, args_redacted, status, result_summary, source_ids, approval_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    case_id,
                    event_type,
                    tool_name,
                    json.dumps(safe_args),
                    status,
                    safe_summary[:500],
                    json.dumps(tools.redact_obj(source_ids or [])),
                    json.dumps({"approved": bool((approval or {}).get("approved", False))}),
                ),
            )
