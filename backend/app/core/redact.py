"""Redaction of secrets / PII.

Two uses:

1. **Trace content** — anything the user uploads is scanned before it is stored
   or sent to an LLM. We never execute uploaded content; we only mask what
   should not be persisted.
2. **Every outbound string** — error messages, provider/LLM failures, notes.
   Anything derived from a third party (an LLM provider quoting the key it
   rejected, an exception string containing a URL with a token) passes through
   the same redactor before it is stored, returned by the API or rendered.

``redact_obj`` handles JSON-like structures; ``redact_error`` and
``redact_secret`` cover the provider-error cases and the configured key itself.
"""

from __future__ import annotations

import re
from typing import Any

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,2000}?-----END [A-Z ]*PRIVATE KEY-----")),
    # Provider keys. Order matters: the longer/more specific patterns first, so
    # `sk-ant-…` is not consumed by the generic `sk-` rule.
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9_\-]{8,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{10,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}")),
    ("api_key_param", re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|authorization)=([^&\s\"']{8,})")),
    ("email", re.compile(r"\b[\w.+\-]+@[\w\-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\b\+?\d[\d\s\-().]{9,}\d\b")),
    ("password_field", re.compile(r'(?i)"(password|passwd|secret|api_key|apikey|token)"\s*:\s*"[^"]{4,}"')),
]


def redact_text(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_redactions)."""
    count = 0
    for _name, pattern in PATTERNS:
        def _sub(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return "[REDACTED]"

        text = pattern.sub(_sub, text)
    return text, count


def redact_secret(text: str, secret: str | None) -> tuple[str, int]:
    """Mask one configured secret wherever it appears in ``text``.

    Pattern matching alone is not enough for a live key: a provider may quote a
    truncated or reformatted fragment. If the exact configured value occurs, it
    is removed too. Short values are ignored so that masking cannot turn into a
    substitution of common words.
    """
    if not text:
        return text, 0
    text, count = redact_text(text)
    if secret and len(secret.strip()) >= 8 and secret in text:
        occurrences = text.count(secret)
        text = text.replace(secret, "[REDACTED]")
        count += occurrences
    return text, count


def redact_obj(value: Any, secret: str | None = None) -> Any:
    """Recursively redact string leaves of a JSON-like structure."""
    if isinstance(value, str):
        return redact_secret(value, secret)[0]
    if isinstance(value, list):
        return [redact_obj(v, secret) for v in value]
    if isinstance(value, tuple):
        return [redact_obj(v, secret) for v in value]
    if isinstance(value, dict):
        return {k: redact_obj(v, secret) for k, v in value.items()}
    return value
