"""Concrete, copy-pasteable fixes derived from the trace itself.

The rule engine already decides *what* to fix ("add a bounded timeout and
retry with backoff"). This module turns that advice into the code a developer
would actually paste, filled from the trace: the real tool name, the timeout it
failed with, the step number, the host it could not reach.

Two rules:

* **Never required.** Every snippet is a deterministic template. The LLM may
  *refine* one, but nothing here depends on it being available or working.
* **Text only.** Snippets are strings for a copy button. Nothing is executed,
  imported or evaluated.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.config import settings
from app.core.models import FailureCategory as F
from app.core.redact import redact_secret

# --------------------------------------------------------------------------- #
# Facts pulled out of the trace
# --------------------------------------------------------------------------- #

_TIMEOUT_RE = re.compile(
    r"(?:read\s*timeout|timed?\s*out|timeout)\s*(?:=|of|after|:)?\s*(\d+(?:\.\d+)?)\s*(?:s\b|seconds|sec\b)|"
    r"\((?:read|connect) timeout=(\d+(?:\.\d+)?)\)",
    re.I,
)
# An HTTP status is only a status when it is introduced as one: a bare 443 in
# "port=443" is a port, not a response code.
_HTTP_STATUS_RE = re.compile(
    r"(?:HTTP[/ ]?|status[^\d]{0,8}|responded |returned |code[=: ]\s*)(4\d{2}|5\d{2})\b", re.I
)
_RETRY_AFTER_RE = re.compile(r"retry[- ]after[:\s]+(\d+(?:\.\d+)?)\s*s?", re.I)
_HOST_RE = re.compile(r"host=['\"]?([\w.-]+)|(?:^|\s)(https?://[\w./-]+)|to ([\w.-]+\.[a-z]{2,})", re.I)
_CONTEXT_RE = re.compile(r"(\d{2,3}[,.]?\d{3})\s*(?:tokens|>)|context (?:window|length) (?:of )?(\d{2,3}[,.]?\d{3})", re.I)
_DB_NAME_RE = re.compile(r"database ['\"]?([\w-]+)", re.I)

# How a trace names the exception that was raised. The first qualifier the
# trace gives wins (`httpx.ReadTimeout`), then the bare name is looked up in
# `EXCEPTION_ALIASES`; a name we cannot place is handled by the generic
# `except Exception` branch instead of guessing at a builtin.
_EXCEPTION_RE = re.compile(r"\b((?:[A-Za-z_]\w{0,30}\.){0,3}[A-Z]\w{0,40}(?:Error|Timeout|Exception|Failure|Interrupt))\b")

# Bare name -> (import line, qualified name). Only names whose module is known.
EXCEPTION_ALIASES: dict[str, tuple[str, str]] = {
    "ReadTimeout": ("import httpx", "httpx.ReadTimeout"),
    "ConnectTimeout": ("import httpx", "httpx.ConnectTimeout"),
    "ReadTimeoutError": ("import httpx", "httpx.ReadTimeout"),
    "TimeoutException": ("from requests.exceptions import TimeoutException", "requests.exceptions.TimeoutException"),
    "ConnectionError": ("from requests.exceptions import ConnectionError", "requests.exceptions.ConnectionError"),
    "RequestException": ("from requests.exceptions import RequestException", "requests.exceptions.RequestException"),
    "OperationalError": ("from sqlalchemy.exc import OperationalError", "OperationalError"),
    "DatabaseError": ("from sqlalchemy.exc import DatabaseError", "DatabaseError"),
    "DBAPIError": ("from sqlalchemy.exc import DBAPIError", "DBAPIError"),
    "RateLimitError": ("", "RateLimitError"),
    "ToolException": ("", "ToolException"),
}

# Builtins that mean the same thing as a read/connect timeout, so they are
# never used as a stand-in for one.
_TIMEOUT_NAMES = {"readtimeout", "connecttimeout", "timeoutexception", "timeouterror"}


# --------------------------------------------------------------------------- #
# Everything interpolated into generated code is validated first
# --------------------------------------------------------------------------- #

# A name that may appear in generated code: a dotted Python identifier. Anything
# else in a trace (quotes, newlines, a statement smuggled into a tool name) is
# replaced by a placeholder, never interpolated raw.
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
# Values that end up inside a Python string literal are narrower: no dots, no
# escape sequences, no quotes.
SAFE_WORD = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _-]{0,63}$")
# A host or a database name is displayed, never executed: keep it plain ASCII.
SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/ -]{0,63}$")
# The framework only ever lands in the note under the snippet.
SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/+()-]{0,39}$")

TOOL_PLACEHOLDER = "your_tool"
EXCEPTION_PLACEHOLDER = "Exception"


def safe_identifier(value: Any, placeholder: str = TOOL_PLACEHOLDER) -> str:
    """A dotted identifier safe to put in code, or the placeholder."""
    text = str(value or "").strip()
    return text if SAFE_IDENTIFIER.fullmatch(text) else placeholder


def safe_word(value: Any, placeholder: str = TOOL_PLACEHOLDER) -> str:
    """A word safe inside a quoted string, or the placeholder."""
    text = str(value or "").strip()
    return text if SAFE_WORD.fullmatch(text) else placeholder


def safe_text(value: Any, placeholder: str = "") -> str:
    """A short plain-text value safe in a comment or a message, or ``placeholder``."""
    text = str(value or "").strip()
    return text if SAFE_TEXT.fullmatch(text) else placeholder


def safe_label(value: Any, placeholder: str = "unspecified framework") -> str:
    text = " ".join(str(value or "").split())
    return text if SAFE_LABEL.fullmatch(text) else placeholder


def _exception_facts(text: str) -> dict[str, Any]:
    """The exception a trace names, resolved to something importable.

    ``{"caught": "httpx.ReadTimeout", "import": "import httpx"}`` when the name
    is known; ``{"caught": "Exception", "import": "", "unknown": "FooError"}``
    when it is not — the snippet then catches ``Exception`` at the boundary and
    explains the branch work in a comment, rather than catching the wrong type.
    """
    for match in _EXCEPTION_RE.finditer(text or ""):
        name = match.group(1)
        short = name.rsplit(".", 1)[-1]
        qualified = name if "." in name else None
        if qualified is None:
            alias = EXCEPTION_ALIASES.get(short)
            if alias and alias[1] != short:
                qualified = alias[1]
            elif alias:
                qualified = short
        if qualified is None:
            return {"caught": "Exception", "import": "", "unknown": short}
        if not SAFE_IDENTIFIER.fullmatch(qualified or ""):
            # A name that is not a plain dotted identifier cannot be put in an
            # except clause: fall back to the boundary catch.
            return {"caught": "Exception", "import": "", "unknown": None, "unsafe": True}
        module = qualified.rsplit(".", 1)[0] if "." in qualified else ""
        if module not in {"httpx", "requests.exceptions", "sqlalchemy.exc"}:
            # A trace's own exception (a tool's custom class): catch it by the
            # name the trace used, and say where the import normally comes from.
            return {"caught": qualified, "import": "", "unknown": None, "external": True}
        imports = {
            "httpx": "import httpx",
            "requests.exceptions": "from requests.exceptions import " + qualified.rsplit(".", 1)[-1],
            "sqlalchemy.exc": "from sqlalchemy.exc import " + qualified.rsplit(".", 1)[-1],
        }
        return {"caught": qualified, "import": imports.get(module, ""), "unknown": None}
    return {"caught": "Exception", "import": "", "unknown": None}


def _is_read_timeout(caught: str) -> bool:
    return caught.rsplit(".", 1)[-1].lower() in _TIMEOUT_NAMES


TIMEOUT_TYPE_HINTS = ("Timeout", "ConnectTimeout", "ReadTimeout", "TimeoutException")
_TABLE_RE = re.compile(r'(?:from|into|table)\s+["\']?([a-z_][\w]*)', re.I)


def _first(step: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _error_text(step: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _first(step, "error"),
            _first(step, "tool_result"),
            _first(step, "output"),
            str(step.get("status") or ""),
        )
        if part
    )


def _number(pattern: re.Pattern[str], text: str, group: int = 1) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    raw = next((g for g in match.groups() if g), None)
    if raw is None:
        return None
    try:
        # Thousands separators only ("214,530"), not decimals: 2.5 stays 2.5.
        return float(raw.replace(",", "")) if "," in raw else float(raw)
    except (TypeError, ValueError):
        return None


def _nearest_tool(steps: list[dict[str, Any]], failed_index: int | None) -> str | None:
    """The tool used by the closest step before the failure that names one."""
    if not isinstance(failed_index, int):
        return None
    earlier = [s for s in steps if isinstance(s.get("index"), int) and s["index"] < failed_index]
    for step in reversed(earlier):
        name = _first(step, "tool_name")
        if name:
            return name
    return None


def _nearest_timeout(steps: list[dict[str, Any]], failed_index: int | None) -> int | None:
    """A timeout mentioned by the failing step or the few steps before it."""
    if not isinstance(failed_index, int):
        return None
    window = [s for s in steps if isinstance(s.get("index"), int) and failed_index - 3 <= s["index"] <= failed_index]
    for step in reversed(window):
        found = _number(_TIMEOUT_RE, _error_text(step))
        if found:
            return found
    return None


def extract_facts(trace: dict[str, Any], failed_index: int | None, categories: list[str]) -> dict[str, Any]:
    """The handful of real values a template needs, read off the failing step."""
    steps = trace.get("steps") or []
    step = next((s for s in steps if s.get("index") == failed_index), None) or {}
    text = _error_text(step)

    timeout = _number(_TIMEOUT_RE, text)
    status_match = _HTTP_STATUS_RE.search(text)
    http_status = int(status_match.group(1)) if status_match else None
    retry_after = _number(_RETRY_AFTER_RE, text)
    host_match = _HOST_RE.search(text)
    host = next((g for g in host_match.groups() if g), None) if host_match else None
    context_match = _CONTEXT_RE.search(text)
    context_tokens = None
    if context_match:
        raw = next((g for g in context_match.groups() if g), None)
        if raw:
            context_tokens = int(raw.replace(",", "").replace(".", ""))

    tool = _first(step, "tool_name") or None
    step_type = str(step.get("type") or "task")
    if not tool:
        # The failure is often recorded one step later (an error record, a
        # retry) than the call that caused it: take the tool from the nearest
        # earlier step that used one, so the fix names the real tool.
        tool = _nearest_tool(steps, failed_index)
    if not timeout:
        # Same reasoning for an observed timeout that only an earlier step
        # recorded.
        timeout = _nearest_timeout(steps, failed_index)
    args = step.get("tool_arguments") or {}
    if not isinstance(args, dict):
        args = {}

    exception = _exception_facts(text)
    # Every one of these can reach generated code, so each is validated here and
    # only ever reaches a template as a value that passed the policy.
    tool_name = safe_word(tool) if tool else None
    return {
        "framework": safe_label(trace.get("framework"), placeholder="") or None,
        # The tool as the trace spelled it, and the same tool reduced to a name
        # that may be interpolated; the two differ only for a hostile value.
        "tool_label": safe_word(tool, placeholder="the tool call") if tool else None,
        "tool_identifier": safe_identifier(tool) if tool else None,
        "caught_exception": exception["caught"],
        "exception_import": exception["import"],
        # The HTTP library the trace itself named, so every snippet on one page
        # speaks the same one: a requests trace never gets an httpx snippet.
        "http_library": "requests" if "requests" in exception["import"] or "requests." in text else None,
        "unknown_exception": exception["unknown"],
        "step_index": failed_index,
        "step_number": (failed_index + 1) if isinstance(failed_index, int) else None,
        "step_type": step_type,
        "tool_name": tool_name,
        "tool_display": tool_name or "the tool call",
        "timeout_seconds": int(timeout) if timeout else None,
        "suggested_timeout_seconds": int(max(60, (timeout or 30) * 2)),
        "http_status": http_status,
        "retry_after_seconds": int(retry_after) if retry_after else None,
        "host": safe_text(host, placeholder="") or None,
        "context_tokens": context_tokens,
        "database_name": safe_word(_DB_NAME_RE.search(text).group(1), placeholder="") if _DB_NAME_RE.search(text) else None,
        "table_name": (
            next(
                (m.group(1) for m in [_TABLE_RE.search(str(args.get(k, ""))) for k in args] if m),
                None,
            )
            if isinstance(args, dict)
            else None
        ),
        "categories": list(categories),
    }


# --------------------------------------------------------------------------- #
# Templates, one per failure family
# --------------------------------------------------------------------------- #

_TIMEOUT_TEMPLATE = '''import time
import httpx
{extra_import}
# Step {step_number} failed: {tool_name} timed out after {timeout_seconds}s.
# Give it room, and retry the transient case instead of failing the run.
{timeout_branch_comment}
TOOL_TIMEOUT = {timeout_constructor}
MAX_ATTEMPTS = 3

# Keep one client: a pool per attempt leaks sockets under load.
{client_line}

def call_{func_name}(*args, **kwargs):
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _client.get(*args, **kwargs)
        except {caught} as exc:
            last_error = exc{timeout_guard}
            backoff = min(4.0, 0.5 * 2 ** (attempt - 1))
            if attempt == MAX_ATTEMPTS:
                break
            time.sleep(backoff)
    raise RuntimeError(
        f"{tool_name} still timing out after {{MAX_ATTEMPTS}} attempts "
        f"(last attempt on step {step_number})"
    ) from last_error
'''

_TOOL_TEMPLATE = '''# Step {step_number}: the tool call {tool_name} failed and the agent had no
# answer to that. Return a structured result the agent can reason about, and
# never let a tool exception end the run.
# {exception_note}
MAX_ATTEMPTS = 2


def run_{func_name}(arguments: dict, tool=None):
    # `tool` is your real tool callable; `arguments` is what the agent passed.
    tool = tool or {tool_ref}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = tool(**arguments)
            if result is None:
                raise ValueError("{tool_name} returned no result")
            return {{"ok": True, "data": result, "attempts": attempt}}
        except {caught} as exc:
            if attempt == MAX_ATTEMPTS:
                # Hand the failure back to the agent as data; never raise here.
                return {{
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc)[:300],
                    "tool": "{tool_name}",
                    "step": {step_number},
                }}
'''

_API_TEMPLATE = '''import random
import time
import httpx

# Step {step_number}: the API answered {http_status} for {tool_name}.
# {retry_after_clause}
MAX_ATTEMPTS = 4
RETRY_STATUSES = {{429, 500, 502, 503, 504}}

def call_api_with_backoff(fn, *args, **kwargs):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = fn(*args, **kwargs)
        if response.status_code not in RETRY_STATUSES:
            response.raise_for_status()
            return response
        retry_after = float(response.headers.get("retry-after", {retry_after_default}))
        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(
                f"Still {{response.status_code}} after {{MAX_ATTEMPTS}} attempts (step {step_number})"
            )
        time.sleep(max(retry_after, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.3))
'''

_LOOP_TEMPLATE = '''# The run looped without making progress. Cap it, and require the state to
# change before another iteration is allowed.
MAX_ITERATIONS = 8
seen_states = set()

for iteration in range(MAX_ITERATIONS):
    state = tuple(sorted(context.items()))   # whatever defines "where we are"
    if state in seen_states:
        raise RuntimeError(
            "No progress: the agent returned to an earlier state "
            "(detected near step {step_number})"
        )
    seen_states.add(state)
    context = step_agent(context)
else:
    raise RuntimeError(f"Did not finish within {{MAX_ITERATIONS}} iterations (step {step_number})")
'''

_CONTEXT_TEMPLATE = '''# The prompt outgrew the context window around step {step_number}.
# Budget it explicitly and summarise instead of truncating blindly.
MAX_CONTEXT_TOKENS = {suggested_context_tokens}
RESERVED_FOR_OUTPUT = 2048

def fit_context(messages: list[dict], budget: int = MAX_CONTEXT_TOKENS) -> list[dict]:
    budget -= RESERVED_FOR_OUTPUT
    kept, used = [], 0
    for message in reversed(messages):
        cost = len(message.get("content", "")) // 4      # ~4 chars per token
        if used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    if len(kept) < len(messages):
        # Keep a rolling summary of everything that was dropped.
        dropped = messages[: len(messages) - len(kept)]
        kept.insert(0, {{
            "role": "system",
            "content": "Earlier context, summarised: " + summarise(dropped),
        }})
    return kept
'''

_AUTH_TEMPLATE = '''import os

# Step {step_number}: the call was rejected ({auth_status}) - credentials, not
# transient load. Do not retry: fail fast and say which credential.
def require_credentials(value: str | None, name: str) -> str:
    if not value:
        raise PermissionError(f"{{name}} is not set (see backend/.env)")
    return value

API_KEY = require_credentials(os.environ.get("SERVICE_API_KEY"), "SERVICE_API_KEY")


def call_service(request, *args, **kwargs):
    # `request` is whatever makes the call in your agent: client.get, a hub
    # client, a tool function. It is called here with the key's header.
    response = request(*args, headers={{"Authorization": f"Bearer {{API_KEY}}"}}, **kwargs)
    if response.status_code in (401, 403):
        raise PermissionError(
            f"Credentials rejected by {{HOST}} (HTTP {{response.status_code}}) "
            f"at step {step_number} - rotate the key or fix its scope; retrying will not help."
        )
    return response
'''

_DATABASE_TEMPLATE = '''import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, DBAPIError

# Step {step_number}: the database call{database_clause} failed.
# Bounded connect, health check before the agent starts, retry only the
# transient connection errors.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={{"connect_timeout": 5}},
)
TRANSIENT = ("could not connect", "connection refused", "deadlock", "server closed")

def query(sql: str, params: dict | None = None, attempts: int = 3):
    for attempt in range(1, attempts + 1):
        try:
            with engine.begin() as conn:
                return conn.execute(text(sql), params or {{}}).fetchall()
        except {caught} as exc:
            message = str(exc).lower()
            if attempt == attempts or not any(t in message for t in TRANSIENT):
                raise
            time.sleep(0.5 * 2 ** (attempt - 1))
'''

_GENERIC_TEMPLATE = '''# Step {step_number}: {category_label}.
# Turn the failure into a value you can inspect, instead of an exception that
# ends the run.
async def guarded(fn, *args, **kwargs):
    try:
        return {{"ok": True, "data": await fn(*args, **kwargs)}}
    except Exception as exc:
        return {{"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]}}
'''

GENERIC_CATEGORY = "generic"
GENERIC_CAPTION = "A generic guard for this failure — not a mechanical patch"


# Families whose failure is a wrong answer, not a wrong call: there is no
# mechanical patch, so they get the generic guard template with a caption that
# says as much instead of pretending to fix the reasoning.
_NON_CODE_FAMILIES = {
    F.REASONING.value,
    F.HALLUCINATION.value,
    F.PLANNING.value,
    F.INCORRECT_OUTPUT.value,
    F.SAFETY.value,
}
NON_CODE_FAMILIES = _NON_CODE_FAMILIES

TEMPLATES: dict[str, str] = {
    F.TIMEOUT.value: _TIMEOUT_TEMPLATE,
    F.TOOL_EXECUTION.value: _TOOL_TEMPLATE,
    F.API.value: _API_TEMPLATE,
    F.INFINITE_LOOP.value: _LOOP_TEMPLATE,
    F.MEMORY_CONTEXT.value: _CONTEXT_TEMPLATE,
    F.AUTH.value: _AUTH_TEMPLATE,
    F.DATABASE.value: _DATABASE_TEMPLATE,
    GENERIC_CATEGORY: _GENERIC_TEMPLATE,
}
# Families whose failure is a wrong answer rather than a wrong call resolve to
# the generic guard; listing them here keeps "has a snippet" true for every
# family while the caption stays honest about what the code can and cannot do.
TEMPLATES.update({category: _GENERIC_TEMPLATE for category in _NON_CODE_FAMILIES})

LANGUAGES: dict[str, str] = {
    F.TIMEOUT.value: "python",
    F.TOOL_EXECUTION.value: "python",
    F.API.value: "python",
    F.INFINITE_LOOP.value: "python",
    F.MEMORY_CONTEXT.value: "python",
    F.AUTH.value: "python",
    F.DATABASE.value: "python",
    GENERIC_CATEGORY: "python",
}

CAPTIONS: dict[str, str] = {
    F.TIMEOUT.value: "Raise the timeout for this tool and retry with backoff",
    F.TOOL_EXECUTION.value: "Return tool failures as data the agent can act on",
    F.API.value: "Back off and retry the rate-limited API call",
    F.INFINITE_LOOP.value: "Cap the loop and detect a repeated state",
    F.MEMORY_CONTEXT.value: "Budget the prompt and summarise what does not fit",
    F.AUTH.value: "Fail fast on rejected credentials, never retry",
    F.DATABASE.value: "Bounded connect with a health check and a transient-only retry",
    GENERIC_CATEGORY: GENERIC_CAPTION,
}


def _label(facts: dict[str, Any]) -> str:
    """A short, factual pointer to the code that needs changing."""
    tool = facts.get("tool_name")
    step = facts.get("step_number")
    if tool and step:
        return f"`{tool}` (step {step})"
    if tool:
        return f"`{tool}`"
    labels = {"llm": "LLM call", "tool_call": "tool call", "error": "error record", "task": "task", "retry": "retry"}
    raw = facts.get("step_type") or "step"
    step_type = labels.get(raw, raw.replace("_", " "))
    if step_type[:1].isupper():
        return f"{step_type} (step {step})"
    article = "an" if step_type[:1].lower() in "aeiou" else "a"
    return f"{article} {step_type} (step {step})"


COMMENT_WIDTH = 70


def _wrap_all(code: str) -> str:
    return "\n".join(_wrap_comment(line) for line in code.split("\n"))


def _tidy(code: str) -> str:
    """Collapse runs of blank lines a wrapped comment can leave behind."""
    out: list[str] = []
    for line in code.split("\n"):
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line.rstrip())
    return "\n".join(out)


def _wrap_comment(line: str, width: int = COMMENT_WIDTH) -> str:
    """Wrap a comment line at ``width``; every other line is left untouched."""
    stripped = line.lstrip()
    if not stripped.startswith("#") or len(line) <= width:
        return line
    indent = line[: len(line) - len(stripped)]
    text = stripped.lstrip("#").strip()
    words, out, current = text.split(), [], ""
    for word in words:
        if current and len(indent) + 2 + len(current) + 1 + len(word) > width:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return "\n".join(f"{indent}# {part}" for part in out)


def _callable_name(tool: str) -> str:
    """The tool as a python name; a spaced name falls back to the parameter."""
    return tool if tool and re.fullmatch(r"[A-Za-z_]\w*", tool) else "tool"


def _func_name(tool: str | None) -> str:
    """A safe python identifier from a tool name."""
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", tool or "tool").strip("_").lower() or "tool"
    return f"_{name}" if name[0].isdigit() else name


def build_snippet(category: str, facts: dict[str, Any]) -> dict[str, str] | None:
    """The code for one failure family; the generic guard when there is none."""
    template = TEMPLATES.get(category)
    if template is not None and facts.get("http_library") == "requests" and category == F.TIMEOUT.value:
        # requests is the library this trace named: no httpx import, and the
        # client is a Session rather than an httpx.Client.
        template = template.replace("import httpx\n", "import requests\n")
        template = template.replace("import requests\n{extra_import}", "import requests\n{extra_import}")

    if template is None:
        if category not in _NON_CODE_FAMILIES:
            return None
        category = GENERIC_CATEGORY
        template = TEMPLATES[GENERIC_CATEGORY]
    tool = facts.get("tool_name") or "the tool call"
    values = {
        "step_number": facts.get("step_number") or 1,
        # Validated once, in extract_facts: the template never sees a raw value.
        "tool_name": facts.get("tool_display") or "the tool call",
        "func_name": _func_name(facts.get("tool_identifier") or facts.get("tool_name") or "call"),
        "timeout_seconds": facts.get("timeout_seconds") or 30,
        "suggested_timeout_seconds": facts.get("suggested_timeout_seconds") or 60,
        "http_status": facts.get("http_status") or 429,
        "retry_after_seconds": facts.get("retry_after_seconds") or 0,
        "retry_after_default": float(facts.get("retry_after_seconds") or 1),
        # An auth failure is 401/403 by definition; a mis-read status (a 429
        # in the same message) must not appear in the credentials snippet.
        "auth_status": facts.get("http_status") if facts.get("http_status") in (401, 403) else "401",
        "suggested_context_tokens": max(8000, int((facts.get("context_tokens") or 8192) * 0.75)),
        "caught": facts.get("caught_exception") or "Exception",
        # When the trace does not name the type we catch broadly, so the
        # snippet has to decide what counts as a timeout itself.
        # What counts as a timeout at the boundary. When the trace names the
        # exception, the catch already picked it; otherwise the name decides.
        "timeout_guard": (
            "\n            # Only a timeout is worth waiting out.\n"
            "            name = type(exc).__name__.lower()\n"
            "            if not any(w in name for w in (\"timeout\", \"timedout\")):\n"
            "                raise"
            if (facts.get("caught_exception") or "Exception") == "Exception"
            else ""
        ),
        "exception_import": facts.get("exception_import") or "",
        # One HTTP library per page: the one the trace's own exception names.
        # A trace that raised httpx.ReadTimeout gets an httpx snippet; one that
        # raised requests.exceptions.ReadTimeout gets a requests snippet.
        "timeout_constructor": (
            "httpx.Timeout(\n    connect=10.0,\n    read={seconds}.0,\n    write=10.0,\n    pool=10.0,\n)".format(
                seconds=facts.get("suggested_timeout_seconds") or 60
            )
            if facts.get("http_library") != "requests"
            else "(10.0, {seconds}.0)".format(seconds=facts.get("suggested_timeout_seconds") or 60)
        ),
        "client_line": (
            # A requests Session takes no timeout at construction: pass it per call.
            "_client = requests.Session()\n"
            "REQUEST_TIMEOUT = TOOL_TIMEOUT  # _client.get(url, timeout=REQUEST_TIMEOUT)"
            if facts.get("http_library") == "requests"
            else "_client = httpx.Client(timeout=TOOL_TIMEOUT)"
        ),
        # Never print "import httpx" twice: the template already imports it.
        "extra_import": (
            ""
            if (facts.get("exception_import") or "") in ("", "import httpx", "import requests")
            else (
                facts["exception_import"]
                + (
                    "\nimport requests  # the attribute access below needs the package"
                    if facts["exception_import"].startswith("from requests.exceptions")
                    and facts.get("http_library") != "requests"
                    else ""
                )
            )
        ),
        "timeout_branch_comment": (
            "# The trace does not name its exception type, so the boundary below\n"
            "# catches Exception and the timeout is decided by isinstance."
            if (facts.get("caught_exception") or "Exception") == "Exception"
            else ""
        ),
        "exception_note": (
            f"The trace raised {facts['unknown_exception']}; import it above."
            if facts.get("unknown_exception")
            else (
                f"The trace raised {facts.get('caught_exception')}."
                if (facts.get("caught_exception") or "Exception") != "Exception"
                else "The trace does not name its exception; catch broadly here."
            )
        ),
        "tool_ref": _callable_name(str(facts.get("tool_identifier") or "")),
        "database_clause": f" on {facts['database_name']}" if facts.get("database_name") else "",
        "category_label": category.replace("_", " "),
    }
    clause = (
        f'The server asked for {values["retry_after_seconds"]}s before the next attempt — honour it.'
        if facts.get("retry_after_seconds")
        else "Honour the Retry-After header when there is one."
    )
    # The framework and the trace's own framework name sit under every snippet:
    # the code is generic Python and says so, rather than pretending to be the
    # agent's exact codebase.
    framework = safe_label(facts.get("framework"))
    code = template.format(**values, retry_after_clause=clause)
    # A trace can name its own exception class; then there is no import line to
    # emit and the comment says where it has to come from.
    if not values["exception_import"]:
        code = code.replace("\n\n\n# Step", "\n\n# Step", 1)
    # Some facts should not be compared with == False at the catch site.
    if category in (F.TIMEOUT.value, F.DATABASE.value) and values["caught"] == "Exception":
        code = code.replace(
            '        except Exception as exc:          # any tool failure: the boundary branch below',
            '        except Exception as exc:',
        )
    # A plain string, not an expression: the provider name belongs in the
    # message, not in the f-string as a name Python would try to resolve.
    code = code.replace("{HOST}", safe_text(facts.get("host")) or "the provider")
    # Generated comment lines wrap at ~70 columns so a long error message does
    # not turn into one very wide line in a code block that does not scroll.
    code = _tidy(_wrap_all(code))
    # The note ships as its own field so the page renders it as a caption
    # instead of a syntax-highlighted line; the code stays runnable as-is.
    note = f"Generic Python - adapt to your framework ({framework})"
    return {"language": LANGUAGES.get(category, "python"), "code": code.rstrip() + "\n", "note": note}


def build_fix(
    recommendation: dict[str, Any],
    facts: dict[str, Any],
    categories: list[str],
) -> dict[str, Any] | None:
    """Attach the best snippet to a recommendation.

    A recommendation is connected to a snippet through the failure families it
    was selected for: ``retry-with-backoff`` fired for a timeout gets the
    timeout template, not the database one.
    """
    preferred = [c for c in recommendation.get("for_categories", []) if c in TEMPLATES]
    if not preferred:
        preferred = [c for c in categories if c in TEMPLATES]
    category = preferred[0] if preferred else _generic_category(recommendation, categories)
    if category in _NON_CODE_FAMILIES:
        # No mechanical patch exists for a wrong answer: the generic guard is
        # what the card shows, and the caption says so.
        category = GENERIC_CATEGORY
    snippet = build_snippet(category, facts)
    if snippet is None:
        return None
    return _fix_payload(recommendation, category, snippet, facts)


def _generic_category(recommendation: dict[str, Any], categories: list[str]) -> str:
    """The family a recommendation belongs to even when it has no code template.

    Every failure family gets a snippet: the code is generic, but the *caption*
    and the title still say which family the change is for, so the card is
    honest rather than empty.
    """
    for category in recommendation.get("for_categories", []):
        if category != GENERIC_CATEGORY:
            return category
    return categories[0] if categories else GENERIC_CATEGORY


def _fix_payload(
    recommendation: dict[str, Any],
    category: str,
    snippet: dict[str, str],
    facts: dict[str, Any],
) -> dict[str, Any]:
    specific = [_label(facts)]
    if facts.get("timeout_seconds"):
        specific.append(f"timed out at {facts['timeout_seconds']}s")
    if facts.get("http_status"):
        specific.append(f"HTTP {facts['http_status']}")
    return {
        "id": recommendation.get("id"),
        "title": recommendation.get("fix") or recommendation.get("problem"),
        "why": recommendation.get("reason") or recommendation.get("benefit"),
        "priority": recommendation.get("priority"),
        "for_categories": recommendation.get("for_categories", []),
        "applies_to": specific,
        "snippet": snippet,
        "caption": CAPTIONS.get(category, GENERIC_CAPTION),
        "source": "template",
        "family": category,
        "generic": category == GENERIC_CATEGORY,
    }


def fixes_for(
    recommendations: list[dict[str, Any]],
    trace: dict[str, Any],
    failed_index: int | None,
    categories: list[str],
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Recommended fixes with code, most specific first, each redacted."""
    facts = extract_facts(trace, failed_index, categories)
    fixes: list[dict[str, Any]] = []
    seen_templates: set[str] = set()
    for recommendation in recommendations:
        fix = build_fix(recommendation, facts, categories)
        if fix is None:
            continue
        # Two recommendations backed by the same family would paste the same
        # code twice; keep the first.
        family = fix["snippet"]["code"][:40]
        if family in seen_templates:
            continue
        seen_templates.add(family)
        fixes.append(fix)
        if len(fixes) >= limit:
            break
    return [redact_fix(fix) for fix in fixes]


def redact_fix(fix: dict[str, Any]) -> dict[str, Any]:
    """Snippets are shown and copied verbatim: they must not carry a key."""
    secret = settings.llm_api_key
    out = dict(fix)
    for field in ("title", "why", "caption"):
        if isinstance(out.get(field), str):
            out[field] = redact_secret(out[field], secret)[0]
    if isinstance(out.get("snippet"), dict):
        out["snippet"] = {
            **out["snippet"],
            "code": redact_secret(out["snippet"].get("code", ""), secret)[0],
        }
    return out


# --------------------------------------------------------------------------- #
# Optional LLM refinement: validated, redacted, or dropped
# --------------------------------------------------------------------------- #

MAX_REFINED_CODE_CHARS = 4000


class RefinementRejected(ValueError):
    """The refined code is not something we are willing to show."""


def validate_refined_code(code: str, *, fallback: str | None = None) -> str:
    """Accept LLM-written Python only if it parses and imports nothing new.

    The model's output is untrusted: it may be redacted, truncated, or contain
    instructions copied out of the trace. It is checked here and, when it fails,
    the deterministic template is returned instead. The app never executes the
    result, but a snippet that does not parse is not worth showing either.
    """
    import ast

    text = (code or "").strip()
    if not text or len(text) > MAX_REFINED_CODE_CHARS:
        raise RefinementRejected("empty or too long")
    if "```" in text:
        # A fenced block is a formatting mistake, not code.
        raise RefinementRejected("markdown fence")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise RefinementRejected(f"does not parse: {exc.msg}") from exc

    allowed_modules = {
        "time",
        # No "os": a snippet does not need the filesystem or a shell, and it is
        # the shortest route from generated text to a command.

        "json",
        "httpx",
        "requests",
        "sqlalchemy",
        "typing",
        "logging",
        "asyncio",
        "random",
        "math",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root and root not in allowed_modules:
                    raise RefinementRejected(f"imports {name}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"eval", "exec", "compile", "__import__", "open", "system", "popen"}:
                raise RefinementRejected(f"calls {node.func.id}")
    return text


def refine_fix(fix: dict[str, Any], suggestion: str | None) -> dict[str, Any]:
    """Swap in an LLM-written snippet only if it survives every check.

    On any failure the deterministic snippet stays exactly as generated, so the
    page can always show runnable code.
    """
    if not suggestion:
        return fix
    snippet = fix.get("snippet") or {}
    original = snippet.get("code") or ""
    try:
        cleaned = redact_secret(suggestion, settings.llm_api_key)[0]
        code = validate_refined_code(cleaned, fallback=original)
    except (RefinementRejected, ValueError):
        return fix
    if not code.strip():
        return fix
    return {
        **fix,
        "snippet": {**snippet, "code": code.rstrip() + "\n"},
        "source": "llm+template",
        "fallback_code": original,
    }
