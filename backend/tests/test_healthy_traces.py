"""Healthy traces: success, recovered and partial runs must not read as failures.

Every stored run in the dev database was a failure, so the dashboard showed a
100% failure rate. Before touching any number, these pin what the analyzer
actually says about three runs that went right — and name the false positives
that were found.
"""

from __future__ import annotations

import json

from app.engine.analyzer import diagnose, recoveries
from app.engine.snippets import build_snippet, extract_facts
from app.ingest import ingest_text
from app.samples.demos import get_demo


def _normalize(trace: dict):
    normalized, _warnings, _fmt = ingest_text(json.dumps(trace))
    return normalized


def _diagnose(trace: dict) -> dict:
    return diagnose(_normalize(trace), use_llm=False)


# --------------------------------------------------------------------------- #
# The three healthy shapes
# --------------------------------------------------------------------------- #

CLEAN = {
    "execution_id": "healthy-clean-1",
    "agent_name": "Support Agent",
    "framework": "langgraph",
    "status": "success",
    "started_at": "2024-05-02T09:00:00Z",
    "ended_at": "2024-05-02T09:00:09Z",
    "task_description": "Answer the customer question about the refund window.",
    "steps": [
        {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-02T09:00:00Z"},
        {"type": "llm", "name": "plan", "status": "success", "timestamp": "2024-05-02T09:00:01Z", "duration_ms": 800,
         "output": "look up the refund policy"},
        {"type": "tool_call", "name": "search_kb", "tool_name": "search_kb", "status": "success",
         "timestamp": "2024-05-02T09:00:03Z", "duration_ms": 1200, "tool_arguments": {"query": "refund window"},
         "tool_result": "Refunds are accepted within 30 days of purchase."},
        {"type": "llm", "name": "compose answer", "status": "success", "timestamp": "2024-05-02T09:00:06Z",
         "duration_ms": 2400, "output": "Within 30 days."},
        {"type": "output", "name": "final answer", "status": "success", "timestamp": "2024-05-02T09:00:09Z",
         "output": "Refunds are accepted within 30 days of purchase."},
    ],
    "output": "Refunds are accepted within 30 days of purchase.",
}

RECOVERED = {
    "execution_id": "healthy-recovered-1",
    "agent_name": "Scraper Agent",
    "framework": "langgraph",
    "status": "success",
    "started_at": "2024-05-02T11:00:00Z",
    "ended_at": "2024-05-02T11:00:40Z",
    "task_description": "Fetch the changelog page and summarise it.",
    "steps": [
        {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-02T11:00:00Z"},
        {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "failed",
         "timestamp": "2024-05-02T11:00:10Z", "duration_ms": 10000,
         "tool_arguments": {"url": "https://example.com/changelog"},
         "error": "httpx.ReadTimeout: timed out (read timeout=10)"},
        {"type": "retry", "name": "retry fetch_page (attempt 2)", "status": "success",
         "timestamp": "2024-05-02T11:00:30Z", "duration_ms": 1800,
         "tool_arguments": {"url": "https://example.com/changelog"}, "tool_result": "200 OK"},
        {"type": "llm", "name": "summarise", "status": "success", "timestamp": "2024-05-02T11:00:38Z",
         "duration_ms": 2400, "output": "Three changes."},
        {"type": "output", "name": "final answer", "status": "success", "timestamp": "2024-05-02T11:00:40Z",
         "output": "Three changes shipped this week."},
    ],
    "output": "Three changes shipped this week.",
}

PARTIAL = {
    "execution_id": "healthy-partial-1",
    "agent_name": "Report Agent",
    "framework": "langgraph",
    "status": "partial",
    "started_at": "2024-05-02T10:00:00Z",
    "ended_at": "2024-05-02T10:01:20Z",
    "task_description": "Summarise Q1 and Q2 revenue from two exports.",
    "steps": [
        {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-02T10:00:00Z"},
        {"type": "tool_call", "name": "read_csv", "tool_name": "read_csv", "status": "success",
         "timestamp": "2024-05-02T10:00:03Z", "duration_ms": 900, "tool_arguments": {"path": "q1.csv"},
         "tool_result": "Q1 revenue 120k"},
        {"type": "tool_call", "name": "read_csv", "tool_name": "read_csv", "status": "failed",
         "timestamp": "2024-05-02T10:00:20Z", "duration_ms": 900, "tool_arguments": {"path": "q2.csv"},
         "error": "FileNotFoundError: q2.csv not found"},
        {"type": "llm", "name": "summarise available data", "status": "success",
         "timestamp": "2024-05-02T10:01:10Z", "duration_ms": 4000, "output": "Q1 only."},
        {"type": "output", "name": "partial answer", "status": "success", "timestamp": "2024-05-02T10:01:20Z",
         "output": "Q1 revenue was 120k. Q2 export could not be read."},
    ],
    "output": "Q1 revenue was 120k. Q2 export could not be read.",
}


# --------------------------------------------------------------------------- #
# (a) a clean run: success, and no failure category at all
# --------------------------------------------------------------------------- #

def test_a_clean_run_is_success_with_no_failure_category():
    result = _diagnose(CLEAN)
    assert result["status"] == "success"
    assert result["categories"] == [], f"false positive: {[c['category'] for c in result['categories']]}"
    assert result["severity"] == "NONE"


def test_a_clean_run_has_no_failure_evidence():
    result = _diagnose(CLEAN)
    assert result["evidence"] == []
    assert "no failure" in result["root_cause"].lower()


# --------------------------------------------------------------------------- #
# (b) a timeout that is retried and succeeds: recovered
# --------------------------------------------------------------------------- #

def test_a_retried_timeout_is_recovered_not_failed():
    result = _diagnose(RECOVERED)
    assert result["status"] == "recovered"
    assert result["recoverable"] == "yes"


def test_the_recovered_run_names_the_transient_cause_once():
    result = _diagnose(RECOVERED)
    categories = [c["category"] for c in result["categories"]]
    assert categories[0] == "timeout_failure"
    # The tool that timed out is the same tool the retry used: not two causes.
    assert categories.count("timeout_failure") == 1
    assert "retried" in " ".join(result["detection_notes"]).lower() or result["status"] == "recovered"


# --------------------------------------------------------------------------- #
# (c) a run that partly succeeded: partial
# --------------------------------------------------------------------------- #

def test_a_partial_run_is_partial():
    result = _diagnose(PARTIAL)
    assert result["status"] == "partial"


def test_a_partial_run_still_reports_the_cause_that_limited_it():
    result = _diagnose(PARTIAL)
    assert [c["category"] for c in result["categories"]] == ["tool_execution_failure"]
    # The run's own severity is lowered by the partial impact and the recovery
    # evidence; the per-category impact stays at its base weight.
    assert result["severity"] in {"LOW", "MODERATE"}


# --------------------------------------------------------------------------- #
# The demo that was missing from the database
# --------------------------------------------------------------------------- #

def test_the_recovered_demo_is_recovered():
    normalized, _warnings, _fmt = ingest_text(get_demo("rate-limit-recovered")["text"])
    result = diagnose(normalized, use_llm=False)
    assert result["status"] == "recovered"


def test_the_clean_demo_is_success_and_the_partial_demo_is_partial():
    for demo_id, expected in (("clean-success", "success"), ("partial-result", "partial")):
        normalized, _warnings, _fmt = ingest_text(get_demo(demo_id)["text"])
        result = diagnose(normalized, use_llm=False)
        assert result["status"] == expected, demo_id


def test_a_successful_run_gets_no_timeout_snippet():
    """A healthy run must not be handed a fix for a failure it never had."""
    result = _diagnose(CLEAN)
    assert result["fixes"] == []


def test_a_recovered_run_keeps_the_shared_http_library():
    """The timeout and the retry on one page use the same library as the trace."""
    normalized, _warnings, _fmt = ingest_text(json.dumps(RECOVERED))
    result = diagnose(normalized, use_llm=False)
    codes = [fix["snippet"]["code"] for fix in result["fixes"]]
    # A healthy-ish run still may get fixes for the retried step; if it does,
    # they must agree with the trace's own library.
    if not codes:
        return
    # The trace named httpx.ReadTimeout, so no snippet may import requests for
    # the same failure: one page, one HTTP library.
    for code in codes:
        assert "import requests" not in code


# --------------------------------------------------------------------------- #
# The recovery heuristic is narrow: only the same tool, later, with the same
# arguments, counts as recovered. Anything else stays a failure.
# --------------------------------------------------------------------------- #

def _failed_tool_then(*later_steps: dict) -> dict:
    return {
        "execution_id": "recovery-heuristic",
        "agent_name": "Scraper Agent",
        # The run itself failed: the trace did not finish. Whether the engine
        # calls it "recovered" is exactly what these tests are about.
        "status": "failed",
        "task_description": "Fetch the changelog and summarise it.",
        "steps": [
            {"type": "task", "name": "task received", "status": "success"},
            {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "failed",
             "tool_arguments": {"url": "https://example.com/a"}, "error": "httpx.ReadTimeout: timed out"},
            *later_steps,
            {"type": "output", "name": "final answer", "status": "success", "output": "done"},
        ],
        "output": "done",
    }


def test_a_different_tool_succeeding_later_is_not_a_recovery():
    """The agent moved on to another tool: the failure was never made good."""
    trace = _failed_tool_then(
        {"type": "tool_call", "name": "search_web", "tool_name": "search_web", "status": "success",
         "tool_arguments": {"q": "changelog"}},
    )
    result = _diagnose(trace)
    assert result["status"] == "failed"
    assert recoveries(_normalize(trace)) == []


def test_a_retry_that_calls_a_different_tool_is_not_a_recovery():
    trace = _failed_tool_then(
        {"type": "retry", "name": "retry with the cache", "tool_name": "read_cache", "status": "success",
         "tool_arguments": {"key": "changelog"}},
    )
    result = _diagnose(trace)
    assert result["status"] == "failed"
    assert recoveries(_normalize(trace)) == []


def test_a_later_success_with_different_arguments_is_not_a_recovery():
    """Same tool, but it asked for something else: a different call, not a retry."""
    trace = _failed_tool_then(
        {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "success",
         "tool_arguments": {"url": "https://example.com/b"}},
    )
    result = _diagnose(trace)
    assert result["status"] == "failed"
    assert recoveries(_normalize(trace)) == []


def test_the_same_tool_later_with_the_same_arguments_is_a_recovery():
    trace = _failed_tool_then(
        {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "success",
         "tool_arguments": {"url": "https://example.com/a"}},
    )
    result = _diagnose(trace)
    # The status only becomes "recovered" when the run itself finished: a run
    # whose own status is "failed" keeps it, however the retry went.
    assert result["status"] == "failed"
    assert result["categories"][0]["category"] == "timeout_failure"
    found = recoveries(_normalize(trace))
    assert len(found) == 1
    assert found[0]["step_index"] == 1
    assert found[0]["recovered_by_index"] == 2


def test_a_later_success_without_recorded_arguments_still_counts():
    """Nothing to compare is not a disagreement: the tool is the same."""
    trace = _failed_tool_then(
        {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "success"},
    )
    assert len(recoveries(_normalize(trace))) == 1


def test_a_run_that_finished_after_the_same_tool_succeeded_is_recovered():
    trace = _failed_tool_then(
        {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "success",
         "tool_arguments": {"url": "https://example.com/a"}},
    )
    trace["status"] = "success"
    assert _diagnose(trace)["status"] == "recovered"
