"""KeyStore unit tests + parent-environ-leak regression.

Two contracts to enforce:

1. Keys live in process memory only — never in ``os.environ``.
2. ``env_for_subprocess(...)`` returns a per-call dict suitable for
   ``subprocess.run(env=...)`` without ever mutating the parent's env.
"""

from __future__ import annotations

import os

import pytest

from scripts.ai_assistant.keystore import ENV_VAR_BY_PROVIDER, KeyStore

# ── Direct KeyStore correctness ────────────────────────────────────────────


def test_set_and_get_round_trip() -> None:
    ks = KeyStore()
    ks.set("anthropic", "sk-ant-test-key-1234")
    assert ks.get("anthropic") == "sk-ant-test-key-1234"
    assert ks.has("anthropic") is True


def test_get_missing_returns_none() -> None:
    ks = KeyStore()
    assert ks.get("openai") is None
    assert ks.has("openai") is False


def test_clear_one_provider() -> None:
    ks = KeyStore()
    ks.set("anthropic", "k1")
    ks.set("openai", "k2")
    ks.clear("anthropic")
    assert ks.has("anthropic") is False
    assert ks.has("openai") is True


def test_clear_all() -> None:
    ks = KeyStore()
    ks.set("anthropic", "k1")
    ks.set("openai", "k2")
    ks.clear()
    assert ks.has("anthropic") is False
    assert ks.has("openai") is False


def test_set_normalises_provider_case() -> None:
    """Case-insensitive provider lookup so ``Anthropic`` and ``anthropic``
    resolve to the same slot — defends against UI inconsistencies."""
    ks = KeyStore()
    ks.set("Anthropic", "k1")
    assert ks.get("anthropic") == "k1"
    assert ks.get("ANTHROPIC") == "k1"


def test_unknown_provider_set_raises() -> None:
    """Only known providers (anthropic/openai/google/nvidia) are storable —
    typos surface immediately, not at next API call."""
    ks = KeyStore()
    with pytest.raises(ValueError):
        ks.set("totally-not-a-provider", "k1")


def test_empty_key_raises() -> None:
    ks = KeyStore()
    with pytest.raises(ValueError):
        ks.set("anthropic", "")


# ── env_for_subprocess: never mutate parent env ─────────────────────────────


def test_env_for_subprocess_returns_only_requested_providers() -> None:
    ks = KeyStore()
    ks.set("anthropic", "k1")
    ks.set("openai", "k2")
    env = ks.env_for_subprocess(["anthropic"])
    assert env == {"ANTHROPIC_API_KEY": "k1"}


def test_env_for_subprocess_skips_unset_providers() -> None:
    ks = KeyStore()
    ks.set("anthropic", "k1")
    env = ks.env_for_subprocess(["anthropic", "openai"])
    assert env == {"ANTHROPIC_API_KEY": "k1"}  # openai not set, skipped


def test_env_for_subprocess_does_not_mutate_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single most important regression test: building a subprocess env
    must NOT pollute the parent's ``os.environ``."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ks = KeyStore()
    ks.set("anthropic", "secret-key")
    _ = ks.env_for_subprocess(["anthropic"])
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_env_for_subprocess_unknown_provider_raises() -> None:
    ks = KeyStore()
    ks.set("anthropic", "k1")
    with pytest.raises(ValueError):
        ks.env_for_subprocess(["bogus"])


# ── ENV_VAR_BY_PROVIDER mapping is the source of truth ─────────────────────


def test_env_var_mapping_covers_supported_providers() -> None:
    """The mapping must include every provider the wizard / cli let users
    pick. Missing entries silently strand keys."""
    expected = {"anthropic", "openai", "google", "nvidia"}
    assert set(ENV_VAR_BY_PROVIDER) == expected


def test_env_var_names_match_sdk_conventions() -> None:
    """LangChain SDKs auto-pick keys from these specific env-var names; if
    we drift from those, the explicit ``api_key=`` override in
    ``agent_graph.py`` is the only thing keeping things working."""
    assert ENV_VAR_BY_PROVIDER["anthropic"] == "ANTHROPIC_API_KEY"
    assert ENV_VAR_BY_PROVIDER["openai"] == "OPENAI_API_KEY"
    assert ENV_VAR_BY_PROVIDER["google"] == "GOOGLE_API_KEY"
    assert ENV_VAR_BY_PROVIDER["nvidia"] == "NVIDIA_API_KEY"
