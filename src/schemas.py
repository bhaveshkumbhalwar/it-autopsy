"""Typed data models used across the agent.

No external dependencies (stdlib only). Kept explicit to avoid framework magic
and to make serialization to JSON trivial.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Issue:
    text: str
    os: str = "unknown"
    case_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TriageResult:
    category: str
    category_probs: dict[str, float] | None = None
    scores: dict[str, float] = field(default_factory=dict)
    priority: str = "medium"
    priority_rationale: list[str] = field(default_factory=list)
    confidence_label: str = "score (not calibrated)"


@dataclass(slots=True)
class Evidence:
    doc_id: str
    source: str
    title: str
    url: str
    snippet: str
    score: float
    os_scope: str = ""
    citation: str = ""


@dataclass(slots=True)
class Step:
    step_id: int
    action: str
    tool_name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    requires_approval: bool = False
    risk: str = "low"


@dataclass(slots=True)
class Plan:
    steps: list[Step] = field(default_factory=list)
    rationale: str = ""


@dataclass(slots=True)
class ToolCall:
    tool_name: str
    params: dict[str, Any]
    reason: str = ""


@dataclass(slots=True)
class ToolResult:
    tool_name: str
    simulated: bool
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: str = ""
    duration_ms: int = 0
    redacted: bool = False


@dataclass(slots=True)
class Verification:
    outcome: str
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    follow_up_question: str = ""


@dataclass(slots=True)
class Escalation:
    summary: str
    symptoms: str
    sources: list[str] = field(default_factory=list)
    actions_attempted: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    to: str = "human support"


@dataclass(slots=True)
class Case:
    case_id: str
    issue: Issue
    triage: TriageResult | None = None
    evidence: list[Evidence] = field(default_factory=list)
    plan: Plan | None = None
    results: list[ToolResult] = field(default_factory=list)
    verification: Verification | None = None
    escalation: Escalation | None = None


def to_dict(obj) -> dict:
    return asdict(obj)
