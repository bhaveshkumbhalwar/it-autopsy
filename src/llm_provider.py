"""Provider-agnostic LLM interface.

The agent works entirely without a language model (``NullProvider``).  This
module provides a minimal interface so that an LLM can be added later without
rewriting the agent.

Modes
-----
``deterministic`` (default, no API key required)
    Uses ``NullProvider``, which returns empty strings.  The agent falls back
    to its deterministic rule-based analysis for every step.

``llm`` (optional, requires environment configuration)
    Uses ``OpenAICompatibleProvider``, which calls any OpenAI-API-compatible
    endpoint.  Configure with environment variables (see ``.env.example``).
    The provider is never given permission to execute commands or call tools
    directly; all decisions remain in ``src.agent`` under policy control.

Security notes
--------------
* The provider never receives raw tool output or user PII; the agent passes
  only the redacted issue text and its own structured questions.
* Model output is parsed strictly; anything that does not match the expected
  schema is discarded and the agent falls back to its deterministic path.
* No API key is ever committed to source control; the key is read from the
  environment at runtime.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Minimal interface shared by all providers."""

    @abstractmethod
    def complete(self, prompt: str, *, system: str = "") -> str:
        """Return the model's text completion for ``prompt``.

        Providers must:
        * Return a plain string (may be empty).
        * Never raise on recoverable errors (network, timeout, quota);
          return empty string instead and log internally.
        * Never execute commands or make side effects.
        """

    def is_available(self) -> bool:
        """Return True if this provider can make real calls."""
        return False

    @property
    def mode_label(self) -> str:
        """Short label displayed in the UI."""
        return "unknown"


class NullProvider(LLMProvider):
    """Deterministic no-op provider.

    Returns empty strings for every completion. The agent's rule-based
    analysis handles everything; no API key is required.
    """

    def complete(self, prompt: str, *, system: str = "") -> str:  # noqa: D102
        return ""

    def is_available(self) -> bool:  # noqa: D102
        return False

    @property
    def mode_label(self) -> str:
        return "deterministic local baseline (no LLM)"


class OpenAICompatibleProvider(LLMProvider):
    """Provider for any OpenAI-API-compatible endpoint.

    Configuration (environment variables; never commit to source control):

    ``LLM_API_KEY``   — Required. API key for the endpoint.
    ``LLM_BASE_URL``  — Optional. Base URL (default: https://api.openai.com/v1).
    ``LLM_MODEL``     — Optional. Model name (default: gpt-4o-mini).
    ``LLM_TIMEOUT``   — Optional. Request timeout in seconds (default: 30).

    The provider falls back to returning empty strings on any error so that
    the deterministic pipeline can continue.
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("LLM_API_KEY", "")
        self._base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self._model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self._timeout = int(os.environ.get("LLM_TIMEOUT", "30"))

    def is_available(self) -> bool:  # noqa: D102
        return bool(self._api_key)

    @property
    def mode_label(self) -> str:
        return f"LLM-assisted ({self._model})"

    def complete(self, prompt: str, *, system: str = "") -> str:
        """Call the endpoint and return text, or empty string on any error."""
        if not self._api_key:
            return ""
        try:
            import urllib.request

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = json.dumps(
                {
                    "model": self._model,
                    "messages": messages,
                    "max_tokens": 512,
                    "temperature": 0,
                }
            ).encode()
            req = urllib.request.Request(
                f"{self._base_url}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode())
            return str(body["choices"][0]["message"]["content"])
        except Exception:  # noqa: BLE001 – never propagate; caller has fallback
            return ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> LLMProvider:
    """Return a provider based on the ``LLM_MODE`` environment variable.

    ``LLM_MODE=deterministic`` (default) → ``NullProvider``
    ``LLM_MODE=llm``                     → ``OpenAICompatibleProvider``

    The ``OpenAICompatibleProvider`` also requires ``LLM_API_KEY`` to make
    real calls; if the key is absent it behaves like a ``NullProvider``.
    """
    mode = os.environ.get("LLM_MODE", "deterministic").strip().lower()
    if mode == "llm":
        return OpenAICompatibleProvider()
    return NullProvider()


# ---------------------------------------------------------------------------
# Schema validator for LLM-produced structured output
# ---------------------------------------------------------------------------

_ALLOWED_TOPICS = frozenset(
    {
        "bluetooth",
        "wifi",
        "dns",
        "network",
        "performance",
        "software-install",
        "account-access",
        "vpn",
        "email",
        "hardware",
        "other",
    }
)


def parse_issue_analysis_json(raw: str) -> dict | None:
    """Parse and validate the IssueAnalysis JSON produced by an LLM.

    Returns the parsed dict if the JSON is valid and contains the required
    fields; returns ``None`` if parsing fails or the output is unsafe.

    The validator is strict: unknown fields are stripped, topic values are
    checked against an allowlist, and string values are length-limited to
    prevent prompt-injection payloads from reaching downstream code.
    """
    if not raw:
        return None
    # Extract the first JSON object from the response (models often add prose)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None

    # Validate and sanitise
    cleaned: dict = {}
    if isinstance(data.get("symptoms"), list):
        cleaned["symptoms"] = [str(s)[:200] for s in data["symptoms"][:10]]
    if isinstance(data.get("affected_device"), str):
        cleaned["affected_device"] = data["affected_device"][:100]
    if isinstance(data.get("expected_behavior"), str):
        cleaned["expected_behavior"] = data["expected_behavior"][:200]
    if isinstance(data.get("actual_behavior"), str):
        cleaned["actual_behavior"] = data["actual_behavior"][:200]
    if isinstance(data.get("topics"), list):
        cleaned["topics"] = [t for t in data["topics"] if t in _ALLOWED_TOPICS][:5]
    if isinstance(data.get("missing_info"), list):
        cleaned["missing_info"] = [str(m)[:200] for m in data["missing_info"][:5]]
    if isinstance(data.get("clarification_question"), str):
        cleaned["clarification_question"] = data["clarification_question"][:300]
    if data.get("diagnostic_confidence") in ("high", "medium", "low"):
        cleaned["diagnostic_confidence"] = data["diagnostic_confidence"]
    if data.get("severity") in ("high", "medium", "low"):
        cleaned["severity"] = data["severity"]

    return cleaned or None
