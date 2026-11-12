"""Log ingestion entry point: validate, detect format, normalize, redact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.models import NormalizedTrace
from app.core.redact import redact_obj, redact_text
from app.ingest import anthropic as anthropic_adapter
from app.ingest import generic as generic_adapter
from app.ingest import langsmith as langsmith_adapter
from app.ingest import mcp as mcp_adapter
from app.ingest import openai as openai_adapter
from app.ingest import otlp as otlp_adapter
from app.ingest.detector import FORMAT_LABELS, detect_format
from app.ingest.util import ParseError, load_json_or_jsonl, trace_content_hash

ADAPTERS = {
    "openai": openai_adapter.parse_openai,
    "anthropic": anthropic_adapter.parse_anthropic,
    "langsmith": langsmith_adapter.parse_langsmith,
    "otlp": otlp_adapter.parse_otlp,
    "mcp": mcp_adapter.parse_mcp,
    "generic": generic_adapter.parse_generic,
}

MAX_TEXT_CHARS = 2_000_000


def validate_filename(filename: str | None) -> None:
    if not filename:
        return
    suffix = Path(filename).suffix.lower()
    if suffix and suffix not in settings.allowed_extensions:
        raise ParseError(
            f"Unsupported file type '{suffix}'.",
            hint="Allowed: " + ", ".join(settings.allowed_extensions),
        )


def validate_size(size: int) -> None:
    if size > settings.max_upload_bytes:
        raise ParseError(
            f"File is too large ({size / 1024 / 1024:.1f} MB).",
            hint=f"The limit is {settings.max_upload_bytes / 1024 / 1024:.0f} MB.",
        )


def ingest_text(
    text: str,
    *,
    agent_name: str | None = None,
    framework: str | None = None,
    task_description: str | None = None,
    expected_output: str | None = None,
    filename: str | None = None,
) -> tuple[NormalizedTrace, str, int]:
    """Parse and normalize a trace.

    Returns (trace, detected_format, redaction_count).
    Raises ParseError with a helpful message for malformed input.
    """
    validate_filename(filename)
    if len(text) > MAX_TEXT_CHARS:
        raise ParseError(
            f"Input is too large ({len(text) / 1024 / 1024:.1f} MB of text).",
            hint="Upload a smaller trace or split the export.",
        )
    if not text.strip():
        raise ParseError("Nothing to analyse: the input is empty.")

    records, kind = load_json_or_jsonl(text)
    if not records:
        raise ParseError("The input contained no records.")

    detected = detect_format(records)
    hints = {
        "agent_name": agent_name,
        "framework": framework,
        "task_description": task_description,
        "expected_output": expected_output,
    }
    adapter = ADAPTERS.get(detected, generic_adapter.parse_generic)
    try:
        trace = adapter(records, hints)
    except ParseError:
        raise
    except Exception as exc:  # adapter bug or truly alien input
        if detected != "generic":
            trace = generic_adapter.parse_generic(records, hints)
            trace.warnings.append(
                f"The {FORMAT_LABELS.get(detected, detected)} parser could not read this file "
                f"({type(exc).__name__}); it was read with the generic parser instead."
            )
            detected = "generic"
        else:
            raise ParseError(f"Could not interpret this trace: {exc}") from exc

    if not trace.steps:
        trace.warnings.append("No execution steps were found; only summary fields were extracted.")

    redactions = 0
    if settings.redact_secrets:
        redacted = redact_obj(trace.to_dict())
        redactions = sum(1 for line in _walk_strings(redacted) if "[REDACTED]" in line)
        trace = _trace_from_dict(redacted)
    trace.source_format = detected
    if kind == "jsonl":
        trace.metadata.setdefault("line_delimited", True)
    # Hash the trace *content* (not the upload text), so the same run submitted
    # as JSON, JSONL or a demo is recognised as one execution.
    try:
        trace.content_hash = trace_content_hash(trace)
    except Exception:  # hashing must never break ingestion
        trace.content_hash = None
    return trace, detected, redactions


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _count_redactions(text: str) -> int:
    return text.count("[REDACTED]")


def _trace_from_dict(data: dict[str, Any]) -> NormalizedTrace:
    from app.core.models import Status, StepType, TraceStep

    steps = [
        TraceStep(
            index=s["index"],
            type=StepType(s["type"]),
            name=s.get("name") or "",
            timestamp=s.get("timestamp"),
            duration_ms=s.get("duration_ms"),
            status=Status(s.get("status") or "unknown"),
            input=s.get("input"),
            output=s.get("output"),
            error=s.get("error"),
            error_type=s.get("error_type"),
            tool_name=s.get("tool_name"),
            tool_arguments=s.get("tool_arguments"),
            tool_result=s.get("tool_result"),
            parent_id=s.get("parent_id"),
            step_id=s.get("step_id"),
            metadata=s.get("metadata") or {},
        )
        for s in data.get("steps", [])
    ]
    return NormalizedTrace(
        execution_id=data["execution_id"],
        agent_name=data["agent_name"],
        source_format=data.get("source_format", "generic"),
        framework=data.get("framework"),
        status=Status(data.get("status") or "unknown"),
        started_at=data.get("started_at"),
        ended_at=data.get("ended_at"),
        duration_ms=data.get("duration_ms"),
        input=data.get("input"),
        output=data.get("output"),
        expected_output=data.get("expected_output"),
        task_description=data.get("task_description"),
        steps=steps,
        errors=data.get("errors") or [],
        tool_calls=data.get("tool_calls") or [],
        metadata=data.get("metadata") or {},
        warnings=data.get("warnings") or [],
    )


def ingest_bytes(data: bytes, filename: str | None = None, **hints: Any):
    validate_size(len(data))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ParseError(
                "The file is not valid UTF-8 text.",
                hint="Re-export the trace as UTF-8 JSON or JSONL.",
            ) from exc
    return ingest_text(text, filename=filename, **hints)


__all__ = ["ingest_text", "ingest_bytes", "ParseError", "FORMAT_LABELS", "redact_text", "detect_format"]
