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

    return {
        "step_index": failed_index,
        "step_number": (failed_index + 1) if isinstance(failed_index, int) else None,
        "step_type": step_type,
        "tool_name": tool,
        "timeout_seconds": int(timeout) if timeout else None,
        "suggested_timeout_seconds": int(max(60, (timeout or 30) * 2)),
        "http_status": http_status,
        "retry_after_seconds": int(retry_after) if retry_after else None,
        "host": host,
        "context_tokens": context_tokens,
        "database_name": (_DB_NAME_RE.search(text).group(1) if _DB_NAME_RE.search(text) else None),
        "table_name": (next((m.group(1) for m in [_TABLE_RE.search(str(args.get(k, ""))) for k in args] if m), None)),
        "categories": list(categories),
    }


# --------------------------------------------------------------------------- #
# Templates, one per failure family
# --------------------------------------------------------------------------- #

_TIMEOUT_TEMPLATE = '''import time
import httpx

# Step {step_number} failed: {tool_name} timed out after {timeout_seconds}s.
# Give it room, and retry the transient case instead of failing the run.
TOOL_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read={suggested_timeout_seconds}.0,
    write=10.0,
    pool=10.0,
)
MAX_ATTEMPTS = 3

def call_{func_name}(*args, **kwargs):
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=TOOL_TIMEOUT) as client:
                return client.get(*args, **kwargs)
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            last_error = exc
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
MAX_ATTEMPTS = 2

def run_{func_name}(arguments: dict):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = {tool_name}(**arguments)          # your real tool
            if result is None:
                raise ValueError("{tool_name} returned no result")
            return {{"ok": True, "data": result, "attempts": attempt}}
        except (ValueError, ConnectionError, TimeoutError) as exc:
            if attempt == MAX_ATTEMPTS:
                # Hand the failure back to the agent as data, do not raise.
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

_AUTH_TEMPLATE = '''# Step {step_number}: the call was rejected ({http_status}) — credentials, not
# transient load. Do not retry: fail fast and say which credential.
def require_credentials(value: str | None, name: str) -> str:
    if not value:
        raise PermissionError(f"{{name}} is not set (see backend/.env)")
    return value

API_KEY = require_credentials(os.environ.get("SERVICE_API_KEY"), "SERVICE_API_KEY")

def call_service(...):
    response = client.get(..., headers={{"Authorization": f"Bearer {{API_KEY}}"}})
    if response.status_code in (401, 403):
        raise PermissionError(
            f"Credentials rejected by {{HOST}} (HTTP {{response.status_code}}) "
            f"at step {step_number} — rotate the key or fix its scope; retrying will not help."
        )
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
        except (OperationalError, DBAPIError) as exc:
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

TEMPLATES: dict[str, str] = {
    F.TIMEOUT.value: _TIMEOUT_TEMPLATE,
    F.TOOL_EXECUTION.value: _TOOL_TEMPLATE,
    F.API.value: _API_TEMPLATE,
    F.INFINITE_LOOP.value: _LOOP_TEMPLATE,
    F.MEMORY_CONTEXT.value: _CONTEXT_TEMPLATE,
    F.AUTH.value: _AUTH_TEMPLATE,
    F.DATABASE.value: _DATABASE_TEMPLATE,
}

LANGUAGES: dict[str, str] = {
    F.TIMEOUT.value: "python",
    F.TOOL_EXECUTION.value: "python",
    F.API.value: "python",
    F.INFINITE_LOOP.value: "python",
    F.MEMORY_CONTEXT.value: "python",
    F.AUTH.value: "python",
    F.DATABASE.value: "python",
}

CAPTIONS: dict[str, str] = {
    F.TIMEOUT.value: "Raise the timeout for this tool and retry with backoff",
    F.TOOL_EXECUTION.value: "Return tool failures as data the agent can act on",
    F.API.value: "Back off and retry the rate-limited API call",
    F.INFINITE_LOOP.value: "Cap the loop and detect a repeated state",
    F.MEMORY_CONTEXT.value: "Budget the prompt and summarise what does not fit",
    F.AUTH.value: "Fail fast on rejected credentials, never retry",
    F.DATABASE.value: "Bounded connect with a health check and a transient-only retry",
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


def _func_name(tool: str | None) -> str:
    """A safe python identifier from a tool name."""
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", tool or "tool").strip("_").lower() or "tool"
    return f"_{name}" if name[0].isdigit() else name


def build_snippet(category: str, facts: dict[str, Any]) -> dict[str, str] | None:
    """The code for one failure family, or None when it has no template."""
    template = TEMPLATES.get(category)
    if template is None:
        return None
    tool = facts.get("tool_name") or "the tool call"
    values = {
        "step_number": facts.get("step_number") or 1,
        "tool_name": tool if facts.get("tool_name") else "this call",
        "func_name": _func_name(str(facts.get("tool_name") or "call")),
        "timeout_seconds": facts.get("timeout_seconds") or 30,
        "suggested_timeout_seconds": facts.get("suggested_timeout_seconds") or 60,
        "http_status": facts.get("http_status") or 429,
        "retry_after_seconds": facts.get("retry_after_seconds") or 0,
        "retry_after_default": float(facts.get("retry_after_seconds") or 1),
        "suggested_context_tokens": max(8000, int((facts.get("context_tokens") or 8192) * 0.75)),
        "database_clause": f" on {facts['database_name']}" if facts.get("database_name") else "",
        "category_label": category.replace("_", " "),
    }
    clause = (
        f'The server asked for {values["retry_after_seconds"]}s before the next attempt — honour it.'
        if facts.get("retry_after_seconds")
        else "Honour the Retry-After header when there is one."
    )
    code = template.format(**values, retry_after_clause=clause)
    if values["http_status"] and "HOST" in code:
        code = code.replace("HOST", facts.get("host") or "the provider")
    return {"language": LANGUAGES.get(category, "python"), "code": code.rstrip() + "\n"}


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
    if not preferred:
        return None
    category = preferred[0]
    snippet = build_snippet(category, facts)
    if snippet is None:
        return None
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
        "caption": CAPTIONS.get(category, "A concrete change for this failure"),
        "source": "template",
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
