"""Phase 3 tests: the merged timeline and the code fixes behind the diagnosis.

Two contracts are pinned here, because the execution detail page renders
straight from them and a quiet regression would show as a blank or misleading
page rather than an error:

1. ``build_timeline`` folds steps, causal chain and evidence into one ordered
   list that names the failing step, redacts raw IO, and collapses long traces.
2. ``fixes_for`` produces a concrete, runnable-looking snippet from the trace
   alone — no LLM — filled with the real tool name, timeout and step number.
"""

from __future__ import annotations

import json

import pytest

from app.api.timeline import build_timeline
from app.core.redact import redact_secret
from app.engine.analyzer import diagnose
from app.engine.snippets import build_snippet, extract_facts, fixes_for
from app.ingest import ingest_text
from app.samples.demos import get_demo

SECRET = "gsk_ABCDEFGHIJKLMNOPQRSTUVWX0123456789"


def _diagnose(payload: dict) -> tuple[dict, dict]:
    trace, _warnings, _fmt = ingest_text(json.dumps(payload))
    return trace.to_dict(), diagnose(trace, use_llm=False)


def _trace(steps: list[dict], **extra) -> dict:
    return {"execution_id": "tl-1", "agent_name": "Timeline Agent", "framework": "langgraph", "steps": steps, **extra}


# --------------------------------------------------------------------------- #
# Merged timeline
# --------------------------------------------------------------------------- #

def test_single_step_trace_marks_its_only_step_as_the_root_cause():
    trace, analysis = _diagnose(_trace([{"type": "llm", "status": "failed", "error": "401 Unauthorized: invalid api key"}]))
    timeline = build_timeline(trace, analysis)
    assert timeline["total_steps"] == 1
    assert timeline["collapsed_by_default"] is False
    item = timeline["items"][0]
    assert item["is_root_cause"] is True
    assert item["number"] == 1
    assert "401" in item["detail"]["error"]


def test_two_step_trace_points_at_the_failing_tool_step():
    trace, analysis = _diagnose(
        _trace(
            [
                {"type": "task", "input": "fetch the page", "status": "success"},
                {"type": "tool_call", "tool_name": "fetch_page", "status": "failed", "error": "Read timeout=30 while fetching"},
            ]
        )
    )
    timeline = build_timeline(trace, analysis)
    assert [item["kind"] for item in timeline["items"]] == ["task", "tool"]
    assert timeline["items"][1]["is_root_cause"] is True
    assert timeline["items"][0]["is_root_cause"] is False
    assert timeline["failed_step_number"] == 2


def test_long_trace_collapses_but_keeps_the_failure_visible():
    steps = [
        {
            "type": "tool_call",
            "tool_name": "search",
            "status": "failed" if i == 17 else "success",
            "input": {"q": f"query {i}"},
            "output": f"result {i}",
            "error": "HTTP 429 Too Many Requests" if i == 17 else None,
        }
        for i in range(20)
    ]
    trace, analysis = _diagnose(_trace(steps))
    timeline = build_timeline(trace, analysis)
    assert timeline["total_steps"] == 20
    assert timeline["collapsed_by_default"] is True
    assert timeline["failed_step_number"] == 18
    assert timeline["items"][17]["is_root_cause"] is True
    assert 17 in timeline["notable_indices"]


def test_step_kinds_are_parsed_and_never_show_an_unknown_tag():
    trace, analysis = _diagnose(
        _trace(
            [
                {"type": "task", "status": "success"},
                {"type": "planning", "status": "success"},
                {"type": "llm", "status": "success"},
                {"type": "tool_call", "tool_name": "search", "status": "success"},
                {"type": "retry", "status": "failed", "error": "retrying search"},
                {"type": "error", "status": "failed", "error": "graph aborted"},
            ]
        )
    )
    timeline = build_timeline(trace, analysis)
    assert [item["kind"] for item in timeline["items"]] == ["task", "plan", "llm", "tool", "retry", "error"]
    for item in timeline["items"]:
        assert item["title"].strip()
        assert item["title"].lower() != "unknown"
        assert item["kind"] != "other"


def test_timeline_evidence_is_attached_to_its_own_step():
    trace, analysis = _diagnose(_trace(get_demo("tool-timeout")["text"] and json.loads(get_demo("tool-timeout")["text"])))
    timeline = build_timeline(trace, analysis)
    failing = next(item for item in timeline["items"] if item["is_root_cause"])
    assert failing["evidence"], "the failing step must carry the evidence that convicted it"
    every_row = [row for item in timeline["items"] for row in item["evidence"]]
    assert len(every_row) == len(analysis["evidence"]), "no evidence row may be dropped"


def test_timeline_redacts_secrets_in_raw_input_and_output():
    trace, analysis = _diagnose(
        _trace(
            [
                {
                    "type": "tool_call",
                    "tool_name": "call_api",
                    "status": "failed",
                    "input": {"headers": {"Authorization": f"Bearer {SECRET}"}},
                    "output": f"upstream said {SECRET}",
                    "error": f"401 Unauthorized for key {SECRET}",
                }
            ]
        )
    )
    timeline = build_timeline(trace, analysis)
    blob = json.dumps(timeline)
    assert SECRET not in blob
    assert "REDACTED" in blob.upper()


def test_timeline_survives_an_analysis_with_no_evidence():
    trace, _analysis = _diagnose(_trace([{"type": "task", "status": "success"}]))
    timeline = build_timeline(trace, None)
    assert timeline["items"][0]["evidence"] == []
    assert timeline["failed_step_index"] is None


# --------------------------------------------------------------------------- #
# Code fixes
# --------------------------------------------------------------------------- #

def test_facts_are_read_from_the_failing_step():
    trace, analysis = _diagnose(json.loads(get_demo("tool-timeout")["text"]))
    facts = extract_facts(trace, analysis)
    assert facts["tool"] == "fetch_page"
    assert facts["timeout"] == 30
    assert facts["step"] == analysis["failed_step_index"]


def test_every_demo_gets_at_least_one_concrete_fix_without_an_llm():
    for demo_id in ("tool-timeout", "infinite-loop", "context-overflow", "rate-limit-recovered"):
        trace, analysis = _diagnose(json.loads(get_demo(demo_id)["text"]))
        fixes = analysis["fixes"]
        assert fixes, f"{demo_id} produced no code fix"
        for fix in fixes:
            assert fix["title"] and fix["caption"]
            assert fix["snippet"]["code"].strip()
            assert fix["snippet"]["language"]
            assert fix["source"] == "template"


def test_timeout_fix_uses_the_real_tool_name_and_a_longer_timeout():
    trace, analysis = _diagnose(json.loads(get_demo("tool-timeout")["text"]))
    fix = analysis["fixes"][0]
    code = fix["snippet"]["code"]
    assert "fetch_page" in code
    assert "timeout=60" in code  # 30s observed -> 60s suggested
    assert fix["applies_to"]


def test_rate_limit_fix_honours_the_retry_after_header():
    trace, analysis = _diagnose(
        _trace([{"type": "tool_call", "tool_name": "search", "status": "failed", "error": "HTTP 429 Too Many Requests, retry after 12s"}])
    )
    code = analysis["fixes"][0]["snippet"]["code"]
    assert "429" in code
    assert "12" in code


def test_infinite_loop_fix_names_the_repeated_tool():
    trace, analysis = _diagnose(json.loads(get_demo("infinite-loop")["text"]))
    code = " ".join(fix["snippet"]["code"] for fix in analysis["fixes"])
    assert "http_get" in code


def test_fixes_are_redacted_before_they_are_returned():
    trace, analysis = _diagnose(
        _trace([{"type": "tool_call", "tool_name": "call_api", "status": "failed", "error": f"401 Unauthorized: key {SECRET} is invalid"}])
    )
    blob = json.dumps(analysis["fixes"])
    assert SECRET not in blob


def test_a_trace_with_no_failure_family_gets_no_invented_fix():
    trace, analysis = _diagnose(_trace([{"type": "task", "status": "success"}, {"type": "output", "status": "success"}]))
    assert analysis["fixes"] == []


def test_snippet_builder_is_safe_for_an_unknown_category():
    facts = {"tool": None, "step": None, "timeout": None}
    assert build_snippet("not_a_category", facts) is None


def test_fix_snippets_are_text_only():
    """A fix is shown and copied; nothing in it is ever executed."""
    trace, analysis = _diagnose(json.loads(get_demo("tool-timeout")["text"]))
    for fix in analysis["fixes"]:
        assert "eval(" not in fix["snippet"]["code"]
        assert "exec(" not in fix["snippet"]["code"]


def test_redaction_helper_leaves_ordinary_text_alone():
    text = "Read timeout=30 while fetching https://example.com/pricing"
    assert redact_secret(text, None)[0] == text


@pytest.mark.parametrize("demo_id", ["tool-timeout", "infinite-loop"])
def test_api_returns_fixes_and_timeline_for_a_demo(demo_id):
    """The two payloads the detail page renders come back together."""
    trace, analysis = _diagnose(json.loads(get_demo(demo_id)["text"]))
    timeline = build_timeline(trace, analysis)
    assert analysis["fixes"] and timeline["items"]
    assert any(item["is_root_cause"] for item in timeline["items"])
