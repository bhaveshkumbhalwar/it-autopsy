"""OpenAI adapter.

Handles three shapes:
  * Chat Completions responses (`object: chat.completion`, `choices[...]`)
  * Responses API objects (`object: response`, `output: [...]`)
  * Agents SDK traces: `{ "spans" | "events": [ {span_data | data: {...}} ] }`
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.models import NormalizedTrace, Status, StepType, TraceStep, iso_or_none
from app.ingest.generic import map_status
from app.ingest.util import as_text, duration_from, first_of, get_path, ms_between, parse_ts_str


def _span_to_step(span: dict[str, Any], index: int) -> TraceStep:
    data = span.get("span_data") or span.get("data") or span
    span_type = str(data.get("type") or span.get("span_type") or span.get("type") or "span")
    error = first_of(span, "error", "exception", default=None)
    if isinstance(error, dict):
        error = error.get("message") or error.get("type")

    step_type = StepType.SYSTEM
    tool_name = None
    if "function" in span_type or "tool" in span_type or "mcp" in span_type:
        step_type = StepType.TOOL_CALL
        tool_name = data.get("name") or data.get("tool_name")
    elif "agent" in span_type:
        step_type = StepType.PLANNING
    elif "generation" in span_type or "completion" in span_type or "response" in span_type:
        step_type = StepType.LLM
    elif "handoff" in span_type:
        step_type = StepType.PLANNING
    elif "guardrail" in span_type:
        step_type = StepType.SYSTEM

    return TraceStep(
        index=index,
        type=step_type,
        name=str(span.get("name") or data.get("name") or span_type),
        timestamp=iso_or_none(first_of(span, "started_at", "startedAt", "start_time")),
        duration_ms=duration_from(first_of(span, "duration_ms", "durationMs"))
        or ms_between(first_of(span, "started_at", "startedAt"), first_of(span, "ended_at", "endedAt"), parse_ts_str),
        status=map_status(span.get("status") or ("failed" if error else "success")),
        input=first_of(data, "input", "arguments", "prompt"),
        output=first_of(data, "output", "result", "response", "content"),
        error=as_text(error, 2000) if error else None,
        error_type=get_path(error, "type") if isinstance(error, dict) else None,
        tool_name=tool_name,
        tool_arguments=data.get("arguments") or data.get("input") if tool_name else None,
        tool_result=data.get("output") or data.get("result") if tool_name else None,
        parent_id=span.get("parent_id") or span.get("parentId"),
        step_id=str(span.get("span_id") or span.get("id") or f"span-{index}"),
        metadata={"span_type": span_type},
    )


def _from_chat_completion(record: dict[str, Any]) -> tuple[list[TraceStep], str | None, Any]:
    steps: list[TraceStep] = []
    choice = get_path(record, "choices.0", default={}) or {}
    message = choice.get("message") or {}
    usage = record.get("usage") or {}

    if record.get("messages"):
        from app.ingest.generic import _from_message_list

        steps.extend(_from_message_list(record["messages"]))

    content = message.get("content")
    if content:
        steps.append(
            TraceStep(
                index=len(steps),
                type=StepType.LLM,
                name="completion",
                output=as_text(content),
                status=Status.SUCCESS,
                metadata={"model": record.get("model"), "usage": usage},
            )
        )
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        steps.append(
            TraceStep(
                index=len(steps),
                type=StepType.TOOL_CALL,
                name=str(fn.get("name") or "tool"),
                tool_name=str(fn.get("name") or "tool"),
                tool_arguments=fn.get("arguments"),
                step_id=str(call.get("id") or ""),
            )
        )
    finish = choice.get("finish_reason")
    if finish == "length":
        steps.append(
            TraceStep(
                index=len(steps),
                type=StepType.ERROR,
                name="output truncated",
                error="finish_reason=length: the model hit the max token limit before finishing.",
                error_type="truncated_output",
                status=Status.FAILED,
            )
        )
    return steps, finish, content


def _from_responses_api(record: dict[str, Any]) -> tuple[list[TraceStep], str | None, Any]:
    steps: list[TraceStep] = []
    for item in record.get("output") or []:
        item_type = item.get("type")
        if item_type == "function_call":
            steps.append(
                TraceStep(
                    index=len(steps),
                    type=StepType.TOOL_CALL,
                    name=str(item.get("name") or "tool"),
                    tool_name=str(item.get("name")),
                    tool_arguments=item.get("arguments"),
                    step_id=str(item.get("call_id") or item.get("id") or ""),
                )
            )
        elif item_type == "function_call_output":
            steps.append(
                TraceStep(
                    index=len(steps),
                    type=StepType.TOOL_RESPONSE,
                    name="function output",
                    tool_result=as_text(item.get("output")),
                    status=Status.SUCCESS,
                )
            )
        elif item_type == "message":
            text = " ".join(
                as_text(get_path(part, "text") or get_path(part, "content"))
                for part in item.get("content") or []
                if isinstance(part, dict)
            )
            steps.append(
                TraceStep(
                    index=len(steps),
                    type=StepType.LLM,
                    name=str(item.get("role") or "message"),
                    output=as_text(text),
                    status=Status.SUCCESS,
                )
            )
    status = record.get("status")
    if status == "incomplete":
        reason = get_path(record, "incomplete_details.reason", default="incomplete")
        steps.append(
            TraceStep(
                index=len(steps),
                type=StepType.ERROR,
                name="response incomplete",
                error=f"OpenAI response incomplete: {reason}",
                error_type=str(reason),
                status=Status.FAILED,
            )
        )
    output_text = record.get("output_text") or first_of(record, "output.0.content.0.text")
    return steps, status, output_text


def parse_openai(records: list[Any], hints: dict[str, Any] | None = None) -> NormalizedTrace:
    hints = hints or {}
    record = records[0] if records else {}
    if not isinstance(record, dict):
        record = {"output": records}

    if "spans" in record or "events" in record or "traces" in record:
        spans = first_of(record, "spans", "events", default=None) or []
        steps = [_span_to_step(s, i) for i, s in enumerate(spans) if isinstance(s, dict)]
        finish = None
        output = first_of(record, "output", "final_output")
    elif record.get("object") == "response" or get_path(record, "output") is not None:
        steps, finish, output = _from_responses_api(record)
    else:
        steps, finish, output = _from_chat_completion(record)

    status = map_status(first_of(record, "status", "state"))
    if status is Status.UNKNOWN:
        status = Status.FAILED if any(s.status is Status.FAILED for s in steps) else Status.SUCCESS
    if finish == "length" or finish == "content_filter":
        status = Status.FAILED

    started = first_of(record, "started_at", "created_at", "created", "timestamp")
    ended = first_of(record, "ended_at", "completed_at", "completed")
    duration = duration_from(first_of(record, "duration_ms"))
    if duration is None:
        duration = ms_between(started, ended, parse_ts_str)
    if duration is None:
        total = sum(s.duration_ms or 0 for s in steps)
        duration = round(total, 2) if total else None

    trace = NormalizedTrace(
        execution_id=str(first_of(record, "id", "trace_id", "run_id", default=None) or f"exec-{uuid.uuid4().hex[:12]}"),
        agent_name=str(hints.get("agent_name") or first_of(record, "agent_name", "name", "model", default=None) or "OpenAI agent"),
        source_format="openai",
        framework=str(hints.get("framework") or "openai"),
        status=status,
        started_at=iso_or_none(started),
        ended_at=iso_or_none(ended),
        duration_ms=duration,
        input=first_of(record, "input", "messages", "prompt"),
        output=output,
        expected_output=hints.get("expected_output"),
        task_description=hints.get("task_description") or as_text(first_of(record, "input", "prompt"), 2000),
        steps=steps,
        errors=[
            {"step_index": s.index, "step": s.name, "message": s.error, "type": s.error_type}
            for s in steps
            if s.error
        ],
        tool_calls=[
            {"index": s.index, "tool": s.tool_name, "arguments": s.tool_arguments, "status": s.status.value}
            for s in steps
            if s.type is StepType.TOOL_CALL
        ],
        metadata={"model": record.get("model"), "finish_reason": finish},
    )
    return trace
