"""Tests for ``scripts.utils.llm_capabilities``.

Pins the contract: PDF-extraction LLM tier runs only for capable models.
"""

from __future__ import annotations

import logging

import pytest

from scripts.utils.llm_capabilities import is_capable_model


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no env override so we're testing the default
    allowlist unless the test explicitly opts in."""
    monkeypatch.delenv("REPORTALIN_PDF_LLM_CAPABLE_MODELS", raising=False)


def test_capable_anthropic_opus_passes() -> None:
    assert is_capable_model("anthropic", "claude-opus-4-6") is True
    assert is_capable_model("anthropic", "claude-opus-4-7") is True
    assert is_capable_model("anthropic", "claude-sonnet-4-6") is True


def test_older_anthropic_sonnet_fails() -> None:
    assert is_capable_model("anthropic", "claude-sonnet-3-5") is False
    assert is_capable_model("anthropic", "claude-3-haiku") is False


def test_uncapable_model_debug_log_omits_raw_operator_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = "operator-provider-SENSITIVE-86"
    model = "operator-model-SENSITIVE-86"

    with caplog.at_level(logging.DEBUG, logger="scripts.utils.llm_capabilities"):
        assert is_capable_model(provider, model) is False

    messages = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "scripts.utils.llm_capabilities"
    )
    assert "not in capable allowlist" in messages
    assert provider not in messages
    assert model not in messages


def test_openai_gpt5_passes_gpt4_fails() -> None:
    assert is_capable_model("openai", "gpt-5") is True
    assert is_capable_model("openai", "gpt-5.4") is True
    assert is_capable_model("openai", "gpt-4.1") is False
    assert is_capable_model("openai", "gpt-3.5-turbo") is False


def test_gemini_pro_passes_flash_fails() -> None:
    assert is_capable_model("google-genai", "gemini-2.5-pro") is True
    assert is_capable_model("google-genai", "gemini-2.5-flash") is False
    assert is_capable_model("google-genai", "gemini-1.5-pro") is False


def test_nvidia_405b_passes_smaller_fails() -> None:
    assert is_capable_model("nvidia-ai-endpoints", "meta/llama-3.3-405b-instruct") is True
    assert is_capable_model("nvidia-ai-endpoints", "meta/llama-3.3-70b-instruct") is False
    assert is_capable_model("nvidia-ai-endpoints", "meta/llama-3.1-8b-instruct") is False


def test_ollama_excluded_by_default() -> None:
    """Local Ollama models are excluded regardless of name — capability
    needs explicit operator opt-in via the env override."""
    assert is_capable_model("ollama", "qwen3:8b") is False
    assert is_capable_model("ollama", "claude-opus-4-6") is False  # name doesn't help


def test_ollama_with_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env override DOES enable Ollama-hosted models if the operator has
    validated one."""
    monkeypatch.setenv("REPORTALIN_PDF_LLM_CAPABLE_MODELS", "qwen3:32b,llama-3.3-405b")
    assert is_capable_model("ollama", "qwen3:32b") is True
    assert is_capable_model("ollama", "qwen3:8b") is False  # not on override list


def test_env_override_replaces_default_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The override REPLACES (not extends) the default list. This is the
    explicit contract — operator takes full responsibility."""
    monkeypatch.setenv("REPORTALIN_PDF_LLM_CAPABLE_MODELS", "custom-model")
    assert is_capable_model("anthropic", "custom-model-v1") is True
    # Default-capable models are no longer capable under override.
    assert is_capable_model("anthropic", "claude-opus-4-6") is False


def test_empty_and_none_inputs_return_false() -> None:
    assert is_capable_model(None, "claude-opus-4-6") is False
    assert is_capable_model("anthropic", None) is False
    assert is_capable_model("", "") is False
    assert is_capable_model("anthropic", "") is False


def test_case_insensitive_matching() -> None:
    assert is_capable_model("ANTHROPIC", "CLAUDE-OPUS-4-6") is True
    assert is_capable_model("Anthropic", "Claude-Opus-4-6") is True
