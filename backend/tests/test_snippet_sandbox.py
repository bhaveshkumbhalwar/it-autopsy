"""Generated snippets are executed *here*, in a sandbox, and only in tests.

The UI never runs a snippet — it shows text and a copy button. But a snippet
that would not run is worse than no snippet, so every generated fix is compiled
and then executed against a stub raising the exception the trace named. If the
trace names no exception, the snippet must catch broadly at the tool boundary and
still retry a timeout while letting an unrelated error through.

This imports ``requests`` (a test-only dependency) to prove the
``requests.exceptions.ReadTimeout`` path.
"""

from __future__ import annotations

import ast
import json
import time

import httpx
import pytest
import requests

from app.engine.snippets import build_snippet, extract_facts
from app.ingest import ingest_text
from app.engine.analyzer import diagnose
from app.samples.demos import get_demo


class ToolBoom(Exception):
    """A trace's own exception class."""


def _facts(error: str, tool: str = "fetch_page", category: str = "timeout_failure") -> dict:
    trace = {
        "framework": "langgraph",
        "steps": [
            {"index": 0, "type": "tool_call", "tool_name": tool, "status": "success"},
            {"index": 1, "type": "tool_call", "tool_name": tool, "status": "failed", "error": error},
        ],
    }
    return extract_facts(trace, 1, [category])


def _namespace(**extra) -> dict:
    return {"ToolBoom": ToolBoom, "__name__": "snippet_sandbox", **extra}


def _load(code: str, **extra) -> dict:
    # The sandbox never executes a snippet that carries a name the policy would
    # not accept: the guard comes before execution, not after.
    from app.engine.snippets import SAFE_IDENTIFIER

    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and not SAFE_IDENTIFIER.fullmatch(root.id):
                raise AssertionError(f"unvalidated identifier {root.id!r} in generated code")
    namespace = _namespace(**extra)
    exec(compile(code, "<generated snippet>", "exec"), namespace)  # noqa: S102 - sandboxed, test only
    return namespace


# --------------------------------------------------------------------------- #
# The exception a snippet catches comes from the trace
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("error", "caught"),
    [
        ("httpx.ReadTimeout: read operation timed out", "httpx.ReadTimeout"),
        ("ReadTimeout: read operation timed out", "httpx.ReadTimeout"),
        ("requests.exceptions.ReadTimeout: timed out", "requests.exceptions.ReadTimeout"),
    ],
)
def test_the_catch_clause_is_the_traced_exception(error, caught):
    code = build_snippet("timeout_failure", _facts(error))["code"]
    assert f"except {caught} as exc:" in code


def test_an_unknown_exception_catches_broadly_and_never_stands_in_a_builtin():
    code = build_snippet("timeout_failure", _facts("Read timeout=30"))["code"]
    assert "except Exception as exc:" in code
    assert "TimeoutError" not in code, "a builtin must never stand in for ReadTimeout"
    assert "isinstance" in code


def test_a_traced_requests_timeout_imports_its_own_module():
    """One page, one HTTP library: a requests trace never gets an httpx snippet."""
    code = build_snippet("timeout_failure", _facts("requests.exceptions.ReadTimeout: timed out"))["code"]
    assert "from requests.exceptions import ReadTimeout" in code
    assert "import requests" in code
    assert "httpx" not in code
    assert "requests.Session()" in code
    compile(code, "<snippet>", "exec")


def test_a_traced_httpx_timeout_stays_on_httpx():
    code = build_snippet("timeout_failure", _facts("httpx.ReadTimeout: read timed out"))["code"]
    assert "import httpx" in code
    assert "import requests" not in code


# --------------------------------------------------------------------------- #
# Executed against a stub that raises what the trace raised
# --------------------------------------------------------------------------- #

def test_httpx_read_timeout_is_caught_retried_and_reported(monkeypatch):
    code = build_snippet("timeout_failure", _facts("httpx.ReadTimeout: read timed out"))["code"]
    namespace = _load(code)
    attempts = {"n": 0}

    def boom(*_args, **_kwargs):
        attempts["n"] += 1
        raise httpx.ReadTimeout("the read operation timed out")

    namespace["_client"].get = boom
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError) as caught:
        namespace["call_fetch_page"]()
    assert attempts["n"] == namespace["MAX_ATTEMPTS"] == 3
    assert "still timing out" in str(caught.value)


def test_requests_read_timeout_is_caught_retried_and_reported(monkeypatch):
    code = build_snippet("timeout_failure", _facts("requests.exceptions.ReadTimeout: timed out"))["code"]
    namespace = _load(code)
    attempts = {"n": 0}

    def boom(*_args, **_kwargs):
        attempts["n"] += 1
        raise requests.exceptions.ReadTimeout("timed out")

    namespace["_client"].get = boom
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError):
        namespace["call_fetch_page"]()
    assert attempts["n"] == 3


def test_an_unnamed_exception_is_caught_at_the_boundary_and_retried(monkeypatch):
    code = build_snippet("timeout_failure", _facts("Read timeout=30"))["code"]
    namespace = _load(code)
    attempts = {"n": 0}

    def slow(*_args, **_kwargs):
        attempts["n"] += 1
        raise httpx.ReadTimeout("slow")

    namespace["_client"].get = slow
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError):
        namespace["call_fetch_page"]()
    assert attempts["n"] == 3


def test_an_unnamed_exception_does_not_swallow_an_unrelated_error(monkeypatch):
    """A 404 is not a timeout: it must leave the retry loop immediately."""
    code = build_snippet("timeout_failure", _facts("Read timeout=30"))["code"]
    namespace = _load(code)
    attempts = {"n": 0}

    def broken(*_args, **_kwargs):
        attempts["n"] += 1
        raise ValueError("404 Not Found")

    namespace["_client"].get = broken
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(ValueError):
        namespace["call_fetch_page"]()
    assert attempts["n"] == 1


def test_the_timeout_snippet_returns_on_success():
    code = build_snippet("timeout_failure", _facts("httpx.ReadTimeout: x"))["code"]
    namespace = _load(code)
    namespace["_client"].get = lambda *a, **k: "ok"
    assert namespace["call_fetch_page"]() == "ok"


# --------------------------------------------------------------------------- #
# The other families
# --------------------------------------------------------------------------- #

def test_tool_snippet_returns_a_structured_failure_instead_of_raising():
    code = build_snippet("tool_execution_failure", _facts("ToolBoom: the tool crashed", "http_get", "tool_execution_failure"))["code"]
    namespace = _load(code)

    def tool(**_kwargs):
        raise ToolBoom("the tool crashed")

    result = namespace["run_http_get"]({}, tool=tool)
    assert result["ok"] is False
    assert result["error"] == "ToolBoom"
    assert result["tool"] == "http_get"


def test_tool_snippet_returns_the_value_on_success():
    code = build_snippet("tool_execution_failure", _facts("ToolBoom: x", "http_get", "tool_execution_failure"))["code"]
    namespace = _load(code)
    assert namespace["run_http_get"]({}, tool=lambda **_: {"price": 10})["ok"] is True


def test_api_snippet_honours_retry_after_and_gives_up(monkeypatch):
    code = build_snippet("api_failure", _facts("HTTP 429 Too Many Requests, retry after 12s", "search", "api_failure"))["code"]
    namespace = _load(code)
    calls = {"n": 0}

    class Response:
        status_code = 429
        headers = {"retry-after": "1"}

        def raise_for_status(self):
            raise AssertionError("must not be called on a retry status")

    def call():
        calls["n"] += 1
        return Response()

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError):
        namespace["call_api_with_backoff"](call)
    assert calls["n"] == namespace["MAX_ATTEMPTS"]


def test_auth_snippet_raises_on_a_rejected_key(monkeypatch):
    code = build_snippet("authentication_failure", _facts("401 Unauthorized", "call_api", "authentication_failure"))["code"]
    monkeypatch.setenv("SERVICE_API_KEY", "sk-test-not-a-real-key")
    namespace = _load(code, os=__import__("os"))

    class Response:
        status_code = 401

    def request(*_args, **_kwargs):
        return Response()

    with pytest.raises(PermissionError):
        namespace["call_service"](request)


def test_every_generated_snippet_for_every_demo_compiles():
    for demo_id in ("tool-timeout", "infinite-loop", "context-overflow", "rate-limit-recovered"):
        trace, _warnings, _fmt = ingest_text(get_demo(demo_id)["text"])
        analysis = diagnose(trace, use_llm=False)
        for fix in analysis["fixes"]:
            compile(fix["snippet"]["code"], fix["id"] or "snippet", "exec")


def test_no_snippet_leaks_a_secret_into_executable_text():
    secret = "gsk_SANDBOX123456789012345678901234"
    trace = {
        "framework": "langgraph",
        "steps": [{"index": 0, "type": "tool_call", "tool_name": "call_api", "status": "failed", "error": f"401 Unauthorized for {secret}"}],
    }
    facts = extract_facts(trace, 0, ["authentication_failure"])
    code = build_snippet("authentication_failure", facts)["code"]
    assert secret not in code
    compile(code, "auth", "exec")
