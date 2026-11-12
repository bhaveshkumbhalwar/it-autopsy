"""Small, dependency-free helpers shared by the ingestion layer."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class ParseError(ValueError):
    """Raised when uploaded content cannot be interpreted as a trace."""

    def __init__(self, message: str, line: int | None = None, hint: str | None = None):
        self.message = message
        self.line = line
        self.hint = hint
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"message": self.message, "line": self.line, "hint": self.hint}


# Fields that differ between two submissions of the same run: the id is minted
# per submission and the hash is derived from this very dict.
_HASH_EXCLUDED = {"execution_id", "content_hash", "started_at", "ended_at"}


def trace_content_hash(trace) -> str:
    """Hash of a trace's content, stable across submissions of the same run."""
    return stable_hash({k: v for k, v in trace.to_dict().items() if k not in _HASH_EXCLUDED})


def stable_hash(value: Any) -> str:
    """Content hash of a JSON-like value, insensitive to key order.

    Used to recognise the same trace submitted twice (paste, upload or demo) so
    that a repeated analysis updates one record instead of piling up duplicates.
    """
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json_or_jsonl(text: str) -> tuple[list[Any], str]:
    """Parse text as JSON, JSONL/NDJSON, or a text log.

    Returns (records, kind) where kind is "json" or "jsonl".
    Raises ParseError with a line number when a JSONL line is malformed.
    """
    stripped = text.strip()
    if not stripped:
        raise ParseError("The input is empty.", hint="Paste a trace or upload a .json/.jsonl file.")

    if stripped[0] in "[{":
        try:
            parsed = json.loads(stripped)
            return ([parsed] if isinstance(parsed, dict) else list(parsed)), "json"
        except json.JSONDecodeError as exc:
            # Fall through to JSONL: a very common case is one object per line.
            if "\n" in stripped:
                pass
            else:
                raise ParseError(
                    f"Invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.",
                    line=exc.lineno,
                    hint="Check for trailing commas or unquoted keys.",
                ) from exc

    records: list[Any] = []
    for number, line in enumerate(stripped.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ParseError(
                f"Invalid JSON on line {number}: {exc.msg} at column {exc.colno}.",
                line=number,
                hint="Each line of a JSONL file must be one complete JSON object.",
            ) from exc
    if not records:
        raise ParseError("No JSON records found in the input.")
    return records, "jsonl"


def get_path(obj: Any, path: str, default: Any = None) -> Any:
    """Safe dotted-path lookup: get_path(d, 'a.b.0.c')."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        elif isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        else:
            return default
    return cur


def first_of(obj: Any, *paths: str, default: Any = None) -> Any:
    for path in paths:
        value = get_path(obj, path, None)
        if value not in (None, "", [], {}):
            return value
    return default


def as_text(value: Any, limit: int = 4000) -> str:
    """Render any JSON-ish value as a readable string, truncated."""
    if value is None:
        return ""
    if isinstance(value, str):
        out = value
    else:
        try:
            out = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            out = str(value)
    return out if len(out) <= limit else out[: limit - 1] + "…"


def parse_ts_str(value: Any):
    from app.core.models import parse_timestamp

    return parse_timestamp(value)


def ms_between(start: Any, end: Any, parse_ts=None) -> float | None:
    parse_ts = parse_ts or parse_ts_str
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() * 1000.0, 2)


def duration_from(value: Any) -> float | None:
    """Interpret a duration value as milliseconds (heuristics for ns/us)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    if num > 1e12:  # nanoseconds
        return round(num / 1e6, 2)
    if num > 1e9:  # microseconds
        return round(num / 1e3, 2)
    return round(num, 2)
