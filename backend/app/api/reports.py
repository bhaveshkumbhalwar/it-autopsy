"""Report export (PDF + JSON) and settings endpoints."""

from __future__ import annotations

import io
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import analysis_to_dict, execution_to_dict, rate_limit, require_auth, sanitize
from app.api.schemas import SettingsUpdate
from app.core.config import settings
from app.core.models import FAILURE_LABELS
from app.db.database import Analysis, Execution, Setting, get_session
from app.engine import llm
from app.ml.classifier import evaluate

router = APIRouter(tags=["reports"])

DEFAULTS = {
    "theme": "system",
    "retention_days": settings.retention_days,
    "default_use_llm": True,
    "redact_secrets": settings.redact_secrets,
}


@router.get("/reports/{analysis_id}.json")
def report_json(analysis_id: int, request: Request, db: Session = Depends(get_session), _: None = Depends(require_auth)):
    rate_limit(request)
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    execution = db.get(Execution, analysis.execution_pk)
    payload = {
        "report_type": "agent_autopsy",
        "generated_by": f"{settings.app_name} {settings.app_version}",
        "engine_version": analysis.engine_version,
        "generated_at": analysis.created_at.isoformat() if analysis.created_at else None,
        "agent": {
            "name": execution.agent_name if execution else None,
            "framework": execution.framework if execution else None,
            "source_format": execution.source_format if execution else None,
            "execution_id": execution.execution_id if execution else None,
            "duration_ms": execution.duration_ms if execution else None,
            "step_count": execution.step_count if execution else None,
        },
        "execution_summary": analysis.what_happened,
        "status": analysis.status,
        "failure_categories": [
            {"category": c["category"], "label": FAILURE_LABELS.get(c["category"], c["category"]), "confidence": c.get("confidence"), "title": c.get("title")}
            for c in analysis.categories or []
        ],
        "severity": analysis.severity,
        "failed_step_index": analysis.failed_step_index,
        "root_cause": analysis.root_cause,
        "evidence": analysis.evidence,
        "impact": analysis.impact,
        "recoverable": analysis.recoverable,
        "confidence": analysis.confidence,
        "recommendations": analysis.recommendations,
        "fixes": analysis.fixes or [],
        "prevention": analysis.prevention,
        "timeline": analysis.timeline,
        "llm_used": analysis.llm_used,
        "llm_model": analysis.llm_model,
        "notes": analysis.detection_notes,
    }
    return JSONResponse(
        content=sanitize(payload),
        headers={"Content-Disposition": f'attachment; filename="autopsy-{analysis_id}.json"'},
    )


@router.get("/reports/{analysis_id}.pdf")
def report_pdf(analysis_id: int, request: Request, db: Session = Depends(get_session), _: None = Depends(require_auth)):
    rate_limit(request)
    analysis = db.get(Analysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    execution = db.get(Execution, analysis.execution_pk)
    from app.report.pdf import build_report_pdf

    pdf_bytes = build_report_pdf(analysis, execution)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="autopsy-{analysis_id}.pdf"'},
    )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

@router.get("/settings")
def get_settings_endpoint(db: Session = Depends(get_session), _: None = Depends(require_auth)):
    stored = {row.key: row.value.get("value") for row in db.scalars(select(Setting)).all()}
    status = llm.config_status()
    return {
        "preferences": {**DEFAULTS, **stored},
        "ai": {
            **status,
            "env_var": "LLM_API_KEY",
            "note": "Set LLM_API_KEY (and optionally LLM_PROVIDER / LLM_MODEL / LLM_BASE_URL) in backend/.env. Keys are never stored in the database or returned by the API.",
        },
        "security": {
            "redaction_enabled": settings.redact_secrets,
            "api_token_required": bool(settings.api_token),
            "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
            "allowed_extensions": list(settings.allowed_extensions),
            "rate_limit": f"{settings.rate_limit_requests} req / {settings.rate_limit_window_seconds}s, {settings.rate_limit_analyze} analyses/min",
        },
        "retention": {"days": settings.retention_days},
        "app": {"name": settings.app_name, "version": settings.app_version, "environment": settings.environment},
    }


@router.put("/settings")
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_session), _: None = Depends(require_auth)):
    updates: dict[str, Any] = {k: v for k, v in payload.model_dump().items() if v is not None}
    for key, value in updates.items():
        row = db.get(Setting, key)
        if row:
            row.value = {"value": value}
        else:
            db.add(Setting(key=key, value={"value": value}))
    db.commit()
    return {"updated": updates}


@router.get("/llm/health")
def llm_health(request: Request, refresh: bool = False, _: None = Depends(require_auth)):
    """A real, lightweight call to the provider, cached for about five minutes.

    The UI badge reads this; "ready" therefore means the key actually works,
    not that a string is present in the environment.
    """
    rate_limit(request, limit=30)
    return llm.check_health(force=refresh)


@router.get("/ml/metrics")
def ml_metrics(request: Request, refresh: bool = False, _: None = Depends(require_auth)):
    rate_limit(request)
    from app.ml.classifier import load_or_train

    if refresh:
        load_or_train(force=True)
    return evaluate()


@router.post("/maintenance/purge")
def purge(request: Request, db: Session = Depends(get_session), _: None = Depends(require_auth)):
    """Delete executions older than the configured retention window."""
    rate_limit(request, limit=5)
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    rows = [r for r in db.scalars(select(Execution)).all() if r.created_at and r.created_at.replace(tzinfo=timezone.utc) < cutoff]
    for row in rows:
        db.delete(row)
    db.commit()
    return {"purged": len(rows), "retention_days": settings.retention_days}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@router.get("/integrations")
def integrations() -> dict[str, Any]:
    """Documented import workflows for frameworks that are not parsed natively."""
    return {
        "native": ["openai (Chat, Responses, Agents SDK)", "anthropic (Messages)", "langsmith runs", "otlp genai spans", "mcp json-rpc", "generic json/jsonl"],
        "documented": [
            {
                "framework": "LangChain",
                "workflow": "Use LangSmith tracing (LANGCHAIN_TRACING_V2=true) and export the run tree as JSON, or emit OpenTelemetry spans with the gen_ai semantic conventions. Both import directly.",
            },
            {
                "framework": "LangGraph",
                "workflow": "Enable LangSmith tracing on the graph; each node becomes a child run. Export the trace and upload the JSONL export.",
            },
            {
                "framework": "OpenAI Agents SDK",
                "workflow": "Export the agent trace (spans/events) with the SDK's trace processor, or send it to an OTLP collector and export JSON from there.",
            },
            {
                "framework": "OpenTelemetry",
                "workflow": "Point an OTLP HTTP exporter at a collector with a file exporter, then upload the resulting JSON. gen_ai.* attributes are mapped to steps.",
            },
            {
                "framework": "MCP",
                "workflow": "Log the JSON-RPC frames exchanged with the server (one JSON object per line) and upload the .jsonl file.",
            },
        ],
    }
