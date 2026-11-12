"""MCP JSON-RPC adapter.

Accepts a JSONL/JSON stream of JSON-RPC 2.0 frames exchanged with an MCP
server (`initialize`, `tools/list`, `tools/call` requests and their responses).
Requests are paired with responses by `id` so tool failures are visible.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.models import NormalizedTrace, Status, StepType, TraceStep
from app.ingest.util import as_text, first_of


def parse_mcp(records: list[Any], hints: dict[str, Any] | None = None) -> NormalizedTrace:
    hints = hints or {}
    requests: dict[Any, dict[str, Any]] = {}
    steps: list[TraceStep] = []
    server_name = None

    for record in records:
        if not isinstance(record, dict):
            continue
        method = record.get("method")
        if method:
            if method == "initialize":
                server_name = as_text(first_of(record, "params.clientInfo.name", default=None)) or server_name
            tool_name = None
            if method == "tools/call":
                tool_name = first_of(record, "params.name", default="tool")
            step = TraceStep(
                index=len(steps),
                type=StepType.TOOL_CALL if tool_name else StepType.SYSTEM,
                name=str(tool_name or method),
                tool_name=str(tool_name) if tool_name else None,
                tool_arguments=first_of(record, "params.arguments"),
                input=record.get("params"),
                step_id=str(record.get("id")),
                status=Status.UNKNOWN,
            )
            steps.append(step)
            requests[record.get("id")] = step
        elif "result" in record or "error" in record:
            req = requests.get(record.get("id"))
            error = record.get("error")
            result = record.get("result")
            if req is None:
                steps.append(
                    TraceStep(
                        index=len(steps),
                        type=StepType.ERROR if error else StepType.TOOL_RESPONSE,
                        name="orphan response",
                        output=as_text(result),
                        error=as_text(error) if error else None,
                        status=Status.FAILED if error else Status.SUCCESS,
                    )
                )
                continue
            if error:
                req.status = Status.FAILED
                req.error = f"{get_path(error, 'code')}: {get_path(error, 'message')}"
                req.error_type = "jsonrpc_error"
                steps.append(
                    TraceStep(
                        index=len(steps),
                        type=StepType.ERROR,
                        name=f"{req.name} failed",
                        error=req.error,
                        error_type="jsonrpc_error",
                        status=Status.FAILED,
                        parent_id=req.step_id,
                    )
                )
            else:
                is_error = bool(get_path(result, "isError"))
                req.tool_result = result
                req.output = as_text(result)
                req.status = Status.FAILED if is_error else Status.SUCCESS
                if is_error:
                    req.error = as_text(result)

    failed = [s for s in steps if s.status is Status.FAILED]
    status = Status.FAILED if failed else (Status.SUCCESS if steps else Status.UNKNOWN)
    return NormalizedTrace(
        execution_id=str(uuid.uuid4().hex[:12]),
        agent_name=str(hints.get("agent_name") or server_name or "MCP client"),
        source_format="mcp",
        framework=str(hints.get("framework") or "mcp"),
        status=status,
        duration_ms=None,
        input=None,
        output=None,
        expected_output=hints.get("expected_output"),
        task_description=hints.get("task_description"),
        steps=steps,
        errors=[{"step_index": s.index, "step": s.name, "message": s.error, "type": s.error_type} for s in failed],
        tool_calls=[
            {"index": s.index, "tool": s.tool_name, "arguments": s.tool_arguments, "status": s.status.value}
            for s in steps
            if s.type is StepType.TOOL_CALL
        ],
        metadata={"frames": len(records), "server": server_name},
    )


def get_path(obj: Any, path: str, default: Any = None) -> Any:  # local tiny helper
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
