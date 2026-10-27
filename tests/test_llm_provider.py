"""Provider and structured-output tests."""

from __future__ import annotations

import json

from src import llm_provider


def test_null_provider_returns_empty():
    provider = llm_provider.NullProvider()
    assert provider.complete("hello") == ""
    assert provider.is_available() is False
    assert provider.mode_label


def test_parse_issue_analysis_json_accepts_valid_subset():
    raw = json.dumps(
        {
            "symptoms": ["slow"],
            "topics": ["performance", "not-allowed"],
            "clarification_question": "How slow?",
            "diagnostic_confidence": "medium",
            "severity": "low",
            "extra": "ignored",
        }
    )
    parsed = llm_provider.parse_issue_analysis_json(raw)
    assert parsed is not None
    assert parsed["symptoms"] == ["slow"]
    assert parsed["topics"] == ["performance"]
    assert parsed["clarification_question"] == "How slow?"
    assert "extra" not in parsed


def test_parse_issue_analysis_json_rejects_invalid_json():
    assert llm_provider.parse_issue_analysis_json("{not json") is None
    assert llm_provider.parse_issue_analysis_json("") is None


def test_openai_provider_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    provider = llm_provider.OpenAICompatibleProvider()
    assert provider.is_available() is False
    assert provider.complete("hello") == ""
