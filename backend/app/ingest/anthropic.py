"""Anthropic Messages adapter.

Accepts a Messages API response, a stream of `message_start` / `content_block_*`
/ `message_delta` events, or a list of messages with `tool_use` /
`tool_result` content blocks.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.models import NormalizedTrace, Status, StepType, TraceStep, iso_or_none
from app.ingest.generic import map_status
from app.ingest.util import as_text, first_of, get_path


def _blocks_to_steps(blocks: list[Any], steps: list[TraceStep]) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_use":
            steps.append(
                TraceStep(
                    index=len(steps),
                    type=StepType.TOOL_CALL,
                    name=str(block.get("name") or "tool"),
                    tool_name=str(block.get("name") or "tool"),
                    tool_arguments=block.get("input"),
                    step_id=str(block.get("id") or ""),
                )
            )
        elif kind == "tool_result":
            is_error = bool(block.get("is_error"))
            steps.append(
                TraceStep(
                    index=len(steps),
                    type=StepType.TOOL_RESPONSE,
                    name="tool result",
                    tool_result=as_text(block.get("content")),
                    status=Status.FAILED if is_error else Status.SUCCESS,
                    error=as_text(block.get("content"), 2000) if is_error else None,
                    step_id=str(block.get("tool_use_id") or ""),
                )
            )
        elif kind == "text":
            steps.append(
                TraceStep(
                    index=len(steps),
                    type=StepType.LLM,
                    name="text",
                    output=as_text(block.get("text")),
                    status=Status.SUCCESS,
                )
            )


def parse_anthropic(records: list[Any], hints: dict[str, Any] | None = None) -> NormalizedTrace:
    hints = hints or {}
    steps: list[TraceStep] = []
    text_parts: list[str] = []
    stop_reason = None
    execution_id = None
    started = ended = None
    error = None

    for record in records:
        if not isinstance(record, dict):
            continue
        rtype = str(record.get("type") or "")

        if rtype == "error" or "error" in record and isinstance(record.get("error"), dict):
            err = record.get("error") or {}
            error = f"{err.get('type', 'error')}: {err.get('message', '')}".strip()
            steps.append(
                TraceStep(index=len(steps), type=StepType.ERROR, name="api error", error=error, error_type=err.get("type"), status=Status.FAILED)
            )
            continue

        message = record.get("message") if isinstance(record.get("message"), dict) else record
        if message.get("id"):
            execution_id = execution_id or str(message["id"])
        started = started or message.get("created_at") or message.get("timestamp")
        if message.get("stop_reason"):
            stop_reason = message["stop_reason"]
        if message.get("content") and isinstance(message["content"], list):
            _blocks_to_steps(message["content"], steps)

        delta = record.get("delta") or {}
        if delta.get("stop_reason"):
            stop_reason = delta["stop_reason"]
        if rtype == "content_block_start":
            block = record.get("content_block") or {}
            if block.get("type") == "tool_use":
                steps.append(
                    TraceStep(
                        index=len(steps),
                        type=StepType.TOOL_CALL,
                        name=str(block.get("name") or "tool"),
                        tool_name=str(block.get("name") or "tool"),
                        step_id=str(block.get("id") or ""),
                    )
                )
        if rtype == "content_block_delta":
            d = record.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                text_parts.append(str(d["text"]))
            if d.get("type") == "input_json_delta" and steps:
                steps[-1].metadata.setdefault("partial_json", "")
                steps[-1].metadata["partial_json"] += str(d.get("partial_json") or "")

        if "messages" in record and isinstance(record["messages"], list):
            for msg in record["messages"]:
                role = msg.get("role")
                content = msg.get("content")
                if role == "user" and isinstance(content, str):
                    steps.insert(0, TraceStep(index=0, type=StepType.TASK, name="task received", input=as_text(content)))
                elif role == "assistant" and isinstance(content, list):
                    _blocks_to_steps(content, steps)
                elif role == "user" and isinstance(content, list):
                    _blocks_to_steps(content, steps)

    if text_parts:
        steps.append(
            TraceStep(index=len(steps), type=StepType.LLM, name="completion", output=as_text("".join(text_parts)), status=Status.SUCCESS)
        )

    if stop_reason in {"max_tokens", "refusal"}:
        steps.append(
            TraceStep(
                index=len(steps),
                type=StepType.ERROR,
                name="stopped early",
                error=f"stop_reason={stop_reason}"
                + (" — output hit the max_tokens limit." if stop_reason == "max_tokens" else " — model refused to continue."),
                error_type=stop_reason,
                status=Status.FAILED,
            )
        )

    status = map_status(first_of(records[0] if records else {}, "status"))
    if error or stop_reason in {"max_tokens", "refusal"}:
        status = Status.FAILED
    elif status is Status.UNKNOWN:
        status = Status.FAILED if any(s.status is Status.FAILED for s in steps) else Status.SUCCESS

    output = "".join(text_parts) or next((s.output for s in reversed(steps) if s.output), None)
    return NormalizedTrace(
        execution_id=execution_id or f"exec-{uuid.uuid4().hex[:12]}",
        agent_name=str(hints.get("agent_name") or first_of(records[0] if records else {}, "model", "agent_name", default=None) or "Anthropic agent"),
        source_format="anthropic",
        framework=str(hints.get("framework") or "anthropic"),
        status=status,
        started_at=iso_or_none(started),
        ended_at=iso_or_none(ended),
        duration_ms=None,
        input=first_of(records[0] if records else {}, "input", "prompt"),
        output=output,
        expected_output=hints.get("expected_output"),
        task_description=hints.get("task_description"),
        steps=steps,
        errors=[{"step_index": s.index, "step": s.name, "message": s.error, "type": s.error_type} for s in steps if s.error],
        tool_calls=[{"index": s.index, "tool": s.tool_name, "arguments": s.tool_arguments, "status": s.status.value} for s in steps if s.type is StepType.TOOL_CALL],
        metadata={"stop_reason": stop_reason, "records": len(records)},
    )
