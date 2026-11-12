"""Phase 4 tests: the step-by-step diff behind the compare view.

The compare page is only as good as its alignment, so these pin the four cases
that actually happen: identical runs, runs that diverge at the very first step,
runs of different lengths, and a three-way comparison. Everything here is
rule-based — no LLM is stubbed or needed.
"""

from __future__ import annotations

import json

import pytest

from app.api.diff import MAX_RUNS, build_comparison
from tests.test_workflow import client  # noqa: F401 - pytest fixture
from app.samples.demos import get_demo

SECRET = "gsk_ABCDEFGHIJKLMNOPQRSTUVWX0123456789"


def _step(
    index: int,
    *,
    kind: str = "tool_call",
    tool: str = "search",
    status: str = "success",
    duration: int | None = 100,
    args: object = None,
    tokens: int | None = None,
    error: str | None = None,
) -> dict:
    return {
        "index": index,
        "type": kind,
        "tool_name": tool if kind == "tool_call" else None,
        "status": status,
        "duration_ms": duration,
        "tool_arguments": args if args is not None else {"q": f"query {index}"},
        "tokens": tokens,
        "error": error,
    }


def _run(pk: int, name: str, steps: list[dict], **extra) -> dict:
    return {"id": pk, "name": name, "execution_id": f"exec-{pk}", "status": "failed", "steps": steps, **extra}


def _clone(steps: list[dict]) -> list[dict]:
    return json.loads(json.dumps(steps))


BASE_STEPS = [_step(0), _step(1), _step(2)]


# --------------------------------------------------------------------------- #
# The four shapes
# --------------------------------------------------------------------------- #

def test_identical_runs_report_no_differences():
    left = _run(1, "Agent A", _clone(BASE_STEPS))
    right = _run(2, "Agent A (retry)", _clone(BASE_STEPS))
    diff = build_comparison([left, right])
    assert diff["identical"] is True
    assert diff["first_divergence"] is None
    assert diff["divergence_count"] == 0
    assert diff["summary"].startswith("No differences found")
    assert all(not row["is_divergence"] for row in diff["rows"])


def test_a_difference_at_step_one_is_the_first_divergence():
    left = _run(1, "Agent A", _clone(BASE_STEPS))
    right = _run(2, "Agent A", [_step(0, status="failed", duration=900, error="HTTP 429")] + _clone(BASE_STEPS[1:]))
    diff = build_comparison([left, right])
    assert diff["identical"] is False
    assert diff["first_divergence"]["number"] == 1
    assert "status" in diff["first_divergence"]["differing_fields"]
    assert diff["summary"].startswith("Step 1")
    # The later steps match, so only the first row diverges.
    assert diff["divergence_count"] == 1


def test_a_later_difference_does_not_flag_the_earlier_steps():
    left = _run(1, "Agent A", _clone(BASE_STEPS))
    right = _run(2, "Agent A", _clone(BASE_STEPS[:2]) + [_step(2, tool="fetch_page")])
    diff = build_comparison([left, right])
    assert [row["is_divergence"] for row in diff["rows"]] == [False, False, True]
    assert diff["first_divergence"]["number"] == 3
    assert "tool" in diff["first_divergence"]["differing_fields"]


def test_runs_of_different_lengths_are_padded_not_cropped():
    long_run = _run(1, "Long", _clone(BASE_STEPS))
    short_run = _run(2, "Short", _clone(BASE_STEPS[:1]))
    diff = build_comparison([long_run, short_run])
    assert diff["total_steps"] == 3
    assert len(diff["rows"]) == 3
    padded = diff["rows"][1]["cells"][1]
    assert padded["present"] is False
    assert padded["label"] == "no step"
    assert diff["first_divergence"]["number"] == 2
    assert "not the same length" in diff["summary"]
    assert "Short" in diff["summary"]


def test_three_way_comparison_uses_the_first_step_any_run_diverges_at():
    a = _run(1, "A", _clone(BASE_STEPS))
    b = _run(2, "B", _clone(BASE_STEPS))
    c = _run(3, "C", [_step(0), _step(1, status="failed"), _step(2)])
    diff = build_comparison([a, b, c])
    assert len(diff["runs"]) == 3
    assert len(diff["rows"][1]["cells"]) == 3
    assert diff["first_divergence"]["number"] == 2
    assert diff["first_divergence"]["differing_fields"] == ["status"]
    assert "failed" in diff["summary"]
    # A and B are still identical to each other at that step.
    assert diff["rows"][1]["cells"][0]["status"] == diff["rows"][1]["cells"][1]["status"]


def test_more_than_three_runs_are_refused_or_truncated():
    runs = [_run(i, f"Run {i}", _clone(BASE_STEPS)) for i in range(1, 6)]
    diff = build_comparison(runs)
    assert len(diff["runs"]) == MAX_RUNS == 3


def test_a_single_run_cannot_be_compared():
    with pytest.raises(ValueError):
        build_comparison([_run(1, "Only", _clone(BASE_STEPS))])


# --------------------------------------------------------------------------- #
# What the diff says it is comparing
# --------------------------------------------------------------------------- #

def test_latency_and_token_differences_are_reported():
    left = _run(1, "A", [_step(0, duration=100, tokens=900)])
    right = _run(2, "B", [_step(0, duration=1400, tokens=2100)])
    diff = build_comparison([left, right])
    fields = diff["first_divergence"]["differing_fields"]
    assert "duration_ms" in fields
    assert "tokens" in fields
    assert "1400" in diff["summary"]  # the slow run is named with its number


def test_argument_differences_quote_the_inputs():
    left = _run(1, "A", [_step(0, args={"url": "https://example.com/pricing"})])
    right = _run(2, "B", [_step(0, args={"url": "https://example.com/docs"})])
    diff = build_comparison([left, right])
    assert diff["first_divergence"]["differing_fields"] == ["args"]
    assert "example.com/pricing" in diff["summary"]
    assert "example.com/docs" in diff["summary"]


def test_a_task_step_is_compared_on_what_it_was_asked_to_do():
    left = _run(1, "A", [{"index": 0, "type": "task", "status": "success", "input": "summarise RAG papers"}])
    right = _run(2, "B", [{"index": 0, "type": "task", "status": "success", "input": "find the pro plan price"}])
    diff = build_comparison([left, right])
    assert "summarise RAG papers" in diff["summary"]
    assert "find the pro plan price" in diff["summary"]


def test_compared_text_is_redacted():
    left = _run(1, "A", [_step(0, args={"Authorization": f"Bearer {SECRET}"})])
    right = _run(2, "B", [_step(0, args={"Authorization": "Bearer other"})])
    diff = build_comparison([left, right])
    assert SECRET not in json.dumps(diff)
    assert "REDACTED" in json.dumps(diff).upper()


def test_an_error_message_is_compared_and_redacted():
    left = _run(1, "A", [_step(0, status="failed", error="Read timeout=30")])
    right = _run(2, "B", [_step(0, status="failed", error="Read timeout=30")])
    assert build_comparison([left, right])["identical"] is True

    noisy = _run(3, "C", [_step(0, status="failed", error=f"401 Unauthorized for {SECRET}")])
    diff = build_comparison([left, noisy])
    assert SECRET not in json.dumps(diff)


def test_empty_and_single_step_runs_do_not_crash():
    empty = _run(1, "Empty", [])
    one = _run(2, "One", [_step(0)])
    diff = build_comparison([empty, one])
    assert diff["total_steps"] == 1
    assert diff["rows"][0]["cells"][0]["present"] is False
    assert diff["rows"][0]["cells"][1]["present"] is True


def test_the_summary_says_it_is_rule_based():
    diff = build_comparison([_run(1, "A", _clone(BASE_STEPS)), _run(2, "B", _clone(BASE_STEPS))])
    assert diff["summary_source"] == "rules"
    assert diff["llm_note"] is None


# --------------------------------------------------------------------------- #
# Real traces
# --------------------------------------------------------------------------- #

def test_two_demo_traces_compare_step_by_step():
    left = json.loads(get_demo("tool-timeout")["text"])
    right = json.loads(get_demo("rate-limit-recovered")["text"])
    diff = build_comparison(
        [
            _run(1, "Timeout demo", left["steps"]),
            _run(2, "Rate limit demo", right["steps"]),
        ]
    )
    assert diff["rows"]
    assert diff["identical"] is False
    assert diff["first_divergence"]["number"] >= 1
    assert diff["summary"]


def test_identical_demo_runs_are_identical():
    trace = json.loads(get_demo("tool-timeout")["text"])
    diff = build_comparison([_run(1, "A", _clone(trace["steps"])), _run(2, "B", _clone(trace["steps"]))])
    assert diff["identical"] is True


# --------------------------------------------------------------------------- #
# The /api/compare route
# --------------------------------------------------------------------------- #

TRACE_A = {
    "execution_id": "cmp-a",
    "agent_name": "Compare Agent",
    "status": "failed",
    "steps": [
        {"type": "task", "input": "fetch the price", "status": "success"},
        {"type": "tool_call", "tool_name": "http_get", "status": "success", "tool_arguments": {"url": "https://example.com"}},
    ],
}
TRACE_B = {
    "execution_id": "cmp-b",
    "agent_name": "Compare Agent",
    "status": "failed",
    "steps": [
        {"type": "task", "input": "fetch the price", "status": "success"},
        {"type": "tool_call", "tool_name": "http_get", "status": "failed", "error": "504 Gateway Timeout after 30s"},
    ],
}


def _two_stored(client) -> list[int]:
    for payload in (TRACE_A, TRACE_B):
        client.post("/api/ingest/paste", json={"text": json.dumps(payload), "use_llm": False})
    ids = [execution["id"] for execution in client.get("/api/executions?limit=2").json()["items"]]
    return ids


def test_compare_route_returns_the_diff_and_respects_the_run_limit(client):
    ids = _two_stored(client)
    response = client.get(f"/api/compare?ids={','.join(map(str, ids[:2]))}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "comparison" in body
    assert len(body["comparison"]["runs"]) == 2
    assert body["comparison"]["summary"]
    assert len(body["comparison"]["rows"]) == body["comparison"]["total_steps"]


def test_compare_route_needs_two_executions(client):
    assert client.get("/api/compare?ids=1").status_code == 422
    assert client.get("/api/compare?ids=abc,def").status_code == 422


def test_compare_route_never_returns_a_provider_key(client):
    """The compare payload echoes configuration, so it must be redacted too."""
    from app.core.config import settings

    secret = "gsk_COMPARE123456789012345678901234"
    original = settings.llm_api_key
    try:
        settings.llm_api_key = secret
        ids = _two_stored(client)
        text = client.get(f"/api/compare?ids={','.join(map(str, ids))}").text
        assert secret not in text
    finally:
        settings.llm_api_key = original


# --------------------------------------------------------------------------- #
# Phase-4 review fixes: incomparable runs, and input nobody recorded
# --------------------------------------------------------------------------- #

def test_different_agents_are_flagged_and_lead_with_the_outcome():
    left = _run(
        1,
        "Research",
        [{"index": 0, "type": "task", "status": "success", "input": "find graphene papers"}],
        agent_name="Research",
        status="failed",
        categories=["timeout_failure"],
        root_cause="timeout_failure",
    )
    right = _run(
        2,
        "Billing",
        [{"index": 0, "type": "task", "status": "success", "input": "summarise this invoice"}],
        agent_name="Billing",
        status="success",
    )
    diff = build_comparison([left, right])
    assert diff["comparable"] is False
    assert diff["comparison_banner"] == "Different agents or tasks - the diff is limited"
    assert diff["same_agent"] is False
    # The outcome difference comes first, not the first step's input.
    assert diff["summary"].startswith("Research")
    # Lowercase mid-sentence clause, quoted input: a sentence, not a template.
    assert "Research failed because timeout failure" in diff["summary"]
    assert "Billing completed" in diff["summary"]
    assert diff["outcomes"] == ["Research failed because timeout failure", "Billing completed"]


def test_the_same_task_with_different_agents_is_still_flagged():
    steps = [{"index": 0, "type": "task", "status": "success", "input": "find graphene papers"}]
    diff = build_comparison(
        [_run(1, "A", steps, agent_name="Research"), _run(2, "B", steps, agent_name="Billing")]
    )
    assert diff["same_task"] is True
    assert diff["same_agent"] is False
    assert diff["comparison_banner"] is not None


def test_the_same_agent_and_task_is_not_flagged():
    steps = [
        {"index": 0, "type": "task", "status": "success", "input": "find graphene papers"},
        _step(1, status="failed", error="Read timeout=30"),
    ]
    diff = build_comparison([_run(1, "A", steps, agent_name="Research"), _run(2, "B", steps, agent_name="Research")])
    assert diff["comparable"] is True
    assert diff["comparison_banner"] is None
    assert diff["summary"].startswith("No differences found")


def test_a_different_task_is_flagged_even_for_one_agent():
    left = _run(1, "A", [{"index": 0, "type": "task", "status": "success", "input": "find graphene papers"}], agent_name="A")
    right = _run(2, "A", [{"index": 0, "type": "task", "status": "success", "input": "summarise the invoice"}], agent_name="A")
    diff = build_comparison([left, right])
    assert diff["same_task"] is False
    assert diff["comparable"] is False
    assert 'the input at step 1 differed ("find graphene papers" vs "summarise the invoice")' in diff["summary"]


def test_an_unrecorded_input_is_shown_as_a_dash_and_is_not_a_difference():
    left = _run(1, "A", [_step(0, args={"q": "x"})])
    right_steps = [_step(0)]
    right_steps[0].pop("tool_arguments")
    right = _run(2, "B", right_steps)
    diff = build_comparison([left, right])
    row = diff["rows"][0]
    assert [cell["args"] for cell in row["cells"]] == ['{"q": "x"}', "-"]
    assert "args" in row["differing_fields"]  # one run did record an input


def test_two_runs_with_no_recorded_input_are_identical():
    steps = [{key: value for key, value in _step(0).items() if key != "tool_arguments"}]
    diff = build_comparison([_run(1, "A", steps), _run(2, "B", list(steps))])
    assert diff["identical"] is True
    assert [cell["args"] for cell in diff["rows"][0]["cells"]] == ["-", "-"]
    assert diff["first_divergence"] is None


def test_a_missing_input_never_becomes_the_first_divergence():
    left = [_step(0, status="failed", error="Read timeout=30"), _step(1)]
    right_steps = [_step(0, status="success"), _step(1)]
    right_steps[1].pop("tool_arguments")
    diff = build_comparison([_run(1, "A", left), _run(2, "B", right_steps)])
    assert diff["first_divergence"]["number"] == 1
    assert diff["first_divergence"]["differing_fields"] == ["status"]
