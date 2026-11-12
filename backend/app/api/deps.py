"""Shared API dependencies: auth, rate limiting, serialization."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.redact import redact_obj, redact_secret
from app.db.database import Analysis, Execution


def sanitize(payload: Any) -> Any:
    """Last line of defence at the API boundary.

    Everything stored is redacted on write; this makes sure that nothing —
    an older row, a provider string, a trace echoed back in full — leaves the
    API carrying the configured key or a recognised credential shape.
    Applied to every response the routers build.
    """
    return redact_obj(payload, settings.llm_api_key)


def require_auth(request: Request) -> None:
    """Optional bearer-token protection. Off when API_TOKEN is unset."""
    if not settings.api_token:
        return
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else request.headers.get("x-api-token", "")
    if token != settings.api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API token.")


_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def rate_limit(request: Request, limit: int | None = None, window: int | None = None) -> None:
    limit = limit or settings.rate_limit_requests
    window = window or settings.rate_limit_window_seconds
    client = request.client.host if request.client else "anonymous"
    now = time.monotonic()
    bucket = _BUCKETS[client]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests per {window}s.",
            headers={"Retry-After": str(window)},
        )
    bucket.append(now)


def redact_analysis_on_write(analysis: Analysis) -> None:
    """Mask secrets in every stored field of an analysis, before commit.

    Redaction happens at write time *and* at the API boundary: this is the first
    half, so a row never rests in the database carrying a key even if a caller
    forgets to sanitise its response.
    """
    for field in ("root_cause", "what_happened", "impact", "llm_error", "llm_prompt", "llm_raw_response"):
        value = getattr(analysis, field, None)
        if isinstance(value, str):
            setattr(analysis, field, redact_secret(value, settings.llm_api_key)[0])
    for field in (
        "evidence",
        "categories",
        "recommendations",
        "prevention",
        "timeline",
        "detection_notes",
        "causal_chain",
        "fixes",
    ):
        value = getattr(analysis, field, None)
        if value:
            setattr(analysis, field, redact_obj(value, settings.llm_api_key))


def _primary_title(analysis: Analysis) -> str | None:
    """The detected category's short title, from the stored categories."""
    if analysis.primary_title:
        return analysis.primary_title
    for category in analysis.categories or []:
        if category.get("is_primary") and category.get("title"):
            return category["title"]
    return None


def analysis_to_dict(analysis: Analysis, include_heavy: bool = True) -> dict[str, Any]:
    data = {
        "id": analysis.id,
        "execution_pk": analysis.execution_pk,
        "engine_version": analysis.engine_version,
        "status": analysis.status,
        "severity": analysis.severity,
        "categories": analysis.categories,
        "primary_category": analysis.primary_category,
        "primary_title": _primary_title(analysis),
        "failed_step_index": analysis.failed_step_index,
        "root_cause": analysis.root_cause,
        "what_happened": analysis.what_happened,
        "impact": analysis.impact,
        "recoverable": analysis.recoverable,
        "confidence": analysis.confidence,
        "confidence_factors": analysis.confidence_factors or [],
        "severity_detail": analysis.severity_detail or {},
        "causal_chain": analysis.causal_chain or [],
        "llm_used": analysis.llm_used,
        "llm_model": analysis.llm_model,
        "llm_error": analysis.llm_error,
        "llm_prompt": analysis.llm_prompt,
        "llm_raw_response": analysis.llm_raw_response,
        "ml": {
            "prediction": analysis.ml_prediction,
            "confidence": analysis.ml_confidence,
            "top": analysis.ml_top or [],
        },
        "fixes": analysis.fixes or [],
        "detection_notes": analysis.detection_notes,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
    }
    if include_heavy:
        data.update(
            {
                "evidence": analysis.evidence,
                "recommendations": analysis.recommendations,
                "prevention": analysis.prevention,
                "timeline": analysis.timeline,
            }
        )
    return data


def execution_to_dict(
    execution: Execution,
    include_trace: bool = False,
    latest_analysis: Analysis | None = None,
    include_raw_trace: bool = False,
) -> dict[str, Any]:
    data = {
        "id": execution.id,
        "execution_id": execution.execution_id,
        "agent_name": execution.agent_name,
        "framework": execution.framework,
        "source_format": execution.source_format,
        "status": execution.status,
        "severity": execution.severity,
        "primary_category": execution.primary_category,
        "categories": execution.categories or [],
        "duration_ms": execution.duration_ms,
        "started_at": execution.started_at,
        "task_description": execution.task_description,
        "step_count": execution.step_count,
        "tool_call_count": execution.tool_call_count,
        "is_demo": execution.is_demo,
        "redactions": execution.redactions,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "analysis_count": len(execution.analyses) if execution.analyses is not None else 0,
        "content_hash": (execution.content_hash or "")[:16],
    }
    if latest_analysis is not None:
        data["latest_analysis"] = analysis_to_dict(latest_analysis, include_heavy=False)
    if include_trace:
        # The detail page renders the trace itself; send the parsed steps and
        # errors rather than the vendor payload, which is what carries secrets.
        trace = execution.raw_trace or {}
        data["trace"] = {k: v for k, v in trace.items() if k != "raw"} if isinstance(trace, dict) else {}
    if include_raw_trace:
        data["raw_trace"] = execution.raw_trace
    return data
