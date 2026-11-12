"""Evidence bookkeeping and causal ordering.

Three jobs, all of them about *not* over-claiming:

* **Deduplication** — the same failure event is often recorded more than once
  (a failed span, the error attached to it, a tool-call summary, a framework's
  own error list). One event must produce one evidence row, keyed on
  ``(step/span, error type, normalised message)``.
* **Causal classification** — of the failures present, exactly one is the root
  cause: the earliest failure that is not explained by an earlier one. Failures
  that sit under a failed span (parent links) or that merely repeat an earlier
  error are downstream. Nothing is invented: parents come from the trace's own
  ``parent_id``/``step_id`` links, otherwise from execution order.
* **Contribution accounting** — each evidence row that changed the diagnosis
  carries a weight and a human sentence saying what it contributed, so a
  confidence score can be explained rather than asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.core.models import NormalizedTrace, Status, StepType, TraceStep

# --------------------------------------------------------------------------- #
# Error-type vocabulary (drives dedupe and the "error type" of an event)
# --------------------------------------------------------------------------- #

ERROR_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("timeout", re.compile(r"(timeout|timed out|deadline exceeded|etimedout|gateway timeout|\b504\b|read timeout)", re.I)),
    ("rate_limit", re.compile(r"(rate.?limit|\b429\b|too many requests|quota exceeded|insufficient_quota)", re.I)),
    ("auth", re.compile(r"(\b401\b|\b403\b|unauthori[sz]ed|invalid.?api.?key|authentication|permission denied|forbidden)", re.I)),
    # Narrow on purpose: "policy", "safety" or "refused" alone are not policy
    # evidence — a refused database connection is an infrastructure failure.
    ("policy", re.compile(
        r"(content.?policy|content.?filter|policy.?violation|guardrail|moderation|safety.?system.?block|"
        r"blocked by (the )?(safety|policy|guardrail|content)|usage.?polic(y|ies)|harmful.?content|"
        r"jailbreak|requires? (human )?approval|approval.?denied|denied by policy)",
        re.I,
    )),
    ("context_overflow", re.compile(r"(context (window|length)|maximum context|too many tokens|token limit|context overflow)", re.I)),
    ("memory", re.compile(r"(out of memory|memoryerror|cannot allocate memory|oom|heap (space|limit))", re.I)),
    ("database", re.compile(
        r"(sqlstate|psycopg|asyncpg|sqlalchemy|deadlock|connection refused.*5432|database (error|is locked|unavailable)|"
        r"no such table|duplicate key|integrityerror|operationalerror|relation .* does not exist|"
        r"could not connect to server|too many connections|pool (exhausted|timeout)|mongodb|redis)",
        re.I,
    )),
    ("tool_error", re.compile(
        r"(tool.?error|tool execution failed|function call failed|invalid arguments|schema validation|"
        r"unknown tool|tool not found|missing required parameter)",
        re.I,
    )),
    ("api_error", re.compile(
        r"(\b500\b|\b502\b|\b503\b|internal server error|bad gateway|service unavailable|"
        r"connection error|apiconnectionerror|overloaded)",
        re.I,
    )),
    ("parse_error", re.compile(r"(jsondecodeerror|json decode|failed to parse|parse error|invalid json|unexpected token)", re.I)),
    ("validation_error", re.compile(r"(validation error|pydantic|schema mismatch)", re.I)),
]

_WS_RE = re.compile(r"\s+")
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_ID_RE = re.compile(r"\b[0-9a-f]{8,}\b")
_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b")


def normalise_message(text: str | None) -> str:
    """A message reduced to what identifies the event, not where it happened.

    Addresses, long hex ids, timestamps and whitespace are stripped so the same
    failure logged twice with different decoration compares equal.
    """
    if not text:
        return ""
    value = _ADDR_RE.sub("<addr>", str(text))
    value = _TS_RE.sub("<ts>", value)
    value = _ID_RE.sub("<id>", value)
    value = _WS_RE.sub(" ", value).strip().lower()
    return value[:400]


def classify_error_type(*texts: str | None) -> str | None:
    """First matching error family for an event, or None when nothing matches."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    for name, pattern in ERROR_TYPE_PATTERNS:
        if pattern.search(blob):
            return name
    return None


def event_signature(text: str | None) -> str:
    """The part of a message that says *what* failed, for cross-step dedupe.

    "psycopg OperationalError: connection refused (host a)" and the same text
    logged again elsewhere are one event; the location is compared separately.
    """
    normalised = normalise_message(text)
    if not normalised:
        return ""
    # Keep the leading clause: that is the error itself, not its decoration.
    for sep in (" at line", " in file", " traceback", "\n"):
        normalised = normalised.split(sep)[0]
    return normalised[:160]


# --------------------------------------------------------------------------- #
# Evidence rows
# --------------------------------------------------------------------------- #

ROOT = "root_cause"
CONTRIBUTING = "contributing"
DOWNSTREAM = "downstream"
CONTEXT = "context"


@dataclass
class EvidenceItem:
    """One deduplicated trace event backing the diagnosis."""

    step_index: int | None
    step_name: str | None
    field: str
    quote: str
    error_type: str | None = None
    error_message: str | None = None
    categories: list[str] = field(default_factory=list)
    source: str = "rule"
    why: str | None = None
    role: str = CONTRIBUTING
    causes: list[int] = field(default_factory=list)
    caused_by: int | None = None
    # what this row contributed to confidence
    weight: float = 0.0
    contribution: str | None = None
    span_id: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of the underlying event.

        Two rows are the same event when they report the same error (type +
        message) on the same step/span, or the same error with no step attached
        to either — a framework's global error list restating a step's error.
        A repeat of the same error on a *different* step is kept, because that
        is a separate occurrence in the execution.
        """
        signature = event_signature(self.error_message or self.quote)
        # A row that is not tied to a step is never keyed apart from the step it
        # describes: it is a restatement, not a second event.
        location = self.span_id or (str(self.step_index) if self.step_index is not None else "")
        return (location, self.error_type or self.field, signature)

    def signature(self) -> str:
        """The error this row reports, independent of where it was recorded."""
        return event_signature(self.error_message or self.quote)

    def is_same_event_as(self, other: "EvidenceItem") -> bool:
        """True when both rows describe one occurrence in the trace."""
        mine = self.signature()
        if self.error_type != other.error_type or mine != other.signature() or not mine:
            return False
        return self._same_span(other)

    def _same_span(self, other: "EvidenceItem") -> bool:
        """Two rows belong to one span, or sit on the same step."""
        if self.span_id and other.span_id:
            return self.span_id == other.span_id
        return self.step_index is not None and self.step_index == other.step_index

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "step_name": self.step_name,
            "field": self.field,
            "quote": self.quote,
            "error_type": self.error_type,
            "category": self.categories[0] if self.categories else None,
            "categories": list(self.categories),
            "source": self.source,
            "why": self.why,
            "role": self.role,
            "causes": list(self.causes),
            "caused_by": self.caused_by,
            "weight": round(self.weight, 3),
            "contribution": self.contribution,
        }


class EvidenceBook:
    """Collects evidence rows, merging repeats of the same event."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], EvidenceItem] = {}

    def add(self, item: EvidenceItem) -> EvidenceItem:
        existing = self._items.get(item.key)
        if existing is None:
            # A restatement of an event already recorded in the trace (the same
            # error on the same span, however the framework logged it) is one
            # event, not two.
            existing = next((i for i in self._items.values() if i.is_same_event_as(item)), None)
            if existing is None:
                self._items[item.key] = item
                return item
        # Same event seen twice: keep the richer description, merge categories.
        for cat in item.categories:
            if cat not in existing.categories:
                existing.categories.append(cat)
        if len(item.quote or "") > len(existing.quote or ""):
            existing.quote = item.quote
        if not existing.why and item.why:
            existing.why = item.why
        if existing.role == CONTRIBUTING and item.role in (ROOT, DOWNSTREAM):
            existing.role = item.role
        existing.weight = max(existing.weight, item.weight)
        existing.contribution = existing.contribution or item.contribution
        return existing

    def items(self) -> list[EvidenceItem]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)


# --------------------------------------------------------------------------- #
# Causal analysis
# --------------------------------------------------------------------------- #

@dataclass
class CausalNode:
    step: TraceStep
    error_type: str | None
    message: str
    caused_by: int | None = None
    is_root: bool = False


def _failure_text(step: TraceStep) -> str:
    for value in (step.error, step.tool_result, step.output):
        if value:
            from app.ingest.util import as_text

            return as_text(value, 600)
    return ""


def failed_steps(trace: NormalizedTrace) -> list[TraceStep]:
    """Steps that carry a failure, deduplicated by their own step id."""
    seen: set[str] = set()
    out: list[TraceStep] = []
    for step in trace.steps:
        failed = step.status is Status.FAILED or step.type is StepType.ERROR or bool(step.error)
        if not failed:
            continue
        ident = step.step_id or f"idx-{step.index}"
        if ident in seen:
            continue
        seen.add(ident)
        out.append(step)
    return out


def analyse_causality(trace: NormalizedTrace) -> tuple[CausalNode | None, list[CausalNode]]:
    """Return (root cause node, all failure nodes with causal links).

    Rules, in order:
      1. A failure whose parent span also failed is downstream of that parent.
      2. A failure repeating an earlier failure's (type, message) is downstream.
      3. Otherwise the earliest remaining failure is the root cause.
    """
    failures = failed_steps(trace)
    if not failures:
        return None, []

    by_id = {s.step_id: s for s in trace.steps if s.step_id}
    nodes: dict[int, CausalNode] = {}
    for step in failures:
        message = _failure_text(step)
        nodes[step.index] = CausalNode(
            step=step,
            error_type=classify_error_type(step.error, step.error_type, message),
            message=message,
        )

    # 1. parent links
    for index, node in nodes.items():
        parent_id = node.step.parent_id
        hops = 0
        while parent_id and parent_id in by_id and hops < 50:
            parent = by_id[parent_id]
            if parent.index in nodes and parent.index != index:
                node.caused_by = parent.index
                break
            parent_id = parent.parent_id
            hops += 1

    # 2. repeated error signatures
    seen_signatures: dict[tuple[str, str], int] = {}
    for node in sorted(nodes.values(), key=lambda n: n.step.index):
        if node.caused_by is not None:
            continue
        signature = (node.error_type or "unknown", normalise_message(node.message))
        if signature in seen_signatures:
            node.caused_by = seen_signatures[signature]
        else:
            seen_signatures[signature] = node.step.index

    # 3. earliest remaining failure wins; everything else is downstream of it
    roots = [n for n in nodes.values() if n.caused_by is None]
    root = min(roots, key=lambda n: n.step.index) if roots else min(nodes.values(), key=lambda n: n.step.index)
    root.is_root = True
    for node in nodes.values():
        if node is root:
            continue
        if node.caused_by is None:
            node.caused_by = root.step.index

    return root, sorted(nodes.values(), key=lambda n: n.step.index)


def describe_causal_role(node: CausalNode) -> str:
    if node.is_root:
        return "Earliest failure in this execution and not explained by an earlier one — the root cause."
    return f"Downstream of step {node.caused_by}; it repeats an effect rather than causing one."


# --------------------------------------------------------------------------- #
# Confidence from contributions
# --------------------------------------------------------------------------- #

def confidence_from_contributions(items: Iterable[EvidenceItem], ml_agrees: bool = False, llm_used: bool = False) -> tuple[float, list[dict[str, Any]]]:
    """Score confidence as the sum of what the evidence actually contributes.

    Starts from a small base for having a located failure at all; every further
    point must be attributable to a specific row, and the arithmetic is returned
    so the UI can show it instead of a bare number.
    """
    items = list(items)
    located = [i for i in items if i.step_index is not None]
    factors: list[dict[str, Any]] = []

    score = 0.30 if located else 0.15
    factors.append(
        {
            "factor": "located_failure",
            "detail": f"Failure localised to step {located[0].step_index}" if located else "No step could be localised",
            "weight": round(score, 2),
        }
    )

    root_rows = [i for i in items if i.role == ROOT]
    if root_rows:
        score += 0.30
        factors.append(
            {
                "factor": "root_cause_identified",
                "detail": f"Causal analysis isolated one root cause among {len([i for i in items if i.role in (ROOT, DOWNSTREAM, CONTRIBUTING)])} failure event(s)",
                "weight": 0.30,
            }
        )

    strong = [i for i in items if i.weight >= 0.2]
    if strong:
        score += 0.20
        factors.append(
            {
                "factor": "explicit_error_text",
                "detail": f"{len(strong)} evidence row(s) quote an explicit error message or policy signal",
                "weight": 0.20,
            }
        )
    elif any(i.weight > 0 for i in items):
        score += 0.10
        factors.append(
            {
                "factor": "reported_failure",
                "detail": "The trace itself reports the failure, but the quoted text names no error family",
                "weight": 0.10,
            }
        )

    if any(i.role == DOWNSTREAM for i in items):
        score += 0.05
        factors.append(
            {
                "factor": "causal_chain_consistent",
                "detail": "Downstream failures are explained by the root cause, so the chain is consistent",
                "weight": 0.05,
            }
        )

    if ml_agrees:
        score += 0.08
        factors.append({"factor": "ml_agreement", "detail": "The classifier independently agrees with the rule-based category", "weight": 0.08})

    if llm_used:
        score += 0.05
        factors.append({"factor": "llm_corroboration", "detail": "A configured LLM layer corroborated the diagnosis", "weight": 0.05})

    score = round(min(0.98, max(0.1, score)), 2)
    return score, factors
