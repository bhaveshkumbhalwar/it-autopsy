"""SQLite persistence for cases and events.

Uses parameterized SQL. Schema created/migrated safely.
New columns in v2 are added via ALTER TABLE … ADD COLUMN IF NOT EXISTS so
existing databases continue to work without a manual migration.
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
  updated_at TEXT DEFAULT (datetime('now')),
  issue_text TEXT,
  os TEXT,
  triage_json TEXT,
  outcome TEXT,
  status TEXT DEFAULT 'open',
  symptoms_json TEXT,
  conversation_json TEXT,
  analysis_json TEXT,
  state_json TEXT,
  diagnostics_json TEXT,
  hypotheses_json TEXT,
  verification_json TEXT,
  next_step TEXT
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

# Columns added in v2. SQLite does not support ADD COLUMN IF NOT EXISTS in
# older versions, so we check existence manually.
_V2_COLUMNS: dict[str, tuple[str, str]] = {
    "cases": (
        ("updated_at", "TEXT DEFAULT (datetime('now'))"),
        ("status", "TEXT DEFAULT 'open'"),
        ("symptoms_json", "TEXT"),
        ("conversation_json", "TEXT"),
        ("analysis_json", "TEXT"),
    ),
}

_V3_COLUMNS: dict[str, tuple[str, str]] = {
    "cases": (
        ("state_json", "TEXT"),
        ("diagnostics_json", "TEXT"),
        ("hypotheses_json", "TEXT"),
        ("verification_json", "TEXT"),
        ("next_step", "TEXT"),
    ),
}

# Valid case status values
CASE_STATUSES = frozenset(
    {"open", "investigating", "awaiting_user", "resolved", "escalated"}
)


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
            self._migrate_v2(con)
            self._migrate_v3(con)

    def _migrate_v2(self, con: sqlite3.Connection) -> None:
        """Add v2 columns to cases if they don't already exist."""
        existing = {
            row[1]
            for row in con.execute("PRAGMA table_info(cases)").fetchall()
        }
        for col_name, col_def in _V2_COLUMNS.get("cases", ()):
            if col_name not in existing:
                if col_name == "updated_at":
                    # SQLite cannot add a column with a non-constant default such
                    # as datetime('now'). Add it as nullable, then backfill.
                    con.execute("ALTER TABLE cases ADD COLUMN updated_at TEXT")
                    con.execute(
                        "UPDATE cases SET updated_at = COALESCE(updated_at, created_at, datetime('now')) "
                        "WHERE updated_at IS NULL"
                    )
                else:
                    con.execute(f"ALTER TABLE cases ADD COLUMN {col_name} {col_def}")

    def _migrate_v3(self, con: sqlite3.Connection) -> None:
        existing = {
            row[1]
            for row in con.execute("PRAGMA table_info(cases)").fetchall()
        }
        for col_name, col_def in _V3_COLUMNS.get("cases", ()):
            if col_name not in existing:
                con.execute(f"ALTER TABLE cases ADD COLUMN {col_name} {col_def}")

    def save_case(
        self,
        case_id: str,
        issue_text: str,
        os: str,
        triage: dict,
        outcome: str | None,
        *,
        status: str = "open",
        analysis: dict | None = None,
        state: dict | None = None,
        symptoms: list[str] | None = None,
        diagnostics: list[dict] | None = None,
        hypotheses: list[dict] | None = None,
        verification: dict | None = None,
        next_step: str | None = None,
    ):
        # The issue is stored for the local case history, but identifiers are
        # redacted at this boundary just like event arguments and summaries.
        safe_issue = tools.redact(issue_text or "")[0][:2000]
        safe_status = status if status in CASE_STATUSES else "open"
        safe_analysis = tools.redact_obj(analysis or {})
        safe_symptoms = tools.redact_obj(symptoms or [])
        safe_state = tools.redact_obj(state or {})
        safe_diagnostics = tools.redact_obj(diagnostics or [])
        safe_hypotheses = tools.redact_obj(hypotheses or [])
        safe_verification = tools.redact_obj(verification or {})
        with self.conn() as con:
            con.execute(
                "INSERT INTO cases"
                "(id, issue_text, os, triage_json, outcome, status, analysis_json, "
                "symptoms_json, state_json, diagnostics_json, hypotheses_json, verification_json, next_step, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))"
                " ON CONFLICT(id) DO UPDATE SET"
                " issue_text=excluded.issue_text,"
                " os=excluded.os,"
                " triage_json=excluded.triage_json,"
                " outcome=excluded.outcome,"
                " status=excluded.status,"
                " analysis_json=excluded.analysis_json,"
                " symptoms_json=excluded.symptoms_json,"
                " state_json=excluded.state_json,"
                " diagnostics_json=excluded.diagnostics_json,"
                " hypotheses_json=excluded.hypotheses_json,"
                " verification_json=excluded.verification_json,"
                " next_step=excluded.next_step,"
                " updated_at=datetime('now')",
                (
                    case_id,
                    safe_issue,
                    os,
                    json.dumps(triage),
                    outcome,
                    safe_status,
                    json.dumps(safe_analysis),
                    json.dumps(safe_symptoms),
                    json.dumps(safe_state),
                    json.dumps(safe_diagnostics),
                    json.dumps(safe_hypotheses),
                    json.dumps(safe_verification),
                    (next_step or "")[:500],
                ),
            )

    def update_outcome(self, case_id: str, outcome: str) -> None:
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET outcome = ?, updated_at = datetime('now') WHERE id = ?",
                (outcome, case_id),
            )

    def update_status(self, case_id: str, status: str) -> None:
        """Update the case workflow status."""
        safe_status = status if status in CASE_STATUSES else "open"
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (safe_status, case_id),
            )

    def set_case_status(self, case_id: str, status: str) -> None:
        """Alias for update_status, used by case-management callers."""
        self.update_status(case_id, status)

    def update_symptoms(self, case_id: str, symptoms: list[str]) -> None:
        """Persist extracted symptoms for a case."""
        safe_symptoms = tools.redact_obj(symptoms or [])
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET symptoms_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(safe_symptoms), case_id),
            )

    def update_analysis(self, case_id: str, analysis: dict | None) -> None:
        """Persist an updated structured analysis for a case."""
        safe_analysis = tools.redact_obj(analysis or {})
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET analysis_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(safe_analysis), case_id),
            )

    def save_case_state(self, case_id: str, state: dict | None) -> None:
        """Persist a structured case state."""
        safe_state = tools.redact_obj(state or {})
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET state_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(safe_state), case_id),
            )

    def update_case_state(self, case_id: str, state: dict | None) -> None:
        self.save_case_state(case_id, state)

    def get_case_state(self, case_id: str) -> dict | None:
        case = self.get_case(case_id)
        return case.get("state") if case else None

    def record_diagnostic(
        self,
        case_id: str,
        diagnostic: dict,
        *,
        state: dict | None = None,
    ) -> None:
        """Append one diagnostic record to a case."""
        safe_diagnostic = tools.redact_obj(diagnostic or {})
        with self.conn() as con:
            row = con.execute(
                "SELECT diagnostics_json FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
            existing = []
            if row is not None:
                try:
                    existing = json.loads(row["diagnostics_json"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    existing = []
            existing.append(safe_diagnostic)
            con.execute(
                "UPDATE cases SET diagnostics_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(existing), case_id),
            )
        if state is not None:
            self.save_case_state(case_id, state)

    def get_diagnostics(self, case_id: str) -> list[dict]:
        case = self.get_case(case_id)
        if not case:
            return []
        diagnostics = case.get("diagnostics")
        return diagnostics if isinstance(diagnostics, list) else []

    def update_hypotheses(self, case_id: str, hypotheses: list[dict]) -> None:
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET hypotheses_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(tools.redact_obj(hypotheses or [])), case_id),
            )

    def update_verification(self, case_id: str, verification: dict | None) -> None:
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET verification_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(tools.redact_obj(verification or {})), case_id),
            )

    def update_next_step(self, case_id: str, next_step: str | None) -> None:
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET next_step = ?, updated_at = datetime('now') WHERE id = ?",
                ((next_step or "")[:500], case_id),
            )

    def update_conversation(self, case_id: str, messages: list[dict]) -> None:
        """Persist the conversation history (redacted) for a case."""
        safe_messages = tools.redact_obj(messages or [])
        with self.conn() as con:
            con.execute(
                "UPDATE cases SET conversation_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(safe_messages), case_id),
            )

    def get_case(self, case_id: str) -> dict | None:
        """Return the full case row as a dict, or None if not found."""
        with self.conn() as con:
            row = con.execute(
                "SELECT id, created_at, updated_at, issue_text, os, triage_json, "
                "outcome, status, symptoms_json, conversation_json, analysis_json, "
                "state_json, diagnostics_json, hypotheses_json, verification_json, next_step "
                "FROM cases WHERE id = ?",
                (case_id,),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        for field in ("triage_json", "symptoms_json", "conversation_json", "analysis_json",
                      "state_json", "diagnostics_json", "hypotheses_json", "verification_json"):
            raw = d.pop(field, None)
            key = field.replace("_json", "")
            try:
                d[key] = json.loads(raw) if raw else None
            except Exception:
                d[key] = None
        if "next_step" in d and d["next_step"] == "":
            d["next_step"] = None
        return d

    def _dashboard_summary(self) -> dict[str, object]:
        """Return operator-facing counts derived from stored cases and events."""
        with self.conn() as con:
            total = con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            status_rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM cases GROUP BY status"
            ).fetchall()
            outcome_rows = con.execute(
                "SELECT COALESCE(outcome, 'pending') AS outcome, COUNT(*) AS n "
                "FROM cases GROUP BY outcome"
            ).fetchall()
            priority_rows = con.execute(
                "SELECT triage_json FROM cases WHERE triage_json IS NOT NULL"
            ).fetchall()
            tool_total = con.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'tool_call'"
            ).fetchone()[0]
            tool_failed = con.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'tool_call' AND status != 'ok'"
            ).fetchone()[0]
            resolved = con.execute(
                "SELECT COUNT(*) FROM cases WHERE status = 'resolved' "
                "OR outcome IN ('verified_resolved')"
            ).fetchone()[0]
            avg_resolution = con.execute(
                "SELECT AVG((julianday(updated_at) - julianday(created_at)) * 86400.0) "
                "FROM cases WHERE status = 'resolved' OR outcome IN ('verified_resolved')"
            ).fetchone()[0]
            repeated = con.execute(
                "SELECT COALESCE(SUM(c - 1), 0) FROM ("
                "SELECT issue_text, COUNT(*) AS c FROM cases GROUP BY issue_text HAVING c > 1"
                ")"
            ).fetchone()[0]

        status_counts = {status: 0 for status in CASE_STATUSES}
        for row in status_rows:
            status = row["status"] if row["status"] in CASE_STATUSES else "open"
            status_counts[status] += int(row["n"])

        outcome_counts = {str(row["outcome"]): int(row["n"]) for row in outcome_rows}

        priority_counts: dict[str, int] = {}
        for row in priority_rows:
            try:
                triage = json.loads(row["triage_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                triage = {}
            priority = str(triage.get("priority") or "unknown")
            priority_counts[priority] = priority_counts.get(priority, 0) + 1

        return {
            "total_cases": int(total),
            "status_counts": status_counts,
            "outcome_counts": outcome_counts,
            "priority_counts": priority_counts,
            "tool_events": {
                "total": int(tool_total),
                "failed": int(tool_failed),
                "failure_rate": (int(tool_failed) / int(tool_total)) if tool_total else 0.0,
            },
            "resolved_cases": int(resolved),
            "average_resolution_seconds": (float(avg_resolution) if avg_resolution is not None else None),
            "repeated_issue_cases": int(repeated),
        }

    def dashboard_summary(self) -> dict[str, object]:
        return self._dashboard_summary()

    def dashboard_metrics(self) -> dict[str, object]:
        return self._dashboard_summary()

    def get_dashboard_summary(self) -> dict[str, object]:
        return self._dashboard_summary()

    def list_cases(self, limit: int = 20) -> list:
        with self.conn() as con:
            return con.execute(
                "SELECT id, created_at, updated_at, issue_text, os, outcome, status "
                "FROM cases "
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

    def recent_events(self, limit: int = 25) -> list:
        """Return the most recent events across all cases."""
        with self.conn() as con:
            return con.execute(
                "SELECT case_id, ts, event_type, tool_name, status, result_summary "
                "FROM events ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()

    def search_cases(
        self,
        query: str = "",
        status: str | None = None,
        priority: str | None = None,
        limit: int = 100,
    ) -> list:
        """Filter cases by text, status and triage priority."""
        wanted = (query or "").strip().lower()
        rows = self.list_cases(limit=1000)
        result = []
        for row in rows:
            if status and (row["status"] or "open") != status:
                continue
            case = self.get_case(row["id"])
            triage = case.get("triage") if case else None
            case_priority = (triage or {}).get("priority", "")
            if priority and case_priority != priority:
                continue
            haystack = " ".join(
                str(value or "")
                for value in (row["id"], row["issue_text"], row["os"], row["outcome"], row["status"])
            ).lower()
            if wanted and wanted not in haystack:
                continue
            result.append(row)
            if len(result) >= limit:
                break
        return result

    def cases_for_source(self, doc_id: str, limit: int = 20) -> list[str]:
        """Return case IDs whose recorded events cite a knowledge-base document."""
        wanted = str(doc_id or "").strip()
        if not wanted:
            return []
        matches: list[str] = []
        with self.conn() as con:
            rows = con.execute(
                "SELECT case_id, source_ids FROM events WHERE source_ids IS NOT NULL"
            ).fetchall()
        for row in rows:
            try:
                source_ids = json.loads(row["source_ids"] or "[]")
            except (json.JSONDecodeError, TypeError):
                source_ids = []
            if wanted in source_ids and row["case_id"] not in matches:
                matches.append(row["case_id"])
            if len(matches) >= limit:
                break
        return matches

    def case_volume_by_date(self, limit: int = 30) -> list[dict[str, object]]:
        """Return case counts grouped by creation date for charts."""
        with self.conn() as con:
            rows = con.execute(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n "
                "FROM cases GROUP BY day ORDER BY day DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [{"day": row["day"], "cases": int(row["n"])} for row in reversed(rows)]

    def log_event(
        self,
        case_id: str,
        event_type: str,
        tool_name: str | None,
        args: dict | None,
        status: str,
        summary: str,
        source_ids: list[str] | None,
        approval: dict | None,
    ):
        # Redact at the persistence boundary as defence in depth: call sites
        # cannot accidentally write identifiers or tokens to disk.
        safe_args = tools.redact_obj(args or {})
        safe_summary = tools.redact(summary or "")[0]
        with self.conn() as con:
            con.execute(
                "INSERT INTO events(case_id, event_type, tool_name, args_redacted, "
                "status, result_summary, source_ids, approval_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
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
