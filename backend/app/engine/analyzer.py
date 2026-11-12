"""The diagnosis pipeline: rules + ML + optional LLM → one Analysis record.

Design notes
------------
* The rule engine is authoritative for *what* failed (it quotes the log).
* The ML classifier adds a second, independent opinion; it is merged only when
  rules found nothing, or when it is confident and the rule engine is uncertain.
* The LLM (when configured) explains *why* and adds nuance; its output is
  labelled as LLM-derived and its evidence is cross-checked against real steps.
* Confidence is reported as a heuristic score in [0,1], explicitly not a
  calibrated probability.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.models import (
    FAILURE_LABELS,
    FailureCategory,
    NormalizedTrace,
    Severity,
    Status,
    StepType,
)
from app.engine import evidence as evidence_mod
from app.engine import llm, recommendations, severity as severity_mod
from app.engine import snippets as snippets_mod
from app.engine.rules import Detection, first_failed_step, run_rules
from app.ingest.util import as_text
from app.ml.classifier import get_classifier

ENGINE_VERSION = "1.1"


def _timeline(trace: NormalizedTrace, failed_index: int | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in trace.steps:
        label = step.name or step.type.value
        if step.type is StepType.TASK:
            label = "Task received"
        elif step.type is StepType.PLANNING:
            label = f"Planning · {label}" if step.name else "Planning"
        elif step.type is StepType.TOOL_CALL:
            label = f"Tool call · {step.tool_name or label}"
        elif step.type is StepType.TOOL_RESPONSE:
            label = f"Tool response · {step.tool_name or label}"
        elif step.type is StepType.LLM:
            label = f"LLM · {label}" if step.name else "LLM call"
        elif step.type is StepType.ERROR:
            label = f"Error · {label}"
        elif step.type is StepType.RETRY:
            label = f"Retry · {label}"
        elif step.type is StepType.OUTPUT:
            label = "Final output"
        out.append(
            {
                "index": step.index,
                "type": step.type.value,
                "label": label,
                "status": step.status.value,
                "timestamp": step.timestamp,
                "duration_ms": step.duration_ms,
                "detail": as_text(step.error or step.output or step.tool_result or step.input, 320),
                "is_failure": bool(
                    failed_index is not None
                    and step.index == failed_index
                    or step.status is Status.FAILED
                    or step.type is StepType.ERROR
                ),
            }
        )
    return out


def _recoverable(trace: NormalizedTrace, failed_index: int | None) -> str:
    if failed_index is None:
        return "unknown"
    after = trace.steps[failed_index + 1 :]
    if not after:
        return "no"
    if trace.status in (Status.SUCCESS, Status.PARTIAL):
        return "yes"
    if any(s.type is StepType.RETRY for s in after):
        return "yes"
    return "unknown"


# Evidence weight by category strength. These are the *inputs* to confidence;
# the score itself is summed from them in evidence.confidence_from_contributions
# and returned with its arithmetic so it can be checked.
STRONG_CATEGORIES = {
    FailureCategory.AUTH,
    FailureCategory.DATABASE,
    FailureCategory.SAFETY,
    FailureCategory.TIMEOUT,
    FailureCategory.INFINITE_LOOP,
}
MODERATE_CATEGORIES = {
    FailureCategory.API,
    FailureCategory.TOOL_EXECUTION,
    FailureCategory.MEMORY_CONTEXT,
}


def _evidence_weight(category: FailureCategory) -> tuple[float, str]:
    if category in STRONG_CATEGORIES:
        return 0.25, "explicit, specific error signal for this failure family"
    if category in MODERATE_CATEGORIES:
        return 0.2, "named failure family with a quoted error message"
    return 0.1, "inferred from execution structure rather than an explicit error"


def _signal_text(trace: NormalizedTrace) -> str:
    """The text the ML classifier sees: errors, failures and final output."""
    parts: list[str] = []
    for step in trace.steps:
        if step.error:
            parts.append(f"{step.type.value} {step.name} error: {as_text(step.error, 800)}")
        if step.status is Status.FAILED and (step.output or step.tool_result):
            parts.append(f"{step.type.value} {step.name} failed: {as_text(step.output or step.tool_result, 600)}")
        if step.type is StepType.TOOL_CALL and step.tool_name:
            parts.append(f"tool {step.tool_name} called with {as_text(step.tool_arguments, 200)}")
    for err in trace.errors:
        if err.get("message"):
            parts.append(f"error: {as_text(err['message'], 600)}")
    if trace.output:
        parts.append(f"final output: {as_text(trace.output, 800)}")
    return "\n".join(parts)


def _causal_rank(step_index: int | None, root_index: int | None) -> int:
    """0 = the root cause, 1 = contributing, 2 = downstream of the root cause."""
    if step_index is None or root_index is None:
        return 1
    if step_index == root_index:
        return 0
    return 2 if step_index > root_index else 1


def _assemble_evidence(
    trace: NormalizedTrace,
    rule_dets: list[Detection],
    root_index: int | None,
    causal_nodes: list[evidence_mod.CausalNode],
) -> evidence_mod.EvidenceBook:
    """One row per trace event, deduplicated, labelled with its causal role."""
    book = evidence_mod.EvidenceBook()
    node_by_index = {n.step.index: n for n in causal_nodes}
    step_by_index = {s.index: s for s in trace.steps}
    # A tool span and the separate error record the ingest layer created for it
    # are one event. They are merged by span id, or — when the ingest layer gave
    # each its own id — by an error record repeating the preceding step's error.
    span_by_index: dict[int, str | None] = {idx: step.step_id for idx, step in step_by_index.items()}
    for idx, step in sorted(step_by_index.items()):
        if idx == 0 or step.type is not StepType.ERROR:
            continue
        prev = step_by_index.get(idx - 1)
        if prev is None or not prev.step_id:
            continue
        if (step.error or "").strip() and (step.error or "").strip() == (prev.error or "").strip():
            span_by_index[idx] = prev.step_id

    for det in rule_dets:
        weight, reason = _evidence_weight(det.category)
        is_ml = det.title.startswith("Classifier suggests") or det.title.startswith("LLM identified")
        for item in det.evidence:
            idx = item.get("step_index")
            step = step_by_index.get(idx) if idx is not None else None
            quote = item.get("quote") or ""
            if not quote and step is not None:
                # No quoted text from the rule: cite the step's own failure text
                # so the row points at something the reader can check.
                quote = as_text(step.error or step.tool_result or step.output or step.status.value, 400)
            # The message identifies the event for dedupe; a bare quote such as
            # a status word must not be used in its place, or unrelated events
            # would collapse into one row.
            message = (step.error if step and step.error else quote) or ""
            role = evidence_mod.CONTEXT if idx is None else (
                evidence_mod.ROOT if idx == root_index else
                evidence_mod.DOWNSTREAM if root_index is not None and idx > root_index else
                evidence_mod.CONTRIBUTING
            )
            node = node_by_index.get(idx) if idx is not None else None
            book.add(
                evidence_mod.EvidenceItem(
                    step_index=idx,
                    step_name=item.get("step_name") or (step.name if step else None),
                    field=item.get("field") or "error",
                    quote=quote,
                    error_type=(node.error_type if node else evidence_mod.classify_error_type(quote, step.error if step else None)),
                    error_message=message or None,
                    categories=[det.category.value],
                    source="ml" if det.title.startswith("Classifier") else ("llm" if det.title.startswith("LLM") else "rule"),
                    why=item.get("why") or (evidence_mod.describe_causal_role(node) if node else None),
                    role=role,
                    caused_by=node.caused_by if node else None,
                    weight=0.0 if is_ml else weight,
                    contribution=None if is_ml else f"{det.category.value}: {reason}",
                    span_id=span_by_index.get(idx) if idx is not None else None,
                )
            )

    # Failures the rules did not name still belong in the causal chain, marked
    # as such so nothing in the trace is silently dropped or double-counted.
    for node in causal_nodes:
        idx = node.step.index
        book.add(
            evidence_mod.EvidenceItem(
                step_index=idx,
                step_name=node.step.name,
                field="error" if node.step.error else "status",
                quote=node.message or node.step.status.value,
                error_type=node.error_type,
                error_message=node.message,
                categories=[],
                source="trace",
                why=evidence_mod.describe_causal_role(node),
                role=evidence_mod.ROOT if node.is_root else evidence_mod.DOWNSTREAM,
                caused_by=node.caused_by,
                weight=0.0,
                contribution=None,
                span_id=span_by_index.get(idx),
            )
        )
    return book


def diagnose(trace: NormalizedTrace, use_llm: bool = True, capture_llm: bool = False) -> dict[str, Any]:
    detection_notes: list[str] = []
    rule_dets = run_rules(trace)

    # ---- causal analysis first: it decides what the root cause is
    root_node, causal_nodes = evidence_mod.analyse_causality(trace)
    root_index = root_node.step.index if root_node else None
    if causal_nodes:
        downstream = [n for n in causal_nodes if not n.is_root]
        detection_notes.append(
            f"Causal analysis: {len(causal_nodes)} failure event(s); step {root_index} is the earliest and is treated as the root cause"
            + (f", with {len(downstream)} downstream failure(s) explained by it." if downstream else ".")
        )

    # Root-cause step wins over whichever rule happened to fire first.
    if root_index is not None:
        rule_dets.sort(key=lambda d: (0 if d.step_index == root_index else 1, d.step_index if d.step_index is not None else 10**6))
    failed_index = root_index if root_index is not None else (rule_dets[0].step_index if rule_dets else first_failed_step(trace))

    # ---- ML opinion
    classifier = get_classifier()
    prediction = classifier.predict(_signal_text(trace))
    ml_used = False
    ml_confidence = None
    if prediction and prediction.confidence >= 0.45:
        ml_used = True
        ml_confidence = prediction.confidence
        detection_notes.append(
            f"ML classifier suggested {FAILURE_LABELS.get(prediction.label, prediction.label)} "
            f"(confidence {prediction.confidence:.2f}) on a TF-IDF model."
        )
        if not rule_dets:
            rule_dets.append(
                Detection(
                    category=FailureCategory(prediction.label),
                    severity=Severity.MODERATE,
                    confidence=round(0.4 + 0.4 * prediction.confidence, 2),
                    step_index=failed_index,
                    title=f"Classifier suggests {FAILURE_LABELS.get(prediction.label, prediction.label)}",
                    evidence=[
                        {
                            "step_index": failed_index,
                            "step_name": None,
                            "field": "ml_classifier",
                            "quote": as_text(_signal_text(trace), 400),
                        }
                    ],
                    notes=["Detected by the ML classifier, not by a deterministic rule."],
                )
            )

    # ---- LLM opinion
    llm_result: dict[str, Any] | None = None
    llm_error: str | None = None
    llm_prompt: str | None = None
    llm_raw: str | None = None
    if use_llm and llm.is_enabled():
        if capture_llm:
            llm_result, llm_error, llm_prompt, llm_raw = llm.analyze_full(trace, [d.to_dict() for d in rule_dets])
        else:
            llm_result, llm_error = llm.analyze(trace, [d.to_dict() for d in rule_dets])
        if llm_result:
            detection_notes.append(f"LLM analysis ({settings.llm_model}) added contextual reasoning.")
            for extra in llm_result.get("additional_categories") or []:
                cat = str(extra.get("category") or "")
                if cat in FAILURE_LABELS and cat not in {d.category.value for d in rule_dets}:
                    rule_dets.append(
                        Detection(
                            category=FailureCategory(cat),
                            severity=Severity.MODERATE,
                            confidence=float(extra.get("confidence") or 0.6),
                            step_index=llm_result.get("failed_step_index"),
                            title=f"LLM identified {FAILURE_LABELS[cat]}",
                            evidence=[
                                {
                                    "step_index": llm_result.get("failed_step_index"),
                                    "step_name": None,
                                    "field": "llm_analysis",
                                    "quote": as_text(extra.get("reason"), 500),
                                }
                            ],
                            notes=["Raised by the LLM layer; verify against the trace."],
                        )
                    )
        elif llm_error:
            detection_notes.append(f"LLM analysis unavailable: {llm_error}")
    elif use_llm:
        detection_notes.append("LLM analysis skipped: no API key configured (rule + ML detection only).")

    # ---- categories, each tagged with what it is in the causal chain
    categories: list[dict[str, Any]] = []
    for det in rule_dets:
        entry = det.to_dict()
        rank = _causal_rank(entry.get("step_index"), root_index)
        entry["causal_role"] = "root_cause" if rank == 0 else ("downstream" if rank == 2 else "contributing")
        categories.append(entry)
    primary = categories[0]["category"] if categories else None
    for cat in categories:
        cat["is_primary"] = cat["category"] == primary

    # ---- evidence book
    book = _assemble_evidence(trace, rule_dets, root_index, causal_nodes)
    evidence = [item.to_dict() for item in book.items()]
    evidence.sort(key=lambda e: (e["step_index"] is None, e["step_index"] if e["step_index"] is not None else 0, e["field"]))

    failed_steps = sum(1 for s in trace.steps if s.status is Status.FAILED or s.type is StepType.ERROR)
    recoverable = (llm_result or {}).get("recoverable") or _recoverable(trace, failed_index)
    if recoverable not in {"yes", "no", "unknown"}:
        recoverable = "unknown"

    sev = severity_mod.assess(trace, [c["category"] for c in categories], recoverable, failed_index, failed_steps)

    # ---- confidence, derived from the evidence that actually exists
    ml_agrees = bool(prediction and categories and prediction.label == primary)
    confidence, confidence_factors = evidence_mod.confidence_from_contributions(
        book.items(), ml_agrees=ml_agrees, llm_used=bool(llm_result)
    )

    # ---- narrative fields
    if categories:
        titles = "; ".join(c["title"] for c in categories[:3])
        what_happened = (llm_result or {}).get("what_happened") or (
            f"The execution {trace.status.value} after {len(trace.steps)} step(s). "
            f"Detection: {titles}."
        )
        root_cause = (llm_result or {}).get("root_cause") or _rule_root_cause(trace, categories[0], failed_index)
        impact = (llm_result or {}).get("impact") or _rule_impact(trace, categories[0])
    else:
        what_happened = (llm_result or {}).get("what_happened") or (
            f"The execution completed with status '{trace.status.value}' in {len(trace.steps)} step(s); "
            "no failure signal matched the deterministic rules."
        )
        root_cause = (llm_result or {}).get("root_cause") or "No failure detected, so there is no root cause to report."
        impact = (llm_result or {}).get("impact") or "None identified."

    if llm_result:
        for item in (llm_result.get("evidence") or [])[:6]:
            if not isinstance(item, dict):
                continue
            idx = item.get("step_index")
            if idx is not None and not any(s.index == idx for s in trace.steps):
                continue  # discard evidence pointing at a step that does not exist
            if any(e["step_index"] == idx and e["source"] == "llm" for e in evidence):
                continue
            evidence.append(
                {
                    "step_index": idx,
                    "step_name": next((s.name for s in trace.steps if s.index == idx), None),
                    "field": item.get("field") or "llm_analysis",
                    "quote": as_text(item.get("quote"), 500),
                    "why": as_text(item.get("why"), 400),
                    "category": primary,
                    "source": "llm",
                    "role": evidence_mod.CONTEXT,
                }
            )

    recs = recommendations.for_categories([c["category"] for c in categories])
    prevention = recommendations.prevention_for([c["category"] for c in categories])
    if llm_result and llm_result.get("prevention"):
        for line in llm_result["prevention"][:3]:
            if isinstance(line, str) and line not in prevention:
                prevention.append(line)

    status_value = trace.status.value
    if llm_result and llm_result.get("status") in {"success", "partial", "failed"} and status_value == "unknown":
        status_value = llm_result["status"]
    # A run that failed somewhere and still produced its result is not a plain
    # "success": the incident happened. It gets its own status so History and
    # Analytics agree.
    if status_value == "success" and categories and _was_recovered(trace, failed_index):
        status_value = Status.RECOVERED.value

    root_cause_step = next((s.to_dict() for s in trace.steps if s.index == failed_index), None)

    return {
        "engine_version": ENGINE_VERSION,
        "status": status_value,
        "severity": sev.severity.value,
        "severity_detail": sev.to_dict(),
        "categories": categories,
        "primary_category": primary,
        "primary_label": FAILURE_LABELS.get(primary) if primary else None,
        # The one-line title of the detected failure family ("An operation
        # exceeded its time limit"), stored so History and the detail header
        # do not have to recompute it from the category list.
        "primary_title": next((c["title"] for c in categories if c.get("is_primary")), None),
        "failed_step_index": failed_index,
        "failed_step": root_cause_step,
        "root_cause_step": root_cause_step,
        "causal_chain": [
            {
                "step_index": n.step.index,
                "step_name": n.step.name,
                "error_type": n.error_type,
                "message": as_text(n.message, 400),
                "role": "root_cause" if n.is_root else "downstream",
                "caused_by": n.caused_by,
            }
            for n in causal_nodes
        ],
        "root_cause": root_cause,
        "what_happened": what_happened,
        "impact": impact,
        "recoverable": recoverable,
        "confidence": confidence,
        "confidence_factors": confidence_factors,
        "confidence_note": (
            "Confidence is the sum of the contributions listed above, each tied to a specific evidence row. "
            "It describes how well the evidence supports the diagnosis; it is not a calibrated probability, "
            "and it is separate from severity, which describes impact."
        ),
        "evidence": evidence,
        "recommendations": recs,
        "prevention": prevention,
        "timeline": _timeline(trace, failed_index),
        # Concrete, trace-filled code for each fix. Rule-based and always
        # available; an LLM may refine it later, but nothing depends on that.
        "fixes": snippets_mod.fixes_for(
            recs,
            trace.to_dict(),
            failed_index,
            [c["category"] for c in categories],
        ),
        "llm_used": bool(llm_result),
        "llm_model": settings.llm_model if llm_result else None,
        "llm_error": llm_error,
        "llm_prompt": llm_prompt,
        "llm_raw_response": llm_raw,
        "detection_notes": detection_notes,
        "ml": {
            "used": ml_used,
            "prediction": prediction.label if prediction else None,
            "confidence": round(prediction.confidence, 3) if prediction else None,
            "top": prediction.top if prediction else [],
            "agrees_with_primary": ml_agrees,
        },
    }


def _was_recovered(trace: NormalizedTrace, failed_index: int | None) -> bool:
    """True when the run hit a failure and then completed anyway.

    Either the trace recorded a retry/recovery step after the failure, or a
    step failed while the execution still ended successfully.
    """
    if failed_index is None:
        return False
    after = trace.steps[failed_index + 1 :]
    if any(step.type is StepType.RETRY for step in after):
        return True
    return any(
        step.status is Status.FAILED and step.type is not StepType.ERROR for step in trace.steps
    )


def _rule_root_cause(trace: NormalizedTrace, category: dict[str, Any], failed_index: int | None) -> str:
    cat = category["category"]
    step = next((s for s in trace.steps if s.index == failed_index), None)
    where = f"step {failed_index} ('{step.name}')" if step else "the execution"
    detail = as_text(category["evidence"][0]["quote"] if category.get("evidence") else "", 240)
    templates = {
        FailureCategory.TIMEOUT.value: f"An operation at {where} did not complete within its time limit. Evidence: {detail}",
        FailureCategory.API.value: f"The provider API rejected or failed the request at {where}. Evidence: {detail}",
        FailureCategory.TOOL_EXECUTION.value: f"A tool call at {where} failed to execute successfully. Evidence: {detail}",
        FailureCategory.INFINITE_LOOP.value: "The agent repeated the same tool call with identical arguments, indicating the loop had no effective stopping condition.",
        FailureCategory.MEMORY_CONTEXT.value: f"The model ran out of usable context at {where}. Evidence: {detail}",
        FailureCategory.SAFETY.value: f"A policy control blocked or refused the action at {where}. Evidence: {detail}",
        FailureCategory.DATABASE.value: f"The database layer failed at {where}. Evidence: {detail}",
        FailureCategory.AUTH.value: f"A credential or permission was rejected at {where}. Evidence: {detail}",
        FailureCategory.PLANNING.value: f"The agent's plan was invalid or could not be executed at {where}. Evidence: {detail}",
        FailureCategory.INCORRECT_OUTPUT.value: f"The output produced at {where} did not meet the required format or content. Evidence: {detail}",
        FailureCategory.HALLUCINATION.value: "The final answer contains claims not supported by any tool result or retrieved context in this trace.",
        FailureCategory.REASONING.value: "The final answer is inconsistent with the information the agent collected during execution.",
    }
    return templates.get(cat, "The most probable cause is described by the primary detection above.")


def _rule_impact(trace: NormalizedTrace, category: dict[str, Any]) -> str:
    if trace.status.value == "success":
        return "The task completed despite this failure; the impact is on latency and cost, not on the result."
    if trace.status.value == "partial":
        return "Part of the task completed, so the result is incomplete and needs verification before use."
    return "The task did not complete, so the user received no usable result from this run."
