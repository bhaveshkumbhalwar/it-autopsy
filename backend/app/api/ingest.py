"""Ingestion + analysis endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    analysis_to_dict,
    execution_to_dict,
    rate_limit,
    redact_analysis_on_write,
    require_auth,
    sanitize,
)
from app.api.schemas import DemoIngestRequest, PasteRequest
from app.core.config import settings
from app.core.models import NormalizedTrace
from app.core.redact import redact_obj, redact_secret
from app.db.database import Analysis, Execution, get_session
from app.engine.analyzer import diagnose, recoveries
from app.ingest import ParseError, ingest_bytes, ingest_text, safe_filename
from app.samples.demos import get_demo, list_demos

router = APIRouter(tags=["ingest"])


def _find_duplicate(db: Session, trace: NormalizedTrace) -> Execution | None:
    """The existing execution with the same content hash, if any.

    The hash is computed from the normalized trace, so the same run pasted,
    uploaded or loaded as a demo resolves to one record instead of a new one.
    """
    if not trace.content_hash:
        return None
    return db.scalars(
        select(Execution).where(Execution.content_hash == trace.content_hash).order_by(Execution.id)
    ).first()


def _persist(db: Session, trace: NormalizedTrace, *, is_demo: bool, redactions: int) -> Execution:
    execution = Execution(
        execution_id=trace.execution_id,
        agent_name=trace.agent_name,
        framework=trace.framework,
        source_format=trace.source_format,
        status=trace.status.value,
        duration_ms=trace.duration_ms,
        started_at=trace.started_at,
        task_description=(trace.task_description or "")[:8000] or None,
        expected_output=(trace.expected_output or "")[:8000] or None,
        output=str(trace.output)[:20000] if trace.output is not None else None,
        step_count=len(trace.steps),
        tool_call_count=len(trace.tool_calls),
        is_demo=is_demo,
        redactions=redactions,
        content_hash=trace.content_hash,
        raw_trace=trace.to_dict(),
    )
    db.add(execution)
    db.flush()
    return execution


def _refresh_duplicate(db: Session, existing: Execution, trace: NormalizedTrace, *, is_demo: bool, redactions: int) -> Execution:
    """Update the stored record in place when the same trace is submitted again.

    The analyses history is preserved (a re-analysis is a new Analysis row, not a
    new Execution), so the dashboard does not count the same run twice.
    """
    existing.agent_name = trace.agent_name
    existing.framework = trace.framework or existing.framework
    existing.source_format = trace.source_format
    existing.started_at = trace.started_at or existing.started_at
    existing.duration_ms = trace.duration_ms if trace.duration_ms is not None else existing.duration_ms
    existing.task_description = (trace.task_description or existing.task_description or "")[:8000] or None
    existing.expected_output = (trace.expected_output or existing.expected_output or "")[:8000] or None
    existing.output = str(trace.output)[:20000] if trace.output is not None else existing.output
    existing.step_count = len(trace.steps)
    existing.tool_call_count = len(trace.tool_calls)
    existing.redactions = max(existing.redactions or 0, redactions)
    existing.is_demo = existing.is_demo or is_demo
    existing.raw_trace = trace.to_dict()
    db.flush()
    return existing


def _run_analysis(db: Session, execution: Execution, trace: NormalizedTrace, use_llm: bool) -> tuple[Analysis, dict]:
    result = diagnose(trace, use_llm=use_llm, capture_llm=True)
    analysis = Analysis(
        execution_pk=execution.id,
        engine_version=result["engine_version"],
        status=result["status"],
        severity=result["severity"],
        categories=result["categories"],
        primary_category=result["primary_category"],
        failed_step_index=result["failed_step_index"],
        root_cause=result["root_cause"],
        what_happened=result["what_happened"],
        impact=result["impact"],
        recoverable=result["recoverable"],
        confidence=result["confidence"],
        evidence=result["evidence"],
        recommendations=result["recommendations"],
        prevention=result["prevention"],
        timeline=result["timeline"],
        llm_used=result["llm_used"],
        llm_model=result["llm_model"],
        llm_error=result["llm_error"],
        primary_title=result.get("primary_title"),
        fixes=result.get("fixes", []),
        ml_prediction=result["ml"]["prediction"],
        ml_confidence=result["ml"]["confidence"],
        ml_top=result["ml"]["top"],
        llm_prompt=result.get("llm_prompt"),
        llm_raw_response=result.get("llm_raw_response"),
        detection_notes=result["detection_notes"] + [result["confidence_note"]],
        confidence_factors=result.get("confidence_factors", []),
        causal_chain=result.get("causal_chain", []),
        severity_detail=result.get("severity_detail", {}),
    )
    # Redact at write time: nothing derived from a provider or the trace is
    # stored unredacted, even if a future code path forgets to clean its input.
    redact_analysis_on_write(analysis)

    db.add(analysis)
    execution.status = result["status"]
    execution.severity = result["severity"]
    execution.primary_category = result["primary_category"]
    execution.categories = [c["category"] for c in result["categories"]]
    # A failed step that a later step made good: the detail page reports it as
    # "recovered after N retries" rather than as a cause.
    execution.retry_count = len(recoveries(trace))
    db.flush()
    execution.analysis_count = len(db.scalars(select(Analysis.id).where(Analysis.execution_pk == execution.id)).all())

    payload = analysis_to_dict(analysis)
    payload["ml"] = result["ml"]
    payload["severity_detail"] = result["severity_detail"]
    payload["confidence_factors"] = result.get("confidence_factors", [])
    payload["causal_chain"] = result.get("causal_chain", [])
    payload["primary_label"] = result["primary_label"]
    payload["failed_step"] = result["failed_step"]
    payload["confidence_note"] = result["confidence_note"]
    return analysis, sanitize(payload)


@router.get("/formats")
def formats() -> dict[str, object]:
    from app.ingest.detector import FORMAT_LABELS

    return {
        "formats": [{"id": k, "label": v} for k, v in FORMAT_LABELS.items()],
        "allowed_extensions": list(settings.allowed_extensions),
        "max_upload_bytes": settings.max_upload_bytes,
        "redaction_enabled": settings.redact_secrets,
    }


@router.get("/demos")
def demos() -> dict[str, object]:
    return {"demos": list_demos()}


@router.post("/ingest/paste", response_model=None)
def ingest_paste(
    payload: PasteRequest,
    request: Request,
    db: Session = Depends(get_session),
    _: None = Depends(require_auth),
):
    rate_limit(request, settings.rate_limit_analyze)
    try:
        trace, detected, redactions = ingest_text(
            payload.text,
            agent_name=payload.agent_name,
            framework=payload.framework,
            task_description=payload.task_description,
            expected_output=payload.expected_output,
        )
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.to_dict()) from exc

    if not payload.save:
        result = diagnose(trace, use_llm=payload.use_llm, capture_llm=True)
        return sanitize(
            {
                "execution": {"execution_id": trace.execution_id, "agent_name": trace.agent_name, "is_demo": False},
                "analysis": result,
                "format": detected,
                "warnings": trace.warnings,
                "redactions": redactions,
            }
        )

    existing = _find_duplicate(db, trace)
    execution = (
        _refresh_duplicate(db, existing, trace, is_demo=False, redactions=redactions)
        if existing
        else _persist(db, trace, is_demo=False, redactions=redactions)
    )
    _analysis, payload_out = _run_analysis(db, execution, trace, payload.use_llm)
    db.commit()
    db.refresh(execution)
    return sanitize(
        {
            "execution": execution_to_dict(execution, include_trace=True),
            "analysis": payload_out,
            "format": detected,
            "warnings": trace.warnings,
            "redactions": redactions,
        }
    )


@router.post("/ingest/upload", response_model=None)
async def ingest_upload(
    request: Request,
    file: UploadFile = File(...),
    agent_name: str | None = Form(None),
    framework: str | None = Form(None),
    task_description: str | None = Form(None),
    expected_output: str | None = Form(None),
    use_llm: bool = Form(True),
    db: Session = Depends(get_session),
    _: None = Depends(require_auth),
):
    rate_limit(request, settings.rate_limit_analyze)
    data = await file.read()
    # Only the name is kept, never the client's path: an upload arrives as
    # whatever the browser sends, and a full local path in an agent name would
    # end up in the stored row and the exported report.
    safe_name = safe_filename(file.filename)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"message": f"File exceeds the {settings.max_upload_bytes // (1024*1024)} MB limit.", "hint": "Split the export."},
        )
    try:
        trace, detected, redactions = ingest_bytes(
            data,
            filename=safe_name,
            agent_name=agent_name,
            framework=framework,
            task_description=task_description,
            expected_output=expected_output,
        )
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.to_dict()) from exc

    existing = _find_duplicate(db, trace)
    execution = (
        _refresh_duplicate(db, existing, trace, is_demo=False, redactions=redactions)
        if existing
        else _persist(db, trace, is_demo=False, redactions=redactions)
    )
    _analysis, payload_out = _run_analysis(db, execution, trace, use_llm)
    db.commit()
    db.refresh(execution)
    return sanitize(
        {
            "execution": execution_to_dict(execution, include_trace=True),
            "analysis": payload_out,
            "format": detected,
            "warnings": trace.warnings,
            "redactions": redactions,
        }
    )


@router.post("/ingest/demo", response_model=None)
def ingest_demo(
    payload: DemoIngestRequest,
    request: Request,
    db: Session = Depends(get_session),
    _: None = Depends(require_auth),
):
    rate_limit(request, settings.rate_limit_analyze)
    try:
        demo = get_demo(payload.demo_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown demo '{payload.demo_id}'.") from exc
    try:
        trace, detected, redactions = ingest_text(demo["text"], agent_name=None, framework=demo["framework"])
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    existing = _find_duplicate(db, trace)
    execution = (
        _refresh_duplicate(db, existing, trace, is_demo=True, redactions=redactions)
        if existing
        else _persist(db, trace, is_demo=True, redactions=redactions)
    )
    _analysis, payload_out = _run_analysis(db, execution, trace, payload.use_llm)
    db.commit()
    db.refresh(execution)
    return sanitize(
        {
            "execution": execution_to_dict(execution, include_trace=True),
            "analysis": payload_out,
            "format": detected,
            "warnings": trace.warnings,
            "redactions": redactions,
        }
    )
