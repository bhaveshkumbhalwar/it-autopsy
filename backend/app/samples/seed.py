"""Restore the sample runs the app promises, idempotently.

Two problems this fixes:

* the recovered demo ("Coder Agent — 429 recovered by retry") was in the demo
  catalogue but not in the database, so the dashboard had no healthy run at all;
* there was no way to put a missing demo back without going through the UI.

Seeding is keyed on the demo's own ``execution_id``: a demo that is already
there is left exactly as it is (never re-analysed, never duplicated), a missing
one is ingested and analysed. Running it twice changes nothing.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import NormalizedTrace
from app.db.database import Execution
from app.ingest import ingest_text
from app.samples.demos import DEMOS, get_demo

logger = logging.getLogger("agent_autopsy.seed")

# Order matters for display only: the healthy example comes first so the demo
# cards lead with something that went right.
DEMO_ORDER = [
    "clean-success",
    "rate-limit-recovered",
    "partial-result",
    "tool-timeout",
    "reasoning-contradiction",
    "infinite-loop",
    "context-overflow",
]


def demo_ids() -> list[str]:
    known = [demo_id for demo_id in DEMO_ORDER if demo_id in DEMOS]
    return known + [demo_id for demo_id in DEMOS if demo_id not in known]


def _prepare(demo_id: str) -> NormalizedTrace:
    demo = get_demo(demo_id)
    trace, _detected, _redactions = ingest_text(demo["text"], agent_name=None, framework=demo["framework"])
    return trace


def existing_demo(db: Session, trace: NormalizedTrace) -> Execution | None:
    return db.scalars(
        select(Execution).where(Execution.execution_id == trace.execution_id).order_by(Execution.id).limit(1)
    ).first()


def seed_demos(db: Session, analyze=None) -> dict[str, list[str]]:
    """Create the demo runs that are missing.

    ``analyze(execution, trace)`` runs the diagnosis and must not commit; the
    caller owns the transaction. Without it the demo is stored unanalysed,
    which the dashboard counts as an execution but not as a healthy run.
    """
    created: list[str] = []
    present: list[str] = []
    for demo_id in demo_ids():
        trace = _prepare(demo_id)
        found = existing_demo(db, trace)
        if found is not None:
            present.append(demo_id)
            continue
        # Imported here to avoid a cycle: ingest imports the demo catalogue.
        from app.api.ingest import _persist

        execution = _persist(db, trace, is_demo=True, redactions=0)
        if analyze is not None:
            analyze(execution, trace)
        created.append(demo_id)
        logger.info("Seeded demo %s as execution %s", demo_id, execution.execution_id)
    if created:
        db.commit()
    return {"created": created, "present": present}


def ensure_demos_on_startup() -> dict[str, list[str]]:
    """Called once at startup: put back any demo run that is missing."""
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        from app.api.ingest import _run_analysis  # local import: avoids an import cycle

        def analyze(execution: Execution, trace: NormalizedTrace) -> None:
            _run_analysis(db, execution, trace, use_llm=False)

        return seed_demos(db, analyze=analyze)
    finally:
        db.close()
