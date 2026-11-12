"""One merged timeline for the execution detail page.

The page used to render three overlapping lists — a causal chain, an execution
timeline and a pile of evidence rows — which asked the reader to cross-reference
step numbers by hand. This module folds them into a single ordered list where
every step carries its own evidence, and names the step that actually failed.

Nothing here invents data: every field comes from the stored trace, the
analysis's ``evidence`` rows or its ``causal_chain``. Text is redacted before it
is returned, so a secret in a step's input or output never reaches the browser.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.redact import redact_secret
from app.ingest.util import as_text, stable_hash

# A step is "notable" if it failed, is the root cause, or belongs to a failure
# family — those stay expanded on a long trace; the rest collapse behind a
# "show all" control.
RAW_LIMITS = {"input": 1200, "output": 1600, "error": 800}


def _clean(value: Any, limit: int) -> str | None:
    text = as_text(value, limit) if value not in (None, "", [], {}) else ""
    if not text:
        return None
    return redact_secret(text, settings.llm_api_key)[0]


def _repeats(quote: Any, step_text: str) -> bool:
    """True when the evidence quote is already visible as the step's own text."""
    clean = str(quote or "").strip()
    if not clean or not step_text:
        return clean == "" or False
    return clean[:120] in step_text


def _step_kind(step_type: str) -> str:
    return {
        "task": "task",
        "planning": "plan",
        "llm": "llm",
        "tool_call": "tool",
        "tool_response": "tool",
        "retry": "retry",
        "error": "error",
        "output": "output",
    }.get(str(step_type), "other")


def _kind_label(kind: str) -> str:
    return {
        "task": "Task",
        "plan": "Plan",
        "llm": "LLM call",
        "tool": "Tool call",
        "retry": "Retry",
        "error": "Error",
        "output": "Output",
        "other": "Step",
    }[kind]


def _step_title(step: dict[str, Any], kind: str) -> str:
    """What the step is, in plain words. Never the literal string "unknown"."""
    name = (step.get("name") or "").strip()
    tool = (step.get("tool_name") or "").strip()
    # A producer that labels an LLM step with a tool name still made a model
    # call: say so, and keep the tool as the qualifier.
    if kind == "llm" and tool and not name:
        return f"LLM · {tool}"
    if kind == "tool":
        return tool or name or "Tool call"
    if kind == "llm":
        # A raw model call usually has no name; describe it by its tool/skill
        # when the trace provides one, otherwise keep it neutral.
        if name and name.lower() not in {"unknown", "none"}:
            return f"LLM · {name}"
        return "LLM call"
    if kind == "task":
        return name or "Task received"
    if kind == "error":
        return name or "Error record"
    if kind == "retry":
        return name or "Retry"
    if kind in {"plan", "output"}:
        return name or _kind_label(kind)
    return name or "Step"


def build_timeline(
    trace: dict[str, Any],
    analysis: dict[str, Any] | None,
    steps_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The steps of an execution, each with its own evidence and raw IO.

    ``steps_override`` lets the compare view pass the same steps without a full
    analysis; otherwise the steps come from the stored trace.
    """
    steps = steps_override if steps_override is not None else (trace.get("steps") or [])
    failed_index = (analysis or {}).get("failed_step_index")
    causal_nodes = {node.get("step_index"): node for node in (analysis or {}).get("causal_chain") or []}

    by_step: dict[Any, list[dict[str, Any]]] = {}
    unplaced: list[dict[str, Any]] = []
    for row in (analysis or {}).get("evidence") or []:
        index = row.get("step_index")
        if index is None:
            unplaced.append(row)
        else:
            by_step.setdefault(index, []).append(row)

    # Rows that report the same event as a step's own error are not repeated
    # twice: a step's `error` field already reads that text, so its evidence
    # keeps only what the step itself does not say (the causation, the role).
    step_payloads = {step.get("index"): " ".join(str(step.get(k) or "") for k in ("error", "tool_result", "output")) for step in steps}

    items: list[dict[str, Any]] = []
    for step in steps:
        kind = _step_kind(str(step.get("type")))
        status = str(step.get("status") or "unknown")
        node = causal_nodes.get(step.get("index"))
        evidence = by_step.get(step.get("index"), [])
        is_root = bool(node and node.get("role") == "root_cause") or step.get("index") == failed_index
        is_failure = is_root or status == "failed" or kind == "error"

        error_text = _clean(step.get("error"), RAW_LIMITS["error"])
        tool_result = _clean(step.get("tool_result"), RAW_LIMITS["output"])
        output_text = _clean(step.get("output"), RAW_LIMITS["output"]) or tool_result

        items.append(
            {
                "index": step.get("index"),
                "number": (step.get("index") or 0) + 1,
                "kind": kind,
                "type": step.get("type"),
                "title": _step_title(step, kind),
                "kind_label": _kind_label(kind),
                "status": status,
                "timestamp": step.get("timestamp"),
                "duration_ms": step.get("duration_ms"),
                "is_root_cause": is_root,
                "is_failure": is_failure,
                "causal_role": (node or {}).get("role"),
                "summary": _clean(
                    step.get("error") or step.get("tool_result") or step.get("output") or step.get("input"), 240
                ),
                "detail": {
                    "input": _clean(step.get("input"), RAW_LIMITS["input"]),
                    "output": output_text,
                    "error": error_text,
                    "tool_arguments": _clean(step.get("tool_arguments"), 600),
                    "metadata": _clean(step.get("metadata"), 400),
                },
                "evidence": [
                    {
                        "field": row.get("field"),
                        "role": row.get("role"),
                        "quote": _clean(row.get("quote"), 600),
                        "why": _clean(row.get("why"), 240),
                        "category": row.get("category"),
                        "source": row.get("source"),
                        "weight": row.get("weight"),
                        "caused_by": row.get("caused_by"),
                        "repeats_step_error": _repeats(row.get("quote"), step_payloads.get(step.get("index"), "")),
                    }
                    for row in evidence
                ],
                # Cheap, stable identifier so the page can keep expansion state
                # across a re-analysis of the same trace.
                "key": stable_hash([step.get("index"), step.get("type"), step.get("name")])[:12],
            }
        )

    # Evidence that is not tied to a step (a framework-level error list, a
    # policy note) still has to be visible: attach it to the failing step when
    # there is one, otherwise show it as its own trailing row.
    if unplaced:
        target = next((item for item in items if item["is_root_cause"]), None)
        if target is not None:
            for row in unplaced:
                target["evidence"].append(
                    {
                        "field": row.get("field"),
                        "role": row.get("role"),
                        "quote": _clean(row.get("quote"), 600),
                        "why": _clean(row.get("why"), 240),
                        "category": row.get("category"),
                        "source": row.get("source"),
                        "weight": row.get("weight"),
                        "caused_by": row.get("caused_by"),
                    }
                )
        else:
            items.append(
                {
                    "index": None,
                    "number": None,
                    "kind": "error",
                    "type": "error",
                    "title": "Execution-level errors",
                    "kind_label": "Error",
                    "status": "failed",
                    "timestamp": None,
                    "duration_ms": None,
                    "is_root_cause": False,
                    "is_failure": True,
                    "causal_role": None,
                    "summary": None,
                    "detail": {"input": None, "output": None, "error": None, "tool_arguments": None, "metadata": None},
                    "evidence": [
                        {
                            "field": row.get("field"),
                            "role": row.get("role"),
                            "quote": _clean(row.get("quote"), 600),
                            "why": _clean(row.get("why"), 240),
                            "category": row.get("category"),
                            "source": row.get("source"),
                            "weight": row.get("weight"),
                            "caused_by": row.get("caused_by"),
                        }
                        for row in unplaced
                    ],
                    "key": "execution-errors",
                }
            )

    notable = [item["index"] for item in items if item["is_failure"]]
    return {
        "items": items,
        "failed_step_index": failed_index,
        "failed_step_number": (failed_index + 1) if isinstance(failed_index, int) else None,
        # Long traces collapse everything except the failure path: the client
        # expands these and hides the rest behind "show all".
        "notable_indices": notable,
        "collapsed_by_default": len(items) > 8,
        "total_steps": len(items),
    }
