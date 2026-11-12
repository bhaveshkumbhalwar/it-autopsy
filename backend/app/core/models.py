"""Shared enums and the normalized trace dataclasses.

Every adapter (OpenAI, Anthropic, LangSmith, OTLP, MCP, generic) converts its
input into these structures so that one pipeline can analyse any framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Status(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    # The run hit a failure and still completed it (e.g. a 429 retried and
    # answered). Reporting it as "success" hides the incident; reporting it as
    # "failed" is wrong. It is its own status.
    RECOVERED = "recovered"
    UNKNOWN = "unknown"


class FailureCategory(str, Enum):
    REASONING = "reasoning_failure"
    HALLUCINATION = "hallucination"
    TOOL_EXECUTION = "tool_execution_failure"
    API = "api_failure"
    MEMORY_CONTEXT = "memory_context_failure"
    PLANNING = "planning_failure"
    INFINITE_LOOP = "infinite_loop"
    INCORRECT_OUTPUT = "incorrect_output"
    SAFETY = "safety_policy_violation"
    TIMEOUT = "timeout_failure"
    DATABASE = "database_failure"
    AUTH = "authentication_failure"


FAILURE_LABELS: dict[str, str] = {
    FailureCategory.REASONING.value: "Reasoning failure",
    FailureCategory.HALLUCINATION.value: "Hallucination",
    FailureCategory.TOOL_EXECUTION.value: "Tool execution failure",
    FailureCategory.API.value: "API failure",
    FailureCategory.MEMORY_CONTEXT.value: "Memory / context failure",
    FailureCategory.PLANNING.value: "Planning failure",
    FailureCategory.INFINITE_LOOP.value: "Infinite loop",
    FailureCategory.INCORRECT_OUTPUT.value: "Incorrect output",
    FailureCategory.SAFETY.value: "Safety / policy violation",
    FailureCategory.TIMEOUT.value: "Timeout failure",
    FailureCategory.DATABASE.value: "Database failure",
    FailureCategory.AUTH.value: "Authentication failure",
}


class Severity(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


SEVERITY_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "CRITICAL": 3}


class StepType(str, Enum):
    TASK = "task"
    PLANNING = "planning"
    LLM = "llm"
    TOOL_CALL = "tool_call"
    TOOL_RESPONSE = "tool_response"
    ERROR = "error"
    RETRY = "retry"
    OUTPUT = "output"
    SYSTEM = "system"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@dataclass
class TraceStep:
    index: int
    type: StepType
    name: str = ""
    timestamp: str | None = None
    duration_ms: float | None = None
    status: Status = Status.UNKNOWN
    input: Any = None
    output: Any = None
    error: str | None = None
    error_type: str | None = None
    tool_name: str | None = None
    tool_arguments: Any = None
    tool_result: Any = None
    parent_id: str | None = None
    step_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "type": self.type.value,
            "name": self.name,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "error_type": self.error_type,
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "tool_result": self.tool_result,
            "parent_id": self.parent_id,
            "step_id": self.step_id,
            "metadata": self.metadata,
        }


@dataclass
class NormalizedTrace:
    """The common trace structure every adapter produces."""

    execution_id: str
    agent_name: str
    source_format: str
    framework: str | None = None
    status: Status = Status.UNKNOWN
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    input: Any = None
    output: Any = None
    expected_output: str | None = None
    task_description: str | None = None
    steps: list[TraceStep] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Set by ingestion: sha256 of the trace content, for duplicate detection.
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "agent_name": self.agent_name,
            "source_format": self.source_format,
            "framework": self.framework,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "input": self.input,
            "output": self.output,
            "expected_output": self.expected_output,
            "task_description": self.task_description,
            "steps": [s.to_dict() for s in self.steps],
            "errors": self.errors,
            "tool_calls": self.tool_calls,
            "metadata": self.metadata,
            "warnings": self.warnings,
            # content_hash is deliberately absent: it is derived from this
            # dict, and execution_id is generated per submission. Identity is
            # (everything except those two) — see ingest.stable_hash usage.
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: Any) -> datetime | None:
    """Best-effort timestamp parser for the many formats vendors use."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # seconds vs milliseconds
        seconds = float(value) / (1000.0 if float(value) > 1e11 else 1.0)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def iso_or_none(value: Any) -> str | None:
    return _iso(parse_timestamp(value))
