"""Generic adapter: the common normalized trace format.

Expected (all keys optional except that *something* is present):

{
  "execution_id": "exec-123",
  "agent_name": "Research Agent",
  "framework": "langchain",
  "status": "failed",
  "started_at": "2024-05-01T10:00:00Z",
  "ended_at": "2024-05-01T10:00:42Z",
  "input": "...",
  "output": "...",
  "task_description": "...",
  "expected_output": "...",
  "steps": [
    {"type": "tool_call", "name": "search", "tool_name": "search",
     "tool_arguments": {...}, "status": "success", "timestamp": "...",
     "duration_ms": 1200, "parent_id": "step-1", "step_id": "step-2"}
  ],
  "metadata": {...}
}
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.models import NormalizedTrace, Status, StepType, TraceStep, iso_or_none
from app.ingest.util import as_text, duration_from, first_of, get_path, ms_between, parse_ts_str

_STATUS_MAP = {
    "success": Status.SUCCESS,
    "succeeded": Status.SUCCESS,
    "ok": Status.SUCCESS,
    "completed": Status.SUCCESS,
    "complete": Status.SUCCESS,
    "failed": Status.FAILED,
    "failure": Status.FAILED,
    "error": Status.FAILED,
    "errored": Status.FAILED,
    "partial": Status.PARTIAL,
    "recovered": Status.PARTIAL,
    "running": Status.UNKNOWN,
    "pending": Status.UNKNOWN,
    "unknown": Status.UNKNOWN,
}


def map_status(value: Any) -> Status:
    if isinstance(value, dict):
        value = value.get("status") or value.get("state") or value.get("type")
    return _STATUS_MAP.get(str(value or "").strip().lower(), Status.UNKNOWN)


_STEP_TYPES = {
    "task": StepType.TASK,
    "input": StepType.TASK,
    "planning": StepType.PLANNING,
    "plan": StepType.PLANNING,
    "llm": StepType.LLM,
    "llm_call": StepType.LLM,
    "generation": StepType.LLM,
    "tool": StepType.TOOL_CALL,
    "tool_call": StepType.TOOL_CALL,
    "function": StepType.TOOL_CALL,
    "function_call": StepType.TOOL_CALL,
    "tool_response": StepType.TOOL_RESPONSE,
    "tool_result": StepType.TOOL_RESPONSE,
    "observation": StepType.TOOL_RESPONSE,
    "error": StepType.ERROR,
    "exception": StepType.ERROR,
    "retry": StepType.RETRY,
    "output": StepType.OUTPUT,
    "final": StepType.OUTPUT,
    "system": StepType.SYSTEM,
}


def map_step_type(value: Any, tool_name: Any = None, has_result: bool = False) -> StepType:
    key = str(value or "").strip().lower().replace("-", "_")
    if key in _STEP_TYPES:
        return _STEP_TYPES[key]
    if has_result:
        return StepType.TOOL_RESPONSE
    if tool_name:
        return StepType.TOOL_CALL
    return StepType.SYSTEM


def _normalize_step(raw: Any, index: int) -> TraceStep:
    if not isinstance(raw, dict):
        return TraceStep(index=index, type=StepType.SYSTEM, output=as_text(raw), name=f"step {index}")

    tool_name = first_of(raw, "tool_name", "name", default=None) if map_step_type(raw.get("type"), raw.get("tool_name") or raw.get("name"), "tool_result" in raw) in (StepType.TOOL_CALL, StepType.TOOL_RESPONSE) else raw.get("tool_name")
    tool_args = first_of(raw, "tool_arguments", "arguments", "args", "input")
    tool_result = first_of(raw, "tool_result", "result", "observation", "tool_output")
    error = first_of(raw, "error", "error_message", "exception", "message" if raw.get("type") in {"error", "exception"} else "", default=None)
    step_type = map_step_type(raw.get("type") or raw.get("kind") or raw.get("role"), tool_name, tool_result is not None)

    start = first_of(raw, "started_at", "start_time", "startTime", "timestamp", "time")
    end = first_of(raw, "ended_at", "end_time", "endTime")
    duration = duration_from(first_of(raw, "duration_ms", "durationMs", "latency_ms", "elapsed_ms"))
    if duration is None:
        duration = ms_between(start, end, parse_ts_str)

    status = map_status(raw.get("status") or raw.get("level") or raw.get("state"))
    if status is Status.UNKNOWN and error:
        status = Status.FAILED
    if status is Status.UNKNOWN and step_type in (StepType.TOOL_RESPONSE, StepType.OUTPUT):
        status = Status.SUCCESS

    return TraceStep(
        index=index,
        type=step_type,
        name=str(raw.get("name") or raw.get("step_name") or (tool_name or step_type.value)),
        timestamp=iso_or_none(start),
        duration_ms=duration,
        status=status,
        input=first_of(raw, "input", "prompt", "request"),
        output=first_of(raw, "output", "response", "content"),
        error=as_text(error, 2000) if error else None,
        error_type=raw.get("error_type") or raw.get("exception_type"),
        tool_name=tool_name if isinstance(tool_name, str) else None,
        tool_arguments=tool_args,
        tool_result=tool_result,
        parent_id=raw.get("parent_id") or raw.get("parentId"),
        step_id=str(raw.get("step_id") or raw.get("id") or f"step-{index}"),
        metadata={
            k: v
            for k, v in raw.items()
            if k
            not in {
                "type", "kind", "role", "name", "input", "output", "error", "status",
                "tool_name", "tool_arguments", "tool_result", "parent_id", "step_id",
                "timestamp", "started_at", "ended_at", "duration_ms",
            }
        },
    )


def _from_message_list(messages: list[Any]) -> list[TraceStep]:
    steps: list[TraceStep] = []
    for i, msg in enumerate(messages):
        role = str(get_path(msg, "role", "message"))
        content = get_path(msg, "content")
        if role == "system":
            steps.append(TraceStep(index=len(steps), type=StepType.SYSTEM, name="system prompt", input=as_text(content)))
        elif role == "user":
            steps.append(TraceStep(index=len(steps), type=StepType.TASK, name="task received", input=as_text(content)))
        elif role == "assistant":
            tool_calls = get_path(msg, "tool_calls") or []
            text = content if isinstance(content, str) else as_text(content)
            if text:
                steps.append(TraceStep(index=len(steps), type=StepType.LLM, name="assistant message", output=as_text(text)))
            for call in tool_calls or []:
                fn = get_path(call, "function", default={}) or {}
                steps.append(
                    TraceStep(
                        index=len(steps),
                        type=StepType.TOOL_CALL,
                        name=str(fn.get("name") or call.get("name") or "tool"),
                        tool_name=str(fn.get("name") or call.get("name") or "tool"),
                        tool_arguments=fn.get("arguments") or call.get("arguments"),
                        step_id=str(call.get("id") or f"call-{i}"),
                    )
                )
        elif role in {"tool", "function"}:
            steps.append(
                TraceStep(
                    index=len(steps),
                    type=StepType.TOOL_RESPONSE,
                    name=str(msg.get("name") or "tool response"),
                    tool_name=str(msg.get("name") or "tool"),
                    tool_result=as_text(content),
                    status=Status.SUCCESS,
                )
            )
    return steps


def parse_generic(records: list[Any], hints: dict[str, Any] | None = None) -> NormalizedTrace:
    hints = hints or {}
    # Unwrap common wrappers: {"trace": {...}}, {"execution": {...}}, {"data": {...}}
    record = records[0] if len(records) == 1 else {"executions": records}
    if isinstance(record, dict):
        for key in ("trace", "execution", "data", "run", "record"):
            if isinstance(record.get(key), dict) and len(record) <= 3:
                record = record[key]
                break
    if not isinstance(record, dict):
        record = {"steps": records}

    raw_steps = first_of(record, "steps", "spans", "events", "children", default=None)
    if raw_steps is None and isinstance(record.get("messages"), list):
        steps = _from_message_list(record["messages"])
    elif raw_steps is None and len(records) > 1:
        steps = [_normalize_step(r, i) for i, r in enumerate(records)]
    else:
        steps = [_normalize_step(s, i) for i, s in enumerate(raw_steps or [])]

    started = first_of(record, "started_at", "start_time", "startTime", "timestamp", "created_at", "createdAt")
    ended = first_of(record, "ended_at", "end_time", "endTime", "completed_at")
    duration = duration_from(first_of(record, "duration_ms", "durationMs", "latency_ms", "total_duration_ms"))
    if duration is None:
        duration = ms_between(started, ended, parse_ts_str)
    if duration is None:
        durations = [s.duration_ms for s in steps if s.duration_ms]
        duration = round(sum(durations), 2) if durations else None

    status = map_status(first_of(record, "status", "state", "result"))
    if status is Status.UNKNOWN:
        if any(s.status is Status.FAILED or s.type is StepType.ERROR for s in steps):
            status = Status.FAILED
        elif steps and all(s.status in (Status.SUCCESS, Status.UNKNOWN) for s in steps):
            status = Status.SUCCESS

    errors = [e for e in (first_of(record, "errors", "exceptions", default=None) or []) if e]
    for s in steps:
        if s.error and s.error not in [as_text(e) for e in errors]:
            errors.append({"step_index": s.index, "step": s.name, "message": s.error, "type": s.error_type})

    execution_id = str(
        first_of(record, "execution_id", "executionId", "id", "run_id", "trace_id", default=None)
        or f"exec-{uuid.uuid4().hex[:12]}"
    )
    agent_name = str(
        first_of(record, "agent_name", "agentName", "agent", "name", default=None)
        or hints.get("agent_name")
        or "Unnamed agent"
    )
    if isinstance(first_of(record, "agent", default=None), dict):
        agent_name = str(get_path(record, "agent.name") or agent_name)

    tool_calls = [
        {
            "index": s.index,
            "tool": s.tool_name,
            "arguments": s.tool_arguments,
            "status": s.status.value,
            "duration_ms": s.duration_ms,
            "error": s.error,
        }
        for s in steps
        if s.type is StepType.TOOL_CALL
    ]

    trace = NormalizedTrace(
        execution_id=execution_id,
        agent_name=agent_name,
        source_format="generic",
        framework=first_of(record, "framework", "agent_framework", default=hints.get("framework")),
        status=status,
        started_at=iso_or_none(started),
        ended_at=iso_or_none(ended),
        duration_ms=duration,
        input=first_of(record, "input", "task", "query", "prompt"),
        output=first_of(record, "output", "final_output", "finalOutput", "response", "answer"),
        expected_output=hints.get("expected_output") or first_of(record, "expected_output", "expected"),
        task_description=hints.get("task_description") or first_of(record, "task_description", "task", "input"),
        steps=steps,
        errors=[{"step_index": e.get("step_index"), "step": e.get("step"), "message": as_text(e.get("message") or e), "type": e.get("type")} if isinstance(e, dict) else {"message": as_text(e)} for e in errors],
        tool_calls=tool_calls,
        metadata={"unparsed_keys": [k for k in record.keys() if k not in {"steps", "messages"}]},
    )
    if hints.get("agent_name"):
        trace.agent_name = str(hints["agent_name"])
    if hints.get("framework"):
        trace.framework = str(hints["framework"])
    return trace
