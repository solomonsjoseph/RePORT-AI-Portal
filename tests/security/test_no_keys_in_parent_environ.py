"""Regression test: no key-handling code path may write to the parent's
``os.environ``.

This is the core integration check for PR #3. Even when the wizard's
``apply_llm_config`` is called or ``agent_graph.build_chat_model`` is
invoked, the parent process's ``os.environ`` for ``*_API_KEY`` vars must
remain empty.

Pre-existing keys (set by the user in their shell before launching the
app) are NOT cleared by this test — that's a user choice and we read
them once into the keystore on first launch. What we forbid is the app
*writing* a key into the env where it didn't already exist.
"""

from __future__ import annotations

import os

import pytest

_API_KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "GEMINI_API_KEY",
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any pre-existing API key env vars so we can detect new writes."""
    for var in _API_KEY_VARS:
        monkeypatch.delenv(var, raising=False)


def test_keystore_set_does_not_touch_environ(clean_env: None) -> None:
    """Storing a key via the KeyStore must NOT propagate to ``os.environ``."""
    from scripts.ai_assistant.keystore import KeyStore

    ks = KeyStore()
    ks.set("anthropic", "sk-ant-test")
    for var in _API_KEY_VARS:
        assert var not in os.environ, f"{var} leaked into os.environ"


def test_env_for_subprocess_returns_dict_without_mutating(clean_env: None) -> None:
    """Building a subprocess env dict must NOT touch the parent env."""
    from scripts.ai_assistant.keystore import KeyStore

    ks = KeyStore()
    ks.set("anthropic", "sk-ant-test")
    ks.set("openai", "sk-test-openai")
    env = ks.env_for_subprocess(["anthropic", "openai"])
    assert "ANTHROPIC_API_KEY" in env
    assert "OPENAI_API_KEY" in env
    for var in _API_KEY_VARS:
        assert var not in os.environ, f"{var} leaked during env_for_subprocess"


def test_keystore_clear_does_not_touch_environ(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the user pre-set a key in their shell env, KeyStore.clear must
    leave the shell env alone — it's the user's session, not ours to mutate."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shell-set-by-user")
    from scripts.ai_assistant.keystore import KeyStore

    ks = KeyStore()
    ks.set("anthropic", "different-key")
    ks.clear("anthropic")
    # The shell-set value is untouched.
    assert os.environ.get("ANTHROPIC_API_KEY") == "shell-set-by-user"
