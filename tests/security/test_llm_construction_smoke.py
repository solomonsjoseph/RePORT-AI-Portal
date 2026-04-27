"""End-to-end smoke tests: actually instantiate the chat-model objects.

These tests are deliberately NOT mocked. The unit-test suite as it stood
through PR #6 mocked every LangChain integration point, which let
PR #6's removal of ``langchain-community`` ship green even though it
broke the live ``from langchain.chat_models import init_chat_model``
import the wizard's whole LLM flow depends on.

This module validates the *real* object graph:

1. ``langchain.chat_models.init_chat_model`` is importable. (The bug
   PR #6 introduced was exactly this import returning
   ``ModuleNotFoundError`` because the ``langchain`` meta-package was
   no longer pulled in transitively.)
2. ``scripts.ai_assistant.agent_graph._build_llm(provider, model)`` can
   construct a chat-model object for every provider the wizard exposes,
   using a fake key from the KeyStore.

These tests use placeholder keys — no network calls are made; the
constructors return without contacting the provider. If a future
LangChain bump removes ``init_chat_model`` for real, this suite fails
on every provider at once and the regression is impossible to miss.
"""

from __future__ import annotations

import importlib

import pytest


def test_langchain_chat_models_init_chat_model_is_importable() -> None:
    """Direct regression for the PR #6 breakage: ``from langchain.chat_models
    import init_chat_model`` must succeed. If this import fails, every
    non-Ollama LLM provider is broken."""
    chat_models = importlib.import_module("langchain.chat_models")
    assert hasattr(chat_models, "init_chat_model"), (
        "init_chat_model missing — every provider in agent_graph._build_llm "
        "will fail. Verify ``langchain`` is in pyproject.toml ai_assistant group."
    )


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("anthropic", "claude-sonnet-4-6"),
        ("openai", "gpt-4.1"),
        ("google-genai", "gemini-2.5-flash"),
    ],
)
def test_build_llm_constructs_chat_model_for_provider(
    provider: str, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_build_llm`` must return a usable client object for every supported
    provider. We use a placeholder key (no network call); the test only
    proves construction succeeds — i.e. the import path, the api_key
    routing, and the LangChain version are all consistent."""
    # Strip any real shell-set keys so the test exercises the KeyStore path,
    # not the SDK auto-pickup.
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "NVIDIA_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    from scripts.ai_assistant.keystore import KeyStore, provider_slug_for

    slug = provider_slug_for(provider)
    assert slug is not None, f"provider_slug_for({provider!r}) returned None"

    ks = KeyStore()
    ks.set(slug, "sk-test-placeholder-not-a-real-key")

    # Patch get_keystore() to return our test instance.
    import scripts.ai_assistant.keystore as keystore_mod

    monkeypatch.setattr(keystore_mod, "get_keystore", lambda: ks)

    # Force re-import of agent_graph so it picks up the patched keystore
    # accessor (it imports get_keystore at function-call time, so this is
    # belt-and-braces).
    from scripts.ai_assistant.agent_graph import _build_llm

    client = _build_llm(provider, model)
    assert client is not None
    assert client.__class__.__name__.startswith("Chat"), (
        f"expected a Chat* client, got {type(client).__name__}"
    )


def test_build_llm_for_ollama_works_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama is local — no API key needed; ``_build_llm`` should still
    construct successfully (and not crash on the missing keystore entry)."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    from scripts.ai_assistant.agent_graph import _build_llm

    # Ollama returns ChatOllama; we only test construction, not connectivity.
    client = _build_llm("ollama", "qwen3:8b")
    assert client is not None
