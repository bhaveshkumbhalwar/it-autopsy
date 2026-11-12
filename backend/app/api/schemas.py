"""Pydantic request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class PasteRequest(BaseModel):
    text: str = Field(..., max_length=2_000_000)
    agent_name: str | None = Field(default=None, max_length=200)
    framework: str | None = Field(default=None, max_length=80)
    task_description: str | None = Field(default=None, max_length=8000)
    expected_output: str | None = Field(default=None, max_length=8000)
    use_llm: bool = True
    save: bool = True

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The trace text is empty.")
        return value


class AnalyzeRequest(BaseModel):
    use_llm: bool = True


class DemoIngestRequest(BaseModel):
    demo_id: str = Field(..., max_length=60)
    use_llm: bool = True


class SettingsUpdate(BaseModel):
    theme: str | None = Field(default=None, pattern="^(light|dark|system)$")
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    llm_enabled: bool | None = None
    llm_model: str | None = Field(default=None, max_length=80)
    llm_provider: str | None = Field(default=None, pattern="^(groq|openai|anthropic)$")
    default_use_llm: bool | None = None
    redact_secrets: bool | None = None


class AnalysisResponse(BaseModel):
    execution: dict[str, Any]
    analysis: dict[str, Any]
    format: str | None = None
    warnings: list[str] = Field(default_factory=list)
    redactions: int = 0
