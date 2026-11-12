"""Demo traces.

These are clearly-marked demo executions used by the "Load demo trace" action
so the product can be explored without an upload. They are stored with
`is_demo = true` and the UI labels them DEMO. The analysis run on them is a
real analysis of these traces — not a hardcoded result.
"""

from __future__ import annotations

import json
from typing import Any

DEMOS: dict[str, dict[str, Any]] = {
    "tool-timeout": {
        "label": "Research agent — search API timeout",
        "framework": "langchain",
        "trace": {
            "execution_id": "demo-exec-1001",
            "agent_name": "Research Agent",
            "framework": "langchain",
            "status": "failed",
            "started_at": "2024-05-01T10:00:00Z",
            "ended_at": "2024-05-01T10:00:42Z",
            "task_description": "Summarise the three most recent papers on retrieval-augmented generation and cite each one.",
            "expected_output": "A summary with three citations.",
            "steps": [
                {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-01T10:00:00Z",
                 "input": "Summarise the three most recent papers on RAG and cite each one."},
                {"type": "planning", "name": "plan", "status": "success", "timestamp": "2024-05-01T10:00:02Z",
                 "output": "1) search arxiv 2) read abstracts 3) write summary"},
                {"type": "tool_call", "name": "arxiv_search", "tool_name": "arxiv_search", "status": "success",
                 "timestamp": "2024-05-01T10:00:03Z", "duration_ms": 820,
                 "tool_arguments": {"query": "retrieval augmented generation", "limit": 5},
                 "tool_result": "5 results returned"},
                {"type": "tool_call", "name": "fetch_page", "tool_name": "fetch_page", "status": "failed",
                 "timestamp": "2024-05-01T10:00:04Z", "duration_ms": 30000,
                 "tool_arguments": {"url": "https://arxiv.org/abs/2404.12345"},
                 "error": "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='arxiv.org', port=443): Read timed out. (read timeout=30)"},
                {"type": "retry", "name": "retrying fetch_page (attempt 2)", "status": "failed",
                 "timestamp": "2024-05-01T10:00:35Z", "duration_ms": 30000,
                 "error": "ReadTimeout again after 30s"},
                {"type": "error", "name": "execution aborted", "status": "failed", "timestamp": "2024-05-01T10:00:42Z",
                 "error": "ToolException: fetch_page failed after 2 attempts"}
            ],
            "output": None
        },
    },
    "reasoning-contradiction": {
        "label": "Support agent — answer contradicts tool result",
        "framework": "openai",
        "trace": {
            "execution_id": "demo-exec-1002",
            "agent_name": "Support Agent",
            "framework": "openai",
            "status": "failed",
            "started_at": "2024-05-01T11:10:00Z",
            "ended_at": "2024-05-01T11:10:09Z",
            "task_description": "Tell the customer when order ORD-7781 will arrive.",
            "expected_output": "The delivery date from the order system.",
            "steps": [
                {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-01T11:10:00Z",
                 "input": "When will order ORD-7781 arrive?"},
                {"type": "tool_call", "name": "lookup_order", "tool_name": "lookup_order", "status": "success",
                 "timestamp": "2024-05-01T11:10:01Z", "duration_ms": 340,
                 "tool_arguments": {"order_id": "ORD-7781"},
                 "tool_result": {"order_id": "ORD-7781", "status": "shipped", "eta": "2024-05-06", "carrier": "BlueDart"}},
                {"type": "llm", "name": "compose answer", "status": "success", "timestamp": "2024-05-01T11:10:04Z",
                 "output": "Your order ORD-7781 will arrive on 2024-05-12 via BlueDart."},
                {"type": "output", "name": "final answer", "status": "failed", "timestamp": "2024-05-01T11:10:09Z",
                 "output": "Your order ORD-7781 will arrive on 2024-05-12 via BlueDart.",
                 "error": "Output verification failed: the answer states 2024-05-12 but the order system returned eta=2024-05-06."}
            ],
            "output": "Your order ORD-7781 will arrive on 2024-05-12 via BlueDart."
        },
    },
    "infinite-loop": {
        "label": "Web agent — repeated identical tool calls",
        "framework": "langgraph",
        "trace": {
            "execution_id": "demo-exec-1003",
            "agent_name": "Web Scraper Agent",
            "framework": "langgraph",
            "status": "failed",
            "started_at": "2024-05-01T12:00:00Z",
            "ended_at": "2024-05-01T12:02:30Z",
            "task_description": "Find the pricing page of example.com and extract the Pro plan price.",
            "steps": [
                {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-01T12:00:00Z",
                 "input": "Find the Pro plan price on example.com"},
                {"type": "tool_call", "name": "http_get", "tool_name": "http_get", "status": "success", "timestamp": "2024-05-01T12:00:05Z", "duration_ms": 210,
                 "tool_arguments": {"url": "https://example.com/pricing"}, "tool_result": "200 OK, 41kb html"},
                {"type": "tool_call", "name": "http_get", "tool_name": "http_get", "status": "success", "timestamp": "2024-05-01T12:00:20Z", "duration_ms": 205,
                 "tool_arguments": {"url": "https://example.com/pricing"}, "tool_result": "200 OK, 41kb html"},
                {"type": "tool_call", "name": "http_get", "tool_name": "http_get", "status": "success", "timestamp": "2024-05-01T12:00:35Z", "duration_ms": 199,
                 "tool_arguments": {"url": "https://example.com/pricing"}, "tool_result": "200 OK, 41kb html"},
                {"type": "tool_call", "name": "http_get", "tool_name": "http_get", "status": "success", "timestamp": "2024-05-01T12:00:50Z", "duration_ms": 201,
                 "tool_arguments": {"url": "https://example.com/pricing"}, "tool_result": "200 OK, 41kb html"},
                {"type": "tool_call", "name": "http_get", "tool_name": "http_get", "status": "success", "timestamp": "2024-05-01T12:01:05Z", "duration_ms": 203,
                 "tool_arguments": {"url": "https://example.com/pricing"}, "tool_result": "200 OK, 41kb html"},
                {"type": "error", "name": "graph aborted", "status": "failed", "timestamp": "2024-05-01T12:02:30Z",
                 "error": "GraphRecursionError: recursion limit of 25 reached, same tool called 5 times with identical arguments"}
            ],
            "output": None
        },
    },
    "rate-limit-recovered": {
        "label": "Coder agent — 429 recovered by retry",
        "framework": "openai-agents",
        "trace": {
            "execution_id": "demo-exec-1004",
            "agent_name": "Coder Agent",
            "framework": "openai-agents",
            "status": "success",
            "started_at": "2024-05-01T13:00:00Z",
            "ended_at": "2024-05-01T13:00:14Z",
            "task_description": "Write a Python function that reverses a linked list.",
            "steps": [
                {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-01T13:00:00Z"},
                {"type": "llm", "name": "generate code", "status": "failed", "timestamp": "2024-05-01T13:00:02Z", "duration_ms": 1900,
                 "error": "openai.RateLimitError: 429 Too Many Requests (retrying in 2s)"},
                {"type": "retry", "name": "retry generate code (attempt 2)", "status": "success", "timestamp": "2024-05-01T13:00:05Z", "duration_ms": 4200,
                 "output": "def reverse(head): ..."},
                {"type": "tool_call", "name": "run_tests", "tool_name": "run_tests", "status": "success", "timestamp": "2024-05-01T13:00:11Z", "duration_ms": 2400,
                 "tool_arguments": {"suite": "linked_list"}, "tool_result": "5 passed"},
                {"type": "output", "name": "final answer", "status": "success", "timestamp": "2024-05-01T13:00:14Z",
                 "output": "Here is the function, and all 5 tests pass."}
            ],
            "output": "Here is the function, and all 5 tests pass."
        },
    },
    "context-overflow": {
        "label": "Analyst agent — context window exceeded",
        "framework": "anthropic",
        "trace": {
            "execution_id": "demo-exec-1005",
            "agent_name": "Data Analyst Agent",
            "framework": "anthropic",
            "status": "failed",
            "started_at": "2024-05-01T14:00:00Z",
            "ended_at": "2024-05-01T14:04:10Z",
            "task_description": "Analyse the attached 4000-row CSV and report the top 5 products by revenue.",
            "steps": [
                {"type": "task", "name": "task received", "status": "success", "timestamp": "2024-05-01T14:00:00Z"},
                {"type": "tool_call", "name": "read_csv", "tool_name": "read_csv", "status": "success", "timestamp": "2024-05-01T14:00:02Z", "duration_ms": 900,
                 "tool_arguments": {"path": "sales.csv"}, "tool_result": "<4000 rows appended to context>"},
                {"type": "llm", "name": "analyse rows", "status": "failed", "timestamp": "2024-05-01T14:03:50Z", "duration_ms": 12000,
                 "error": "anthropic.BadRequestError: prompt is too long: 214530 tokens > 200000 maximum context length"},
                {"type": "error", "name": "run failed", "status": "failed", "timestamp": "2024-05-01T14:04:10Z",
                 "error": "ContextLengthExceeded"}
            ],
            "output": None
        },
    },
}


def list_demos() -> list[dict[str, str]]:
    return [{"id": key, "label": value["label"], "framework": value["framework"]} for key, value in DEMOS.items()]


def get_demo(demo_id: str) -> dict[str, Any]:
    demo = DEMOS[demo_id]
    return {"text": json.dumps(demo["trace"], indent=2), "label": demo["label"], "framework": demo["framework"]}
