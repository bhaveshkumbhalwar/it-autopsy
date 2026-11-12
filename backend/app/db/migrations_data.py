"""One-time data migrations: duplicate merge and stored-secret redaction.

Both are idempotent — running them on a clean database changes nothing, which
means they can safely run at every startup.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redact import redact_obj, redact_secret
from app.db import database as db_module

logger = logging.getLogger("agent_autopsy.migrations")

Execution = db_module.Execution
Analysis = db_module.Analysis


def _execution_signature(execution: Execution) -> str:
    """Group key for executions that predate the content_hash column.

    The stored normalized trace is the most precise identity; older rows that
    stored only summary fields fall back to (execution_id, agent, step count).
    """
    from app.ingest.util import _HASH_EXCLUDED, stable_hash

    trace = execution.raw_trace or {}
    if isinstance(trace, dict) and trace.get("steps"):
        # Hash the stored trace itself rather than trusting the column: it was
        # added later, so a duplicate pair can have it on one side only. Same
        # exclusions as ingest, so old and new rows agree.
        return stable_hash({k: v for k, v in trace.items() if k not in _HASH_EXCLUDED})
    if execution.content_hash:
        return execution.content_hash
    return f"legacy:{execution.execution_id}:{execution.agent_name}:{execution.step_count}"


def merge_duplicate_executions() -> dict[str, object]:
    """Collapse executions that hold the same trace into one row.

    The oldest row is kept (its id is the one already linked from the UI). Its
    analyses are kept; a newer duplicate's analyses are kept only when the kept
    row has none, then the duplicate is deleted. The number of rows removed is
    logged and returned so the caller can report it.
    """
    db = db_module.SessionLocal()
    try:
        rows = list(db.scalars(select(db_module.Execution).order_by(Execution.id)).all())
        groups: dict[str, list[Execution]] = defaultdict(list)
        for row in rows:
            if row.raw_trace:
                groups[_execution_signature(row)].append(row)

        merged: list[dict[str, object]] = []
        for signature, group in groups.items():
            if len(group) < 2:
                continue
            keeper, duplicates = group[0], group[1:]
            before_hash = keeper.content_hash
            if not keeper.content_hash:
                keeper.content_hash = signature
            keeper.analysis_count = max(
                keeper.analysis_count or 0, len(db.scalars(select(db_module.Analysis).where(db_module.Analysis.execution_pk == keeper.id)).all())
            )
            removed_ids: list[int] = []
            for duplicate in duplicates:
                duplicate_analyses = db.scalars(select(db_module.Analysis).where(db_module.Analysis.execution_pk == duplicate.id)).all()
                has_keeper_analyses = bool(
                    db.scalars(select(db_module.Analysis.id).where(db_module.Analysis.execution_pk == keeper.id).limit(1)).first()
                )
                if duplicate_analyses and not has_keeper_analyses:
                    # Keep the analysis the user can see: re-point the newest one.
                    newest = max(duplicate_analyses, key=lambda a: a.id)
                    newest.execution_pk = keeper.id
                removed_ids.append(duplicate.id)
                db.delete(duplicate)
            entry = {
                "execution_id": keeper.execution_id,
                "kept_pk": keeper.id,
                "removed_pks": removed_ids,
                "hash_assigned": before_hash != keeper.content_hash,
            }
            merged.append(entry)
            logger.warning(
                "Merged %s duplicate execution(s) of '%s' into pk=%s (removed pk=%s)",
                len(removed_ids),
                keeper.execution_id,
                keeper.id,
                removed_ids,
            )

        # Backfill the hash on singletons so future submissions match immediately.
        for row in rows:
            if row.raw_trace and not row.content_hash:
                row.content_hash = _execution_signature(row)

        db.commit()
        if merged:
            logger.warning(
                "Duplicate merge complete: %s group(s), %s row(s) removed",
                len(merged),
                sum(len(m["removed_pks"]) for m in merged),  # type: ignore[arg-type]
            )
        return {"groups": merged, "rows_removed": sum(len(m["removed_pks"]) for m in merged)}  # type: ignore[arg-type]
    finally:
        db.close()


_TEXT_FIELDS_ANALYSIS = ("llm_error", "llm_prompt", "llm_raw_response", "root_cause", "what_happened", "impact")
_JSON_FIELDS_ANALYSIS = ("evidence", "recommendations", "prevention", "timeline", "detection_notes", "causal_chain")
_TEXT_FIELDS_EXECUTION = ("task_description", "expected_output", "output")


def redact_stored_secrets() -> int:
    """Re-apply the redactor to rows written before it covered every field.

    A key fragment has been observed in ``llm_error`` (banner) and in
    ``detection_notes`` (''How this was diagnosed''). The configured key is
    masked exactly, and known key shapes are masked by pattern.
    """
    secret = settings.llm_api_key
    changed = 0
    db = db_module.SessionLocal()
    try:
        for analysis in db.scalars(select(db_module.Analysis)).all():
            dirty = False
            for field in _TEXT_FIELDS_ANALYSIS:
                value = getattr(analysis, field, None)
                if isinstance(value, str):
                    cleaned, count = redact_secret(value, secret)
                    if count:
                        setattr(analysis, field, cleaned)
                        dirty = True
            for field in _JSON_FIELDS_ANALYSIS:
                value = getattr(analysis, field, None)
                if value:
                    cleaned = redact_obj(value, secret)
                    if cleaned != value:
                        setattr(analysis, field, cleaned)
                        dirty = True
            if dirty:
                changed += 1

        for execution in db.scalars(select(db_module.Execution)).all():
            dirty = False
            for field in _TEXT_FIELDS_EXECUTION:
                value = getattr(execution, field, None)
                if isinstance(value, str):
                    cleaned, count = redact_secret(value, secret)
                    if count:
                        setattr(execution, field, cleaned)
                        dirty = True
            if execution.raw_trace:
                cleaned = redact_obj(execution.raw_trace, secret)
                if cleaned != execution.raw_trace:
                    execution.raw_trace = cleaned
                    dirty = True
            if dirty:
                changed += 1

        db.commit()
        if changed:
            logger.warning("Redacted stored secrets in %s row(s)", changed)
        return changed
    finally:
        db.close()
