"""Database layer: engine, session, and ORM models.

SQLite by default. The models avoid SQLite-only constructs so that switching
DATABASE_URL to PostgreSQL requires no code change.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Execution(Base):
    """One ingested agent execution (a trace)."""

    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(200), index=True)
    agent_name: Mapped[str] = mapped_column(String(200), index=True)
    framework: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_format: Mapped[str] = mapped_column(String(80), default="generic")
    status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    primary_category: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    task_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # A failed step that a later step of the same run made good ("429, then the
    # retry succeeded"). Kept so the detail page can say why a run is healthy.
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    # Set only when the agent's remediation was verified against a follow-up
    # trace: the status says "recovered", and this says why.
    resolution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # sha256 of the normalized trace: the same trace is one execution, not many.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    analysis_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan", order_by="Analysis.id.desc()"
    )


class Analysis(Base):
    """One diagnosis of an execution (rule + ML + optional LLM)."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_pk: Mapped[int] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    engine_version: Mapped[str] = mapped_column(String(20), default="1.0")
    status: Mapped[str] = mapped_column(String(20), default="failed")
    severity: Mapped[str] = mapped_column(String(20), default="LOW")
    categories: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    primary_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    failed_step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_happened: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    recoverable: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    prevention: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # How the LLM was asked to help, with the key masked.
    primary_title: Mapped[str | None] = mapped_column(String(240), nullable=True)
    fixes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    ml_prediction: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ml_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ml_top: Mapped[list[dict]] = mapped_column(JSON, default=list)
    llm_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    detection_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Engine 1.1+: explainable confidence, causal chain, severity arithmetic.
    confidence_factors: Mapped[list[dict]] = mapped_column(JSON, default=list)
    causal_chain: Mapped[list[dict]] = mapped_column(JSON, default=list)
    severity_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    execution: Mapped[Execution] = relationship(back_populates="analyses")


class Remediation(Base):
    """One turn of the agent loop: plan -> approval -> action -> verification.

    A session is every remediation row sharing an ``execution_pk``; ``attempt``
    numbers them, so the whole loop can be replayed in order and the attempt
    limit is enforced from the same table it is recorded in.
    """

    __tablename__ = "remediations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_pk: Mapped[int] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, index=True)

    # ---- what the agent decided
    decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # ---- the plan
    action_id: Mapped[str] = mapped_column(String(60), index=True)
    action_type: Mapped[str] = mapped_column(String(20), default="recommendation")
    risk: Mapped[str] = mapped_column(String(20), default="low")
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # ---- approval
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- action execution
    execution_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- verification
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification_status: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    verification_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The follow-up trace this was verified against, kept so the evidence the
    # verdict rests on can be inspected later.
    verification_execution_pk: Mapped[int | None] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    execution: Mapped["Execution"] = relationship(foreign_keys=[execution_pk])


class AgentEvent(Base):
    """The agent's activity log: one row per step of Observe/Analyze/Decide/Act/Verify/Learn."""

    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_pk: Mapped[int] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    remediation_id: Mapped[int | None] = mapped_column(
        ForeignKey("remediations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    phase: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite"):
        path = url.split("sqlite:///")[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False}, future=True)
    return create_engine(url, pool_pre_ping=True, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables and apply column + data migrations.

    Delegates to ``app.db.migrations`` so an existing database gains the newer
    columns and gets the one-time data fixes (duplicate merge, secret redaction).
    """
    from app.db.migrations import run_migrations

    run_migrations()


def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def json_default(value: Any) -> str:
    return json.dumps(value, default=str)
