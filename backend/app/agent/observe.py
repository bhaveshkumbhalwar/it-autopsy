"""Observe: the facts the agent is allowed to reason from.

The diagnosis engine already parses traces. This module does not re-parse
anything: it reads the normalised trace and the stored analysis and produces a
flat, quotable observation set, so every later decision can point at *what was
seen* rather than at a summary.

Nothing here interprets or judges — interpretation is ``decide.py``.
"""

from __future__ import annotations

from typing import Any

from app.core.models import NormalizedTrace, Status, StepType
from app.ingest.util import as_text

# How much of any one string reaches a decision. Enough to quote, small enough
# that a hostile 2 MB error message cannot swamp a prompt or a JSON column.
QUOTE_CHARS = 600


def _quote(value: Any, limit: int = QUOTE_CHARS) -> str | None:
    text = as_text(value or "", limit)
    text = " ".join(text.split())
    return text or None


def observe(trace: NormalizedTrace, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything the agent saw, in one structure.

    ``analysis`` is the engine's result for this trace (rule + ML + optional
    LLM). It is optional so the observation can be taken before an analysis
    exists.
    """
    analysis = analysis or {}
    steps = trace.steps

    failed_steps = [
        {
            "index": step.index,
            "number": step.index + 1,
            "type": step.type.value,
            "name": step.name or None,
            "tool_name": step.tool_name,
            "error": _quote(step.error),
            "error_type": step.error_type,
        }
        for step in steps
        if step.status is Status.FAILED and step.type is not StepType.ERROR
    ]

    tool_calls = [
        {
            "index": step.index,
            "tool_name": step.tool_name,
            "status": step.status.value,
            "arguments": _quote(step.tool_arguments, 400),
            "result": _quote(step.tool_result, 400),
            "duration_ms": step.duration_ms,
        }
        for step in steps
        if step.tool_name
    ]

    retries = [
        {"index": step.index, "name": step.name or None, "status": step.status.value}
        for step in steps
        if step.type is StepType.RETRY or "retry" in (step.name or "").lower()
    ]

    durations = [step.duration_ms for step in steps if isinstance(step.duration_ms, (int, float))]
    slowest = max(durations) if durations else None

    categories = [c.get("category") for c in analysis.get("categories") or [] if c.get("category")]
    ml = analysis.get("ml") or {}

    return {
        "execution": {
            "execution_id": trace.execution_id,
            "agent_name": trace.agent_name,
            "framework": trace.framework,
            "status": trace.status.value,
            "task": _quote(trace.task_description, 400),
            "step_count": len(steps),
            "tool_call_count": len(tool_calls),
            "duration_ms": trace.duration_ms,
            "slowest_step_ms": slowest,
        },
        "sequence": [
            {"index": step.index, "type": step.type.value, "name": step.name or None, "status": step.status.value}
            for step in steps
        ],
        "failed_steps": failed_steps,
        "tool_calls": tool_calls,
        "retries": retries,
        "output": _quote(trace.output, 800),
        # The engine's own view, carried through unchanged: the agent decides
        # with the rules and the classifier, it does not re-run them.
        "engine": {
            "status": analysis.get("status"),
            "primary_category": analysis.get("primary_category"),
            "categories": categories,
            # Rules decide; the classifier's hint is listed separately so a
            # decision never rests on it.
            "rule_categories": [
                c.get("category")
                for c in analysis.get("categories") or []
                if c.get("category") and (c.get("source") in {None, "rule"} or c.get("is_primary", True))
                and c.get("category") in categories
            ],
            "ml_hint_category": (analysis.get("ml") or {}).get("prediction"),
            "root_cause": analysis.get("root_cause"),
            "failed_step_index": analysis.get("failed_step_index"),
            "confidence": analysis.get("confidence"),
            "severity": analysis.get("severity"),
            "recoverable": analysis.get("recoverable"),
            "evidence": [
                {
                    "step_index": row.get("step_index"),
                    "field": row.get("field"),
                    "quote": _quote(row.get("quote")),
                    "source": row.get("source"),
                    "role": row.get("role"),
                }
                for row in (analysis.get("evidence") or [])[:6]
            ],
            "ml": {
                "prediction": ml.get("prediction"),
                "confidence": ml.get("confidence"),
                "hint_only": ml.get("hint_only", True),
                "agrees": ml.get("agrees"),
            },
        },
        "signals": {
            "has_error_text": any(item["error"] for item in failed_steps),
            "has_failed_step": bool(failed_steps),
            "has_tool_calls": bool(tool_calls),
            "has_retries": bool(retries),
            "has_engine_evidence": bool(analysis.get("evidence")),
            "has_engine_categories": bool(categories),
            "status": trace.status.value,
        },
    }


def observe_dict(trace_dict: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Observe a trace that is stored as a plain dict (raw_trace in the DB)."""
    from app.core.models import Status as _Status
    from app.core.models import StepType as _StepType

    steps = []
    for raw in trace_dict.get("steps") or []:
        try:
            status = _Status(str(raw.get("status") or "unknown").lower())
        except ValueError:
            status = _Status.UNKNOWN
        try:
            step_type = _StepType(str(raw.get("type") or "other").lower())
        except ValueError:
            step_type = _StepType.SYSTEM
        steps.append(
            {
                "index": raw.get("index"),
                "type": step_type.value,
                "name": raw.get("name"),
                "status": status.value,
                "tool_name": raw.get("tool_name"),
                "tool_arguments": raw.get("tool_arguments"),
                "tool_result": raw.get("tool_result"),
                "error": raw.get("error"),
                "error_type": raw.get("error_type"),
                "duration_ms": raw.get("duration_ms"),
            }
        )
    return observe(_SimpleTrace(trace_dict, steps), analysis)


class _SimpleTrace:
    """The handful of attributes ``observe`` reads, from a stored dict."""

    def __init__(self, raw: dict[str, Any], steps: list[dict[str, Any]]) -> None:
        from app.core.models import Status as _Status

        self.execution_id = raw.get("execution_id")
        self.agent_name = raw.get("agent_name")
        self.framework = raw.get("framework")
        self.task_description = raw.get("task_description")
        self.output = raw.get("output")
        self.duration_ms = raw.get("duration_ms")
        try:
            self.status = _Status(str(raw.get("status") or "unknown").lower())
        except ValueError:
            self.status = _Status.UNKNOWN
        self.steps = [_SimpleStep(step) for step in steps]


class _SimpleStep:
    def __init__(self, raw: dict[str, Any]) -> None:
        from app.core.models import Status as _Status
        from app.core.models import StepType as _StepType

        self.index = int(raw.get("index") or 0)
        self.name = raw.get("name") or ""
        self.tool_name = raw.get("tool_name")
        self.tool_arguments = raw.get("tool_arguments")
        self.tool_result = raw.get("tool_result")
        self.error = raw.get("error")
        self.error_type = raw.get("error_type")
        self.duration_ms = raw.get("duration_ms")
        try:
            self.status = _Status(str(raw.get("status") or "unknown").lower())
        except ValueError:
            self.status = _Status.UNKNOWN
        try:
            self.type = _StepType(str(raw.get("type") or "other").lower())
        except ValueError:
            self.type = _StepType.SYSTEM
