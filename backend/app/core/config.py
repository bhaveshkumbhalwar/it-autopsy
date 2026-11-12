"""Application configuration.

All settings come from environment variables (or a .env file). No secrets are
ever hardcoded. See .env.example for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Agent Autopsy"
    app_version: str = "0.5.0"
    environment: str = "development"
    # Put back any missing sample run at startup. Off in tests, which build
    # their own database and must not find demo rows in it.
    seed_demos: bool = True

    # Database. SQLite by default; set DATABASE_URL=postgresql+psycopg://...
    # to move to PostgreSQL (the SQLAlchemy models are dialect agnostic).
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'agent_autopsy.db'}"

    # LLM integration (optional). When no key is set the analysis engine falls
    # back to rule + ML detection only and says so in the report.
    # Providers: "openai" (any OpenAI-compatible endpoint), "groq"
    # (OpenAI-compatible at https://api.groq.com/openai/v1), "anthropic".
    # LLM_BASE_URL overrides the provider default; LLM_MODEL must be a model the
    # provider serves. An empty provider is inferred from the key prefix.
    llm_provider: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 45.0
    llm_max_input_chars: int = 24000
    # Seconds a "LLM ready" check result is reused before calling the provider.
    llm_health_cache_seconds: int = 300

    # Ingestion limits
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB
    allowed_extensions: tuple[str, ...] = (".json", ".jsonl", ".ndjson", ".txt", ".log")
    redact_secrets: bool = True

    # Retention: traces older than this are purged by the maintenance endpoint.
    retention_days: int = 90

    # Rate limiting (simple in-process token bucket per client IP)
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    rate_limit_analyze: int = 20

    # CORS: the Vite dev server by default.
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )

    # Optional bearer token to protect the API (empty = open, dev only).
    api_token: str = ""

    ml_model_path: str = str(BASE_DIR / "data" / "ml" / "failure_classifier.joblib")


# Provider presets. "openai" has no default model because the endpoint may be
# any OpenAI-compatible server; a model must be configured explicitly.
PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "groq": {"base_url": "https://api.groq.com/openai/v1"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1"},
}

# Key prefixes that identify a provider. Used only to detect a mismatch — the
# key itself is never logged, echoed or returned.
KEY_PREFIXES: dict[str, str] = {"groq": "gsk_", "openai": "sk-", "anthropic": "sk-ant-"}


def resolve_llm(provider: str | None = None, api_key: str | None = None) -> tuple[str, str, str]:
    """Return (provider, base_url, model) with defaults applied.

    Provider resolution order: explicit setting → key prefix → "openai".
    An explicit provider always wins; a mismatch is reported by
    ``llm_config_problem`` rather than silently ignored.
    """
    key = (api_key if api_key is not None else settings.llm_api_key or "").strip()
    explicit = (provider if provider is not None else settings.llm_provider or "").strip().lower()
    if not explicit:
        explicit = next((name for name, prefix in KEY_PREFIXES.items() if key.startswith(prefix)), "openai")
    if explicit not in PROVIDER_DEFAULTS:
        explicit = "openai"
    base_url = (settings.llm_base_url or "").strip() or PROVIDER_DEFAULTS[explicit]["base_url"]
    model = (settings.llm_model or "").strip() or PROVIDER_DEFAULTS[explicit].get("model", "")
    return explicit, base_url.rstrip("/"), model


def llm_config_problem() -> str | None:
    """A human-readable misconfiguration message, or None when the setup is sane.

    Never includes the key: only its shape is described.
    """
    key = (settings.llm_api_key or "").strip()
    if not key:
        return None
    provider, _base_url, model = resolve_llm()
    detected = next((name for name, prefix in KEY_PREFIXES.items() if key.startswith(prefix)), None)
    if detected and detected != provider:
        return (
            f"LLM_PROVIDER is '{provider}' but LLM_API_KEY looks like a {detected} key "
            f"(its prefix is '{KEY_PREFIXES[detected]}…'). "
            + (
                f"Set LLM_PROVIDER={detected} (and a {detected} model name) or supply the {provider} key."
            )
        )
    if not model:
        return (
            f"No LLM_MODEL is configured for provider '{provider}'. "
            "Set LLM_MODEL in backend/.env to a model the provider serves."
        )
    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
