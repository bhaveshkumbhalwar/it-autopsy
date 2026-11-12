"""Severity assessment.

Severity is a function of four explicit factors, each of which is recorded so
the UI can explain the score:

  impact      did the task fail, partially fail, or succeed anyway?
  safety      did anything unsafe or policy-breaking happen?
  position    did the failure happen at the final step (nothing after it) or early?
  frequency   is it a repeated / systemic failure (loops, retries, many errors)?
  recovery    did the agent recover on its own?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.models import FailureCategory, NormalizedTrace, Severity

BASE_WEIGHT: dict[str, float] = {
    FailureCategory.SAFETY.value: 4.0,
    FailureCategory.API.value: 2.5,
    FailureCategory.TIMEOUT.value: 2.0,
    FailureCategory.TOOL_EXECUTION.value: 2.0,
    FailureCategory.INFINITE_LOOP.value: 2.5,
    FailureCategory.MEMORY_CONTEXT.value: 2.0,
    FailureCategory.HALLUCINATION.value: 2.5,
    FailureCategory.REASONING.value: 2.0,
    FailureCategory.PLANNING.value: 1.5,
    FailureCategory.INCORRECT_OUTPUT.value: 1.5,
    FailureCategory.DATABASE.value: 2.5,
    FailureCategory.AUTH.value: 3.0,
}

SEVERITY_SCORE = {Severity.NONE: 0.0, Severity.LOW: 1.0, Severity.MODERATE: 2.0, Severity.HIGH: 3.0, Severity.CRITICAL: 4.0}


@dataclass
class SeverityResult:
    severity: Severity
    score: float
    factors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"severity": self.severity.value, "score": round(self.score, 2), "factors": self.factors}


def assess(
    trace: NormalizedTrace,
    categories: list[str],
    recoverable: str,
    failed_step_index: int | None,
    repeated_failures: int,
) -> SeverityResult:
    if not categories:
        # Nothing went wrong: that is not a low-severity failure, it is no failure.
        return SeverityResult(Severity.NONE, 0.0, [{"factor": "status", "detail": "Execution completed without a detected failure.", "weight": 0}])

    score = max(BASE_WEIGHT.get(c, 1.5) for c in categories)
    factors: list[dict[str, Any]] = [
        {"factor": "category", "detail": f"Highest-impact category: {max(categories, key=lambda c: BASE_WEIGHT.get(c, 1.5))}", "weight": round(score, 2)}
    ]

    total = max(len(trace.steps), 1)
    if trace.status.value == "failed":
        score += 0.8
        factors.append({"factor": "impact", "detail": "The overall execution failed.", "weight": 0.8})
    elif trace.status.value == "partial":
        score += 0.3
        factors.append({"factor": "impact", "detail": "The execution partially succeeded.", "weight": 0.3})
    elif trace.status.value == "success":
        score -= 0.8
        factors.append({"factor": "impact", "detail": "The agent recovered and the final status is success.", "weight": -0.8})

    if FailureCategory.SAFETY.value in categories:
        score += 1.0
        factors.append({"factor": "safety", "detail": "A safety or policy control was involved.", "weight": 1.0})

    if failed_step_index is not None and failed_step_index >= total - 1:
        score += 0.5
        factors.append({"factor": "position", "detail": "Failure occurred at the final recorded step.", "weight": 0.5})

    if repeated_failures > 1:
        bonus = min(1.0, 0.3 * repeated_failures)
        score += bonus
        factors.append({"factor": "frequency", "detail": f"{repeated_failures} failing steps in this execution.", "weight": round(bonus, 2)})

    if recoverable == "yes":
        score -= 0.6
        factors.append({"factor": "recovery", "detail": "Evidence the agent could have recovered (retry or later success).", "weight": -0.6})
    elif recoverable == "no":
        score += 0.3
        factors.append({"factor": "recovery", "detail": "No path to recovery was available in this execution.", "weight": 0.3})

    if score >= 4.0:
        severity = Severity.CRITICAL
    elif score >= 3.0:
        severity = Severity.HIGH
    elif score >= 1.8:
        severity = Severity.MODERATE
    else:
        severity = Severity.LOW
    return SeverityResult(severity, score, factors)
