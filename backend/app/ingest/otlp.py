"""OTLP (OpenTelemetry) GenAI span adapter.

Accepts an OTLP JSON export (`resourceSpans[].scopeSpans[].spans[]`), a bare
list of spans, or the OTel collector "span" JSON. GenAI semantic convention
attributes (`gen_ai.*`) and `tool.*` / `mcp.*` attributes are mapped onto steps.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.models import NormalizedTrace, Status, StepType, TraceStep, iso_or_none
from app.ingest.util import as_text, first_of, get_path

_OTEL_STATUS = {"STATUS_CODE_ERROR": Status.FAILED, "STATUS_CODE_OK": Status.SUCCESS, "2": Status.FAILED, "1": Status.SUCCESS}


def _flatten(value: Any) -> dict[str, Any]:
    """OTLP attributes are [{key, value:{stringValue|intValue|boolValue}}]."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            raw = item.get("value")
            if isinstance(raw, dict):
                for vk in ("stringValue", "intValue", "doubleValue", "boolValue"):
                    if vk in raw:
                        out[key] = raw[vk]
                        break
                else:
                    out[key] = raw.get("arrayValue") or raw
            else:
                out[key] = raw
    return out


def _collect_spans(records: list[Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if "resourceSpans" in record or "resource_spans" in record:
            for rs in first_of(record, "resourceSpans", "resource_spans", default=[]) or []:
                resource_attrs = _flatten(get_path(rs, "resource.attributes"))
                for ss in first_of(rs, "scopeSpans", "scope_spans", "instrumentationLibrarySpans", default=[]) or []:
                    for span in ss.get("spans") or []:
                        span = dict(span)
                        span["_resource"] = resource_attrs
                        spans.append(span)
        elif "spans" in record:
            spans.extend([s for s in record["spans"] if isinstance(s, dict)])
        elif "traceId" in record or "trace_id" in record:
            spans.append(record)
    return spans


def parse_otlp(records: list[Any], hints: dict[str, Any] | None = None) -> NormalizedTrace:
    hints = hints or {}
    spans = _collect_spans(records)
    spans.sort(key=lambda s: str(first_of(s, "startTimeUnixNano", "start_time_unix_nano", default=0)))

    steps: list[TraceStep] = []
    trace_id = None
    for i, span in enumerate(spans):
        attrs = _flatten(span.get("attributes"))
        name = str(span.get("name") or "span")
        operation = str(attrs.get("gen_ai.operation.name") or attrs.get("operation.name") or "")
        tool_name = attrs.get("gen_ai.tool.name") or attrs.get("tool.name") or attrs.get("mcp.tool.name")
        if "tool" in operation or tool_name or "tool" in name.lower():
            step_type = StepType.TOOL_CALL
        elif "chat" in operation or "text_completion" in operation or "llm" in operation:
            step_type = StepType.LLM
        elif "agent" in operation or "invoke_agent" in operation:
            step_type = StepType.PLANNING
        else:
            step_type = StepType.SYSTEM

        status_code = str(get_path(span, "status.code", default=""))
        status = _OTEL_STATUS.get(status_code, Status.UNKNOWN)
        if status is Status.UNKNOWN and attrs.get("error.type"):
            status = Status.FAILED
        if status is Status.UNKNOWN:
            status = Status.SUCCESS

        error_message = (
            get_path(span, "status.message")
            or attrs.get("exception.message")
            or attrs.get("error.message")
        )
        if error_message:
            status = Status.FAILED

        start_ns = first_of(span, "startTimeUnixNano", "start_time_unix_nano")
        end_ns = first_of(span, "endTimeUnixNano", "end_time_unix_nano")
        duration = None
        try:
            if start_ns and end_ns:
                duration = round((int(end_ns) - int(start_ns)) / 1e6, 2)
        except (TypeError, ValueError):
            duration = None

        trace_id = trace_id or span.get("traceId") or span.get("trace_id")
        steps.append(
            TraceStep(
                index=i,
                type=step_type,
                name=name,
                timestamp=iso_or_none(int(start_ns) / 1e9) if start_ns else None,
                duration_ms=duration,
                status=status,
                input=attrs.get("gen_ai.prompt") or attrs.get("input.value"),
                output=attrs.get("gen_ai.completion") or attrs.get("output.value"),
                error=as_text(error_message, 2000) if error_message else None,
                error_type=attrs.get("error.type") or attrs.get("exception.type"),
                tool_name=str(tool_name) if tool_name else None,
                tool_arguments=attrs.get("gen_ai.tool.call.arguments") or attrs.get("tool.arguments"),
                tool_result=attrs.get("gen_ai.tool.call.result") or attrs.get("tool.result"),
                parent_id=span.get("parentSpanId") or span.get("parent_span_id"),
                step_id=str(span.get("spanId") or span.get("span_id") or f"span-{i}"),
                metadata={"operation": operation, "attributes": {k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool))}},
            )
        )

    failed = [s for s in steps if s.status is Status.FAILED]
    status = Status.FAILED if failed else (Status.SUCCESS if steps else Status.UNKNOWN)
    agent_name = hints.get("agent_name")
    if not agent_name:
        for span in spans:
            attrs = _flatten(span.get("attributes"))
            if attrs.get("gen_ai.agent.name"):
                agent_name = str(attrs["gen_ai.agent.name"])
                break
    return NormalizedTrace(
        execution_id=str(trace_id or uuid.uuid4().hex[:12]),
        agent_name=str(agent_name or "OTel agent"),
        source_format="otlp",
        framework=str(hints.get("framework") or "opentelemetry"),
        status=status,
        started_at=steps[0].timestamp if steps else None,
        ended_at=None,
        duration_ms=round(sum(s.duration_ms or 0 for s in steps), 2) if steps else None,
        input=next((s.input for s in steps if s.input), None),
        output=next((s.output for s in reversed(steps) if s.output), None),
        expected_output=hints.get("expected_output"),
        task_description=hints.get("task_description"),
        steps=steps,
        errors=[{"step_index": s.index, "step": s.name, "message": s.error, "type": s.error_type} for s in failed],
        tool_calls=[
            {"index": s.index, "tool": s.tool_name, "arguments": s.tool_arguments, "status": s.status.value, "duration_ms": s.duration_ms}
            for s in steps
            if s.type is StepType.TOOL_CALL
        ],
        metadata={"span_count": len(spans)},
    )
