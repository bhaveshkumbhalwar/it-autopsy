"""LangSmith run adapter.

LangSmith exports a flat list of runs with `run_type` (`llm`, `tool`, `chain`,
`retriever`, ...), `parent_run_id`, `inputs`, `outputs`, `error`, `start_time`,
`end_time`, `total_tokens`. The flat list is rebuilt into parent/child steps.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.models import NormalizedTrace, Status, StepType, TraceStep, iso_or_none
from app.ingest.generic import map_status
from app.ingest.util import as_text, duration_from, first_of, ms_between, parse_ts_str

_RUN_TYPE_MAP = {
    "llm": StepType.LLM,
    "tool": StepType.TOOL_CALL,
    "chain": StepType.PLANNING,
    "graph": StepType.PLANNING,
    "retriever": StepType.TOOL_CALL,
    "prompt": StepType.SYSTEM,
    "parser": StepType.OUTPUT,
    "embedding": StepType.TOOL_CALL,
}


def parse_langsmith(records: list[Any], hints: dict[str, Any] | None = None) -> NormalizedTrace:
    hints = hints or {}
    runs = [r for r in records if isinstance(r, dict)]
    if len(runs) == 1 and isinstance(runs[0].get("runs"), list):
        runs = [r for r in runs[0]["runs"] if isinstance(r, dict)]
    elif len(runs) == 1 and isinstance(runs[0].get("traces"), list):
        collected = []
        for group in runs[0]["traces"]:
            collected.extend(r for r in (group.get("runs") or []) if isinstance(r, dict))
        runs = collected

    def start_key(run: dict[str, Any]) -> str:
        return str(first_of(run, "start_time", "startTime", default="") or "")

    runs.sort(key=start_key)
    steps: list[TraceStep] = []
    roots: list[dict[str, Any]] = []
    for i, run in enumerate(runs):
        run_type = str(run.get("run_type") or "chain")
        error = run.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("type")
        outputs = run.get("outputs")
        inputs = run.get("inputs")
        status = map_status(run.get("status")) if run.get("status") else (
            Status.FAILED if error else Status.SUCCESS
        )
        duration = duration_from(run.get("latency_ms")) or ms_between(
            run.get("start_time"), run.get("end_time"), parse_ts_str
        )
        step_type = _RUN_TYPE_MAP.get(run_type, StepType.SYSTEM)
        name = str(run.get("name") or run_type)
        if step_type is StepType.TOOL_CALL:
            name = str(run.get("name") or "tool")
        steps.append(
            TraceStep(
                index=i,
                type=step_type,
                name=name,
                timestamp=iso_or_none(run.get("start_time")),
                duration_ms=duration,
                status=status,
                input=inputs,
                output=outputs,
                error=as_text(error, 2000) if error else None,
                error_type=run.get("error_type"),
                tool_name=name if step_type is StepType.TOOL_CALL else None,
                tool_arguments=inputs if step_type is StepType.TOOL_CALL else None,
                tool_result=outputs if step_type is StepType.TOOL_CALL else None,
                parent_id=run.get("parent_run_id"),
                step_id=str(run.get("id") or f"run-{i}"),
                metadata={"run_type": run_type, "total_tokens": run.get("total_tokens")},
            )
        )
        if not run.get("parent_run_id"):
            roots.append(run)

    root = roots[0] if roots else (runs[0] if runs else {})
    if not steps:
        return NormalizedTrace(
            execution_id=str(uuid.uuid4().hex[:12]),
            agent_name=str(hints.get("agent_name") or "LangSmith agent"),
            source_format="langsmith",
            framework="langsmith",
            warnings=["No runs found in the uploaded LangSmith export."],
        )

    failed = [s for s in steps if s.status is Status.FAILED]
    if not failed:
        status = Status.SUCCESS
    else:
        # A failed root run means the whole execution failed; a failed child run
        # that the root still completed means the execution partially recovered.
        # LangSmith marks a completed-but-failed run with an `error` field; the
        # execution is only "partial" when the root finished and produced output
        # despite a failed child run.
        root_runs = [r for r in runs if not r.get("parent_run_id")]
        root_failed = any(r.get("error") for r in root_runs)
        root_completed = bool(root_runs and (root_runs[0].get("outputs") or root_runs[0].get("end_time")))
        status = Status.PARTIAL if root_completed and not root_failed else Status.FAILED

    trace_id = root.get("trace_id") or root.get("id") or uuid.uuid4().hex[:12]
    total_duration = sum(s.duration_ms or 0 for s in steps) or None
    return NormalizedTrace(
        execution_id=str(trace_id),
        agent_name=str(hints.get("agent_name") or root.get("name") or "LangSmith agent"),
        source_format="langsmith",
        framework=str(hints.get("framework") or "langsmith"),
        status=status,
        started_at=iso_or_none(root.get("start_time")),
        ended_at=iso_or_none(root.get("end_time")),
        duration_ms=duration_from(root.get("latency_ms")) or ms_between(root.get("start_time"), root.get("end_time"), parse_ts_str),
        input=root.get("inputs"),
        output=root.get("outputs"),
        expected_output=hints.get("expected_output"),
        task_description=hints.get("task_description") or as_text(root.get("inputs"), 2000),
        steps=steps,
        errors=[{"step_index": s.index, "step": s.name, "message": s.error, "type": s.error_type} for s in failed],
        tool_calls=[
            {"index": s.index, "tool": s.tool_name, "arguments": s.tool_arguments, "status": s.status.value, "duration_ms": s.duration_ms}
            for s in steps
            if s.type is StepType.TOOL_CALL
        ],
        metadata={"run_count": len(runs), "total_step_duration_ms": total_duration},
    )
