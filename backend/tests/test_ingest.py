"""Ingestion tests: formats, invalid input, redaction."""

from __future__ import annotations

import json

import pytest

from app.ingest import ParseError, ingest_text
from app.ingest.detector import detect_format
from app.ingest.util import load_json_or_jsonl


def test_generic_trace_parses_steps_and_status():
    payload = {
        "execution_id": "e1",
        "agent_name": "Agent A",
        "status": "failed",
        "steps": [
            {"type": "task", "input": "do it", "status": "success"},
            {"type": "tool_call", "tool_name": "search", "tool_arguments": {"q": "x"}, "status": "failed", "error": "boom"},
        ],
    }
    trace, fmt, redactions = ingest_text(json.dumps(payload))
    assert fmt == "generic"
    assert trace.execution_id == "e1"
    assert len(trace.steps) == 2
    assert trace.steps[1].tool_name == "search"
    assert trace.status.value == "failed"
    assert redactions == 0


def test_jsonl_one_object_per_line():
    lines = "\n".join(json.dumps({"execution_id": "x", "steps": [{"type": "task", "status": "success"}]}) for _ in range(2))
    records, kind = load_json_or_jsonl(lines)
    assert kind == "jsonl"
    assert len(records) == 2


def test_invalid_json_reports_line_number():
    bad = '{"a": 1}\n{"b": 2,,}\n'
    with pytest.raises(ParseError) as exc:
        load_json_or_jsonl(bad)
    assert exc.value.line == 2
    assert "line 2" in exc.value.message


def test_empty_input_rejected():
    with pytest.raises(ParseError):
        ingest_text("   ")


def test_unsupported_extension_rejected():
    with pytest.raises(ParseError):
        ingest_text('{"a":1}', filename="trace.exe")


def test_openai_chat_completion_detected_and_parsed():
    payload = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"Pune"}'}}
                    ],
                },
            }
        ],
    }
    trace, fmt, _ = ingest_text(json.dumps(payload))
    assert fmt == "openai"
    assert any(s.tool_name == "get_weather" for s in trace.steps)


def test_openai_length_finish_reason_is_failure():
    payload = {
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}],
    }
    trace, fmt, _ = ingest_text(json.dumps(payload))
    assert trace.status.value == "failed"
    assert any(s.error and "max token" in s.error for s in trace.steps)


def test_anthropic_tool_use_and_error():
    payload = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet",
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "Let me look that up."},
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "rag"}},
        ],
    }
    trace, fmt, _ = ingest_text(json.dumps(payload))
    assert fmt == "anthropic"
    assert trace.execution_id == "msg_1"
    assert any(s.tool_name == "search" for s in trace.steps)


def test_langsmith_runs_rebuilt():
    runs = [
        {"id": "r1", "trace_id": "t1", "name": "AgentExecutor", "run_type": "chain", "start_time": "2024-05-01T10:00:00Z", "end_time": "2024-05-01T10:00:05Z", "inputs": {"q": "hi"}, "outputs": {"o": "done"}},
        {"id": "r2", "trace_id": "t1", "parent_run_id": "r1", "name": "search", "run_type": "tool", "start_time": "2024-05-01T10:00:01Z", "error": "ToolException: boom"},
    ]
    trace, fmt, _ = ingest_text(json.dumps(runs))
    assert fmt == "langsmith"
    assert len(trace.steps) == 2
    # The root run completed, so the execution is recorded as partially recovered.
    assert trace.status.value == "partial"
    assert trace.steps[1].error.startswith("ToolException")


def test_otlp_spans_parsed():
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "agent"}}]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "abc123",
                                "spanId": "s1",
                                "name": "invoke_agent",
                                "startTimeUnixNano": "1714557600000000000",
                                "endTimeUnixNano": "1714557601000000000",
                                "status": {"code": "STATUS_CODE_ERROR", "message": "tool failed"},
                                "attributes": [
                                    {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
                                    {"key": "gen_ai.tool.name", "value": {"stringValue": "get_weather"}},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    trace, fmt, _ = ingest_text(json.dumps(payload))
    assert fmt == "otlp"
    assert trace.execution_id == "abc123"
    assert trace.steps[0].tool_name == "get_weather"
    assert trace.status.value == "failed"


def test_mcp_frames_paired_by_id():
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "my-client"}}},
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "a.txt"}}},
        {"jsonrpc": "2.0", "id": 2, "error": {"code": -32603, "message": "file not found"}},
    ]
    trace, fmt, _ = ingest_text("\n".join(json.dumps(f) for f in frames))
    assert fmt == "mcp"
    assert trace.status.value == "failed"
    assert any("file not found" in (s.error or "") for s in trace.steps)


def test_secrets_are_redacted():
    payload = {
        "agent_name": "Leaky",
        "status": "failed",
        "steps": [
            {"type": "task", "input": "my key is sk-abcdefghijklmnopqrstuvwxyz123456 and mail me at a@b.com", "status": "success"}
        ],
    }
    trace, _fmt, redactions = ingest_text(json.dumps(payload))
    text = json.dumps(trace.to_dict())
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text
    assert "a@b.com" not in text
    assert redactions >= 2


def test_detect_format_falls_back_to_generic():
    assert detect_format([{"foo": "bar"}]) == "generic"


def test_unknown_shape_still_parses_as_generic():
    trace, fmt, _ = ingest_text(json.dumps({"steps": [{"role": "assistant", "content": "hello"}]}))
    assert fmt == "generic"
    assert trace.steps
