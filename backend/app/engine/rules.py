"""Rule-based failure detection.

Every rule inspects the normalized trace and, when it fires, emits a
`Detection` carrying the failure category, a severity hint, the offending step
and human-readable evidence. Nothing here invents events: each detection quotes
the step/field it came from.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.core.models import FailureCategory, NormalizedTrace, Severity, StepType, TraceStep
from app.ingest.util import as_text

# --------------------------------------------------------------------------- #
# Signal vocabularies
# --------------------------------------------------------------------------- #

TIMEOUT_RE = re.compile(
    r"(timeout|timed out|deadline exceeded|etimedout|gateway timeout|504|read timeout|connection reset by peer)",
    re.I,
)
# A timeout is not a database problem: these are the *timeout* spellings that
# belong to a database layer, used only to label the event, never to classify.
DB_HINT_RE = re.compile(
    r"(sqlstate|psycopg|asyncpg|sqlalchemy|deadlock|no such table|duplicate key|integrityerror|"
    r"operationalerror|relation .* does not exist|pool (exhausted|timeout)|too many connections|"
    r"database (error|is locked|unavailable)|connection refused.*5432|mongodb|redis)",
    re.I,
)
RATE_LIMIT_RE = re.compile(r"(rate.?limit|429|quota exceeded|too many requests|insufficient_quota)", re.I)
AUTH_RE = re.compile(
    r"(\b401\b|\b403\b|unauthori[sz]ed|invalid.?api.?key|authentication (failed|error)|"
    r"permission denied|forbidden|access denied|insufficient permissions|expired token|invalid token)",
    re.I,
)
# Token budgets and memory are a different failure family from auth.
AUTH_NEGATIVE_RE = re.compile(r"(token (limit|budget|count|usage)|max_?tokens|memory|context)", re.I)
API_ERROR_RE = re.compile(
    r"(500|502|503|internal server error|bad gateway|service unavailable|connection error|apiconnectionerror|overloaded)",
    re.I,
)
TOOL_ERROR_RE = re.compile(
    r"(tool.?error|tool execution failed|function call failed|invalid arguments|schema validation|unknown tool|"
    r"tool not found|missing required parameter|argument parsing failed)",
    re.I,
)
CONTEXT_RE = re.compile(
    r"(context (window|length)|maximum context|too many tokens|token limit|truncat|context overflow|"
    r"message too long|conversation history)",
    re.I,
)
# Explicit policy-control vocabulary. Deliberately narrow: a bare "safety" or
# "refused" is not evidence of a policy block, and ordinary infrastructure errors
# (database, network, auth) must never match this.
POLICY_BLOCK_RE = re.compile(
    r"(content.?policy|content.?filter|policy.?violation|guardrail|moderation.?flag|"
    r"safety.?system.?block|blocked by (the )?(safety|policy|guardrail|content)|"
    r"responsible.?ai.?policy|usage.?polic(y|ies)|harmful.?content|jailbreak|"
    r"human.?approval.?(required|denied)|approval.?denied|requires? (human )?approval|"
    r"action (not )?permitted by policy|denied by policy|forbidden action|destructive action blocked)",
    re.I,
)
POLICY_REFUSAL_RE = re.compile(
    r"(\bI (can ?not|can't|won't|will not|am unable to) (help|assist|comply|do that)\b|"
    r"\bI'm (not able|unable) to (help|assist)\b|\bI must decline\b)",
    re.I,
)
# These can never be policy evidence, whatever else the text says.
POLICY_NEGATIVE_RE = re.compile(
    r"(sqlstate|psycopg|asyncpg|sqlalchemy|deadlock|no such table|duplicate key|integrityerror|"
    r"operationalerror|connection refused|connection reset|\b401\b|\b403\b|\b429\b|\b50[0-9]\b|"
    r"timeout|timed out|rate.?limit|quota|context (window|length)|too many tokens|memoryerror|"
    r"out of memory|jsondecodeerror|invalid json|unknown tool|tool not found)",
    re.I,
)
PARSE_RE = re.compile(
    r"(jsondecodeerror|json decode|failed to parse|parse error|malformed|invalid json|unexpected token|"
    r"could not parse|validation error|pydantic)",
    re.I,
)
PLAN_RE = re.compile(
    r"(no plan|planning (failed|error)|unable to (create|build) (a )?plan|invalid plan|"
    r"step .* not found|task decomposition)",
    re.I,
)
HALLUCINATION_RE = re.compile(
    r"(hallucinat|fabricat|made.?up|not found in (the )?(context|sources|documents)|unsupported claim|"
    r"no evidence)",
    re.I,
)
RETRY_WORD_RE = re.compile(r"(retry|retrying|retries|attempt \d)", re.I)

MAX_TOOL_REPEATS = 3


@dataclass
class Detection:
    category: FailureCategory
    severity: Severity
    confidence: float
    step_index: int | None
    title: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "severity": self.severity.value,
            "confidence": round(self.confidence, 2),
            "step_index": self.step_index,
            "title": self.title,
            "evidence": self.evidence,
            "notes": self.notes,
        }


def _ev(step: TraceStep | None, field_name: str, quote: str, index: int | None = None) -> dict[str, Any]:
    return {
        "step_index": step.index if step else index,
        "step_name": step.name if step else None,
        "field": field_name,
        "quote": as_text(quote, 600),
    }


def _texts(step: TraceStep) -> str:
    return " ".join(
        as_text(v, 2000)
        for v in (step.error, step.output, step.tool_result, step.input, step.metadata)
        if v
    )


def _is_failed(step: TraceStep) -> bool:
    return step.status.value == "failed" or step.type is StepType.ERROR or bool(step.error)


# --------------------------------------------------------------------------- #
# Individual rules
# --------------------------------------------------------------------------- #

def rule_database(trace: NormalizedTrace) -> Detection | None:
    """Database-layer failures: connectivity, integrity, deadlock, pool exhaustion."""
    for step in trace.steps:
        blob = _texts(step)
        if not DB_HINT_RE.search(blob):
            continue
        text = step.error or step.tool_result or step.output or ""
        transient = bool(re.search(r"(deadlock|too many connections|pool (exhausted|timeout)|connection reset)", text, re.I))
        return Detection(
            category=FailureCategory.DATABASE,
            severity=Severity.MODERATE if transient else Severity.HIGH,
            confidence=0.88,
            step_index=step.index,
            title=f"Database error at '{step.tool_name or step.name or 'step'}'",
            evidence=[_ev(step, "error", text)],
            notes=[
                "The failing component is the database layer"
                + (", and the error looks transient (retryable)." if transient else ".")
            ],
        )
    return None


def rule_timeout(trace: NormalizedTrace) -> Detection | None:
    evidence: list[dict[str, Any]] = []
    step_hit = None
    for step in trace.steps:
        blob = _texts(step)
        if TIMEOUT_RE.search(blob) and not DB_HINT_RE.search(blob):
            evidence.append(_ev(step, "error", step.error or step.output or step.tool_result))
            step_hit = step_hit or step
    if not evidence:
        for err in trace.errors:
            if TIMEOUT_RE.search(as_text(err.get("message"))):
                evidence.append({"step_index": err.get("step_index"), "step_name": err.get("step"), "field": "errors", "quote": as_text(err.get("message"), 600)})
    if not evidence:
        return None
    # A timeout that was retried and then succeeded is less severe.
    recovered = trace.status.value in {"success", "partial"} and not any(
        s.status.value == "failed" for s in trace.steps[-1:]
    )
    severity = Severity.MODERATE if recovered else Severity.HIGH
    return Detection(
        category=FailureCategory.TIMEOUT,
        severity=severity,
        confidence=0.9,
        step_index=step_hit.index if step_hit else evidence[0].get("step_index"),
        title="An operation exceeded its time limit",
        evidence=evidence,
        notes=["Timeout signature found in error text." + (" The execution later recovered." if recovered else "")],
    )


def rule_api_failure(trace: NormalizedTrace) -> Detection | None:
    for step in trace.steps:
        blob = _texts(step)
        if DB_HINT_RE.search(blob):
            continue  # a database failure is not a provider API failure
        if AUTH_RE.search(blob) and not AUTH_NEGATIVE_RE.search(blob):
            return Detection(
                category=FailureCategory.AUTH,
                severity=Severity.CRITICAL,
                confidence=0.92,
                step_index=step.index,
                title="Authentication / authorisation error from a provider API",
                evidence=[_ev(step, "error", step.error or step.output)],
                notes=["Credential or permission errors cannot be recovered by retrying."],
            )
        if RATE_LIMIT_RE.search(blob):
            return Detection(
                category=FailureCategory.API,
                severity=Severity.MODERATE,
                confidence=0.9,
                step_index=step.index,
                title="Provider rate limit or quota exceeded",
                evidence=[_ev(step, "error", step.error or step.output)],
            )
        if API_ERROR_RE.search(blob):
            return Detection(
                category=FailureCategory.API,
                severity=Severity.HIGH,
                confidence=0.85,
                step_index=step.index,
                title="Upstream provider returned a server-side error",
                evidence=[_ev(step, "error", step.error or step.output)],
            )
    return None


def rule_tool_execution(trace: NormalizedTrace) -> Detection | None:
    for step in trace.steps:
        blob = _texts(step)
        if DB_HINT_RE.search(blob):
            continue  # classified as a database failure, which is more specific
        if _policy_signal(step):
            continue  # a policy block is a decision, not a tool malfunction
        failed_tool = step.type in (StepType.TOOL_CALL, StepType.TOOL_RESPONSE) and _is_failed(step)
        if failed_tool or (step.type is StepType.TOOL_CALL and TOOL_ERROR_RE.search(blob)):
            return Detection(
                category=FailureCategory.TOOL_EXECUTION,
                severity=Severity.HIGH,
                confidence=0.88,
                step_index=step.index,
                title=f"Tool '{step.tool_name or step.name}' failed to execute",
                evidence=[_ev(step, "error", step.error or step.tool_result or step.output)],
                notes=["The failure is localised to a tool call, not the model's reasoning."],
            )
    return None


def rule_missing_tool_response(trace: NormalizedTrace) -> Detection | None:
    calls = {s.step_id: s for s in trace.steps if s.type is StepType.TOOL_CALL and s.step_id}
    responses = {s.step_id for s in trace.steps if s.type is StepType.TOOL_RESPONSE and s.step_id}
    orphans = [c for sid, c in calls.items() if sid not in responses and not c.tool_result]
    if not orphans:
        # Also catch a tool call that is the last step of a failed run.
        last = trace.steps[-1] if trace.steps else None
        if last and last.type is StepType.TOOL_CALL and trace.status.value == "failed":
            orphans = [last]
    if not orphans:
        return None
    # A policy block explains the missing response; it is not a tool malfunction.
    orphans = [c for c in orphans if not _policy_signal(c)]
    if not orphans:
        return None
    step = orphans[0]
    return Detection(
        category=FailureCategory.TOOL_EXECUTION,
        severity=Severity.HIGH,
        confidence=0.7,
        step_index=step.index,
        title=f"Tool '{step.tool_name or step.name}' was called but no response was recorded",
        evidence=[_ev(step, "tool_arguments", step.tool_arguments or step.name)],
        notes=["A tool call without a matching response usually means a crash, a silent timeout, or a dropped log."],
    )


def rule_infinite_loop(trace: NormalizedTrace) -> Detection | None:
    calls = [s for s in trace.steps if s.type is StepType.TOOL_CALL]
    if len(calls) < MAX_TOOL_REPEATS:
        return None
    signature = Counter(
        (s.tool_name or s.name, as_text(s.tool_arguments, 500)) for s in calls
    )
    (tool, args), count = signature.most_common(1)[0]
    if count < MAX_TOOL_REPEATS:
        return None
    repeats = [s for s in calls if (s.tool_name or s.name, as_text(s.tool_arguments, 500)) == (tool, args)]
    return Detection(
        category=FailureCategory.INFINITE_LOOP,
        severity=Severity.HIGH,
        confidence=min(0.95, 0.6 + 0.1 * count),
        step_index=repeats[0].index,
        title=f"Tool '{tool}' was called {count} times with identical arguments",
        evidence=[_ev(s, "tool_arguments", s.tool_arguments or s.name) for s in repeats[:5]],
        notes=["Repeated identical tool calls with no change in arguments indicate a missing stopping condition."],
    )


def rule_context(trace: NormalizedTrace) -> Detection | None:
    for step in trace.steps:
        if CONTEXT_RE.search(_texts(step)):
            return Detection(
                category=FailureCategory.MEMORY_CONTEXT,
                severity=Severity.HIGH,
                confidence=0.85,
                step_index=step.index,
                title="Context window or token limit reached",
                evidence=[_ev(step, "error", step.error or step.output)],
                notes=["The agent ran out of context, so earlier instructions or tool results were dropped."],
            )
    return None


def _policy_signal(step: TraceStep) -> tuple[str, str] | None:
    """(kind, quoted text) when this step carries *explicit* policy evidence."""
    for field_name, value in (("error", step.error), ("tool_result", step.tool_result), ("output", step.output)):
        text = as_text(value, 4000) if value else ""
        if not text or POLICY_NEGATIVE_RE.search(text):
            continue
        if POLICY_BLOCK_RE.search(text):
            return "block", text
        if step.type is StepType.LLM and POLICY_REFUSAL_RE.search(text):
            return "refusal", text
    return None


def rule_safety(trace: NormalizedTrace) -> Detection | None:
    """Safety/policy violation — only on explicit evidence.

    A database, network, auth or tool error is *not* a policy problem, however
    the surrounding text is worded; those are detected by their own rules.
    """
    for step in trace.steps:
        signal = _policy_signal(step)
        if not signal:
            continue
        kind, text = signal
        is_block = kind == "block"
        return Detection(
            category=FailureCategory.SAFETY,
            severity=Severity.CRITICAL if is_block else Severity.HIGH,
            confidence=0.9 if is_block else 0.7,
            step_index=step.index,
            title=(
                "A policy control blocked the execution"
                if is_block
                else "The model refused on policy grounds"
            ),
            evidence=[_ev(step, "error" if step.error else "output", text)],
            notes=[
                "Explicit policy/guardrail signal quoted above."
                + ("" if is_block else " A refusal is a model-side policy decision, not a tool failure."),
                "This is a policy decision, not an infrastructure failure — do not retry it blindly.",
            ],
        )
    return None


def rule_planning(trace: NormalizedTrace) -> Detection | None:
    for step in trace.steps:
        if PLAN_RE.search(_texts(step)):
            return Detection(
                category=FailureCategory.PLANNING,
                severity=Severity.MODERATE,
                confidence=0.7,
                step_index=step.index,
                title="The agent could not build or follow a valid plan",
                evidence=[_ev(step, "error", step.error or step.output)],
            )
    # A failed run with no tool usage at all suggests the agent never acted.
    has_tools = any(s.type in (StepType.TOOL_CALL, StepType.TOOL_RESPONSE) for s in trace.steps)
    if trace.status.value == "failed" and not has_tools and len(trace.steps) <= 2:
        return Detection(
            category=FailureCategory.PLANNING,
            severity=Severity.MODERATE,
            confidence=0.5,
            step_index=trace.steps[0].index if trace.steps else None,
            title="Execution failed before any tool was used",
            evidence=[_ev(trace.steps[0], "output", trace.steps[0].output or "")] if trace.steps else [],
            notes=["Few or no steps were recorded: the agent stopped during planning or setup."],
        )
    return None


def rule_parse_error(trace: NormalizedTrace) -> Detection | None:
    for step in trace.steps:
        if PARSE_RE.search(_texts(step)):
            return Detection(
                category=FailureCategory.INCORRECT_OUTPUT,
                severity=Severity.MODERATE,
                confidence=0.75,
                step_index=step.index,
                title="The agent produced output that could not be parsed",
                evidence=[_ev(step, "output", step.output or step.error)],
                notes=["Malformed structured output breaks downstream steps that expect JSON."],
            )
    return None


def rule_empty_output(trace: NormalizedTrace) -> Detection | None:
    if trace.status.value != "failed":
        return None
    if trace.output not in (None, "", [], {}):
        return None
    failed_steps = [s for s in trace.steps if _is_failed(s)]
    if failed_steps:
        return None  # already covered by a more specific rule
    return Detection(
        category=FailureCategory.INCORRECT_OUTPUT,
        severity=Severity.HIGH,
        confidence=0.6,
        step_index=trace.steps[-1].index if trace.steps else None,
        title="The execution failed and produced no final output",
        evidence=[{"step_index": None, "step_name": None, "field": "output", "quote": "(empty)"}],
    )


FINAL_KEYS = ("final_output", "finalOutput", "final", "answer", "final_answer")
OBSERVATION_KEYS = ("tool_result", "tool_output", "observation", "observations", "retrieved_documents", "retrieved_context", "context")
VERIFICATION_FAILURE_RE = re.compile(
    r"(contradict|inconsistent|does not match|mismatch|not supported by|unsupported|verification failed|disagree|conflict)",
    re.I,
)


def rule_contradiction(trace: NormalizedTrace) -> Detection | None:
    """Final answer contradicts a tool observation that arrived *before* it.

    We only fire when we can point at a concrete conflicting number or string
    and the final answer was produced after the observation.
    """
    for step in trace.steps:
        if VERIFICATION_FAILURE_RE.search(_texts(step)):
            return Detection(
                category=FailureCategory.REASONING,
                severity=Severity.HIGH,
                confidence=0.85,
                step_index=step.index,
                title="A verification step found the answer inconsistent with the evidence",
                evidence=[_ev(step, "error", step.error or step.output)],
                notes=["The trace itself reports a contradiction between the answer and the collected evidence."],
            )

    def numbers(text: str) -> set[str]:
        return set(re.findall(r"\b\d[\d.,:/\-]{2,}\b", text or ""))

    def words(text: str) -> set[str]:
        return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", text or "")}

    for obs in trace.steps:
        observation = " ".join(
            as_text(obs.tool_result if obs.tool_result is not None else obs.output, 4000) or "",
        )
        if not observation:
            continue
        obs_numbers = numbers(observation)
        obs_words = words(observation)
        if not obs_numbers and not obs_words:
            continue
        for final in trace.steps:
            if final.index <= obs.index:
                continue
            if final.type not in (StepType.OUTPUT, StepType.LLM):
                continue
            answer = as_text(final.output, 4000) or ""
            if not answer:
                continue
            shared_words = obs_words & words(answer)
            if len(shared_words) < 2:
                continue  # not talking about the same entity
            answer_numbers = numbers(answer)
            conflicts = [n for n in answer_numbers if n not in obs_numbers]
            if conflicts:
                # Guard against dates that merely differ in formatting.
                real_conflicts = [c for c in conflicts if c.replace(",", "") not in {o.replace(",", "") for o in obs_numbers}]
                if real_conflicts:
                    return Detection(
                        category=FailureCategory.REASONING,
                        severity=Severity.HIGH,
                        confidence=0.7,
                        step_index=final.index,
                        title="The final answer conflicts with an earlier tool result",
                        evidence=[
                            _ev(obs, "tool_result", observation),
                            _ev(final, "output", answer),
                        ],
                        notes=[
                            f"Values in the answer that do not appear in the observation: {', '.join(real_conflicts[:4])}.",
                            "The agent had the correct information but did not use it consistently.",
                        ],
                    )
    return None


def rule_retry_recovery(trace: NormalizedTrace) -> Detection | None:
    retries = [s for s in trace.steps if s.type is StepType.RETRY or RETRY_WORD_RE.search(s.name or "")]
    if not retries:
        return None
    failed_after = any(s.status.value == "failed" for s in trace.steps[retries[0].index :])
    if failed_after or trace.status.value == "success":
        return None
    return Detection(
        category=FailureCategory.TOOL_EXECUTION,
        severity=Severity.LOW,
        confidence=0.5,
        step_index=retries[0].index,
        title="A retry was attempted",
        evidence=[_ev(s, "name", s.name) for s in retries[:3]],
    )


# Order matters: the first detection becomes the primary category. A structural
# signature such as an infinite loop is more specific than a generic symptom, so
# it is checked before the error-text rules that would otherwise claim it.
RULES = [
    rule_infinite_loop,
    rule_database,
    rule_safety,
    rule_timeout,
    rule_api_failure,
    rule_tool_execution,
    rule_missing_tool_response,
    rule_context,
    rule_planning,
    rule_parse_error,
    rule_contradiction,
    rule_empty_output,
    rule_retry_recovery,
]


def run_rules(trace: NormalizedTrace) -> list[Detection]:
    detections: list[Detection] = []
    seen: set[str] = set()
    for rule in RULES:
        try:
            result = rule(trace)
        except Exception as exc:  # a broken rule must never break analysis
            trace.warnings.append(f"Rule {rule.__name__} failed: {type(exc).__name__}")
            continue
        if result and result.category.value not in seen:
            seen.add(result.category.value)
            detections.append(result)
    return detections


def first_failed_step(trace: NormalizedTrace) -> int | None:
    for step in trace.steps:
        if _is_failed(step):
            return step.index
    return None
