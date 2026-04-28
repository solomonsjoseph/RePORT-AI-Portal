"""Tests for the study-load model-version allowlist."""

from __future__ import annotations

import pytest

from scripts.ai_assistant.ui import model_policy


@pytest.mark.parametrize(
    "provider, model",
    [
        ("ollama", "qwen3:8b"),
        ("ollama", "mistral:latest"),
        ("anthropic", "claude-opus-4-7"),
        ("anthropic", "claude-opus-4-6"),
        ("anthropic", "claude-opus-5-0"),
        ("anthropic", "claude-opus-4-6-20251101"),
        ("google-genai", "gemini-3.1-pro"),
        ("google-genai", "gemini-3.1-pro-preview"),
        ("google-genai", "gemini-3.2-pro"),
        ("google-genai", "gemini-4-pro"),  # next major
        ("openai", "gpt-5-3"),
        ("openai", "gpt-5.5"),
        ("openai", "gpt-5-4-2026-01"),
        ("openai", "gpt-6"),
    ],
)
def test_allowed(provider: str, model: str) -> None:
    res = model_policy.is_model_allowed_for_study_load(provider=provider, model=model)
    assert res.allowed, res.reason


@pytest.mark.parametrize(
    "provider, model",
    [
        ("anthropic", "claude-opus-4-5-20251101"),  # below 4.6
        ("anthropic", "claude-sonnet-4-6"),  # sonnet is not on allowlist
        ("anthropic", "claude-haiku-4-5-20251001"),
        ("google-genai", "gemini-2.5-pro"),  # below 3.1
        ("google-genai", "gemini-3-pro"),  # parses as 3.0 — below 3.1
        ("google-genai", "gemini-3-flash"),  # wrong family
        ("openai", "gpt-4.1"),  # below 5.3
        ("openai", "gpt-5-2"),  # below 5.3
        ("openai", "gpt-4o"),
        ("", ""),  # no model selected
        ("anthropic", ""),  # no model selected
    ],
)
def test_blocked(provider: str, model: str) -> None:
    res = model_policy.is_model_allowed_for_study_load(provider=provider, model=model)
    assert not res.allowed, res.reason


def test_describe_allowlist_mentions_requirements() -> None:
    text = model_policy.describe_allowlist()
    assert "Opus" in text
    assert "Pro" in text
    assert "GPT" in text
    assert "Ollama" in text
