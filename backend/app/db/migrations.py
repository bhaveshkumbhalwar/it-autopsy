"""Safe database migrations that run at startup.

SQLAlchemy's ``create_all`` creates missing tables but never alters existing
ones. This module adds the columns later versions need, idempotently, and then
applies the one-time data migrations (duplicate merge, stored-secret redaction).

Every step is guarded so that starting the app on a database that is already up
to date does nothing, and a failure is logged instead of preventing startup.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

import json

from sqlalchemy import func, inspect, select, text

from app.core.config import settings
from app.db import database as db_module

logger = logging.getLogger("agent_autopsy.migrations")

# table -> column -> DDL fragment used when the row-level default is needed too.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "executions": {
        "content_hash": "VARCHAR(64)",
        "analysis_count": "INTEGER NOT NULL DEFAULT 0",
    },
    "analyses": {
        "confidence_factors": "JSON NOT NULL DEFAULT '[]'",
        "causal_chain": "JSON NOT NULL DEFAULT '[]'",
        "severity_detail": "JSON NOT NULL DEFAULT '{}'",
        "primary_title": "VARCHAR(240)",
        "fixes": "JSON NOT NULL DEFAULT '[]'",
        "ml_prediction": "VARCHAR(60)",
        "ml_confidence": "FLOAT",
        "ml_top": "JSON NOT NULL DEFAULT '[]'",
        "llm_prompt": "TEXT",
        "llm_raw_response": "TEXT",
    },
}


def _sqlite_path() -> Path | None:
    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    raw = url.split("sqlite:///")[-1]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def backup_database(reason: str) -> Path | None:
    """Write a timestamped copy of the SQLite file next to it.

    Called before anything that rewrites stored rows so a bad migration is
    always recoverable by copying the file back.
    """
    path = _sqlite_path()
    if path is None or not path.exists():
        logger.info("No SQLite file to back up (%s)", settings.database_url.split("///")[-1])
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.backup-{stamp}-{reason}{path.suffix}")
    shutil.copy2(path, target)
    logger.info("Database backed up to %s", target)
    return target


def logical_backup(reason: list[str]) -> dict[str, object] | None:
    """Row-level JSON snapshot, taken before data migrations rewrite anything.

    The physical file copy is not enough on its own: it must be requested before
    the database is opened, so we also keep a portable dump of the rows that the
    migrations touch. Returns None when there is nothing to snapshot.
    """
    if not reason and not _needs_backup():
        return None
    if not reason and not _needs_data_migration():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sqlite_path = _sqlite_path()
    # Next to the database file, and never inside a temp directory: a backup
    # that vanishes with the process is not a backup.
    target_dir = sqlite_path.parent / "backups" if sqlite_path else None
    snapshot: dict[str, list[dict]] = {}
    Analysis = db_module.Analysis
    Execution = db_module.Execution
    Setting = db_module.Setting

    with db_module.SessionLocal() as db:
        snapshot["executions"] = [_row_to_dict(r) for r in db.scalars(select(Execution)).all()]
        snapshot["analyses"] = [_row_to_dict(r) for r in db.scalars(select(Analysis)).all()]
        snapshot["settings"] = [_row_to_dict(r) for r in db.scalars(select(Setting)).all()]
    if not snapshot["executions"] and not snapshot["analyses"]:
        return None
    if target_dir is None:
        return {"skipped": "not a file-backed SQLite database"}
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"rows-{stamp}.json"
    path.write_text(json.dumps(snapshot, default=str), encoding="utf-8")
    logger.warning("Row-level backup written to %s (%s executions, %s analyses)", path, len(snapshot["executions"]), len(snapshot["analyses"]))
    return {"path": str(path), "executions": len(snapshot["executions"]), "analyses": len(snapshot["analyses"])}


def _needs_data_migration() -> bool:
    """True when the stored rows still contain a duplicate or an unredacted secret."""
    from app.core.redact import redact_obj, redact_secret
    from app.db.database import Analysis, Execution

    secret = settings.llm_api_key
    with db_module.SessionLocal() as db:
        seen: set[str] = set()
        for row in db.scalars(select(Execution)).all():
            if not row.raw_trace:
                continue
            from app.db.migrations_data import _execution_signature  # local import avoids a cycle

            signature = _execution_signature(row)
            if signature in seen:
                return True
            seen.add(signature)
            if redact_obj(row.raw_trace, secret) != row.raw_trace:
                return True
        for analysis in db.scalars(select(Analysis)).all():
            for field in ("llm_error", "llm_prompt", "llm_raw_response", "root_cause", "what_happened", "impact"):
                value = getattr(analysis, field)
                if isinstance(value, str) and redact_secret(value, secret)[0] != value:
                    return True
    return False


def _needs_backup() -> bool:
    """True when a data migration could change something."""
    Execution = db_module.Execution

    with db_module.SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Execution)) > 0


def _row_to_dict(row) -> dict:
    return {c.key: getattr(row, c.key) for c in inspect(row).mapper.column_attrs}


def _add_missing_columns() -> list[str]:
    """Add columns introduced after the database was first created."""
    added: list[str] = []
    inspector = inspect(db_module.engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {col["name"] for col in inspector.get_columns(table)}
        for column, ddl in columns.items():
            if column in present:
                continue
            with db_module.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            added.append(f"{table}.{column}")
            logger.info("Added column %s.%s", table, column)
    return added


def run_migrations() -> dict[str, object]:
    """Idempotent: creates tables, adds columns, then runs data migrations."""
    db_module.Base.metadata.create_all(db_module.engine)
    added = _add_missing_columns()
    # A row-level snapshot is only taken when a data migration can actually
    # change something (duplicates to merge, unreplayed secret redaction).
    backup: dict[str, object] = {"columns_added": added, "rows": logical_backup(added)}
    if backup["rows"]:
        # A file copy too: it restores the database in one `cp`, without replaying anything.
        copy = backup_database("pre-migration")
        backup["file"] = str(copy) if copy else None
        if copy:
            logger.warning("Database file backed up to %s", copy)
    report: dict[str, object] = {"columns_added": added, "backup": backup}

    # Imported here so the models are registered before any data work.
    from app.db.migrations_data import merge_duplicate_executions, redact_stored_secrets

    report["secrets_redacted"] = redact_stored_secrets()
    report["duplicates"] = merge_duplicate_executions()
    return report
