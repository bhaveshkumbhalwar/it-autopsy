"""Format detection.

The detector inspects the shape of the uploaded records and returns the name of
the adapter that should handle them. Detection is deliberately conservative and
always falls back to the generic adapter.
"""

from __future__ import annotations

from typing import Any

from app.ingest.util import get_path

FORMAT_LABELS = {
    "openai": "OpenAI Chat / Responses / Agents SDK",
    "anthropic": "Anthropic Messages",
    "langsmith": "LangSmith runs",
    "otlp": "OTLP GenAI spans",
    "mcp": "MCP JSON-RPC",
    "generic": "Generic JSON / JSONL trace",
}


def _looks_like_otel(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if "resourceSpans" in record or "resource_spans" in record:
        return True
    return bool(
        ("traceId" in record or "trace_id" in record)
        and ("spanId" in record or "span_id" in record)
    ) or "scopeSpans" in record or "scope_spans" in record


def detect_format(records: list[Any]) -> str:
    if not records:
        return "generic"

    flat: list[Any] = []
    for record in records:
        if isinstance(record, list):
            flat.extend(record)
        else:
            flat.append(record)

    if any(_looks_like_otel(r) for r in flat):
        return "otlp"

    if any(
        isinstance(r, dict)
        and (("run_type" in r and ("dotted_order" in r or "child_run_ids" in r or "trace_id" in r)) or "events" in r and "run_type" in r)
        for r in flat
    ):
        return "langsmith"

    if any(
        isinstance(r, dict)
        and (r.get("jsonrpc") == "2.0" or r.get("method") in {"tools/call", "tools/list", "initialize"})
        and ("id" in r or "result" in r or "error" in r)
        for r in flat
    ):
        return "mcp"

    if any(
        isinstance(r, dict)
        and (
            str(get_path(r, "type", "")).startswith("message.")
            or str(get_path(r, "type", "")).startswith("content_block")
            or ("message" in r and get_path(r, "message.role") == "assistant")
            or ("stop_reason" in r and "content" in r)
        )
        for r in flat
    ):
        return "anthropic"

    if any(
        isinstance(r, dict)
        and (
            get_path(r, "object") in {"chat.completion", "response", "chat.completion.chunk"}
            or ("choices" in r and "model" in r)
            or "output" in r and get_path(r, "object") == "response"
            or get_path(r, "response.output") is not None
        )
        for r in flat
    ):
        return "openai"

    if any(
        isinstance(r, dict) and ("steps" in r or "spans" in r or "events" in r) and ("messages" in r or "output" in r or "status" in r)
        for r in flat
    ):
        return "generic"

    return "generic"
