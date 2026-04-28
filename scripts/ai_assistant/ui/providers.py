"""LLM provider configuration and Ollama model helpers."""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

import config
from scripts.ai_assistant.ollama_config import get_ollama_base_url

logger = logging.getLogger(__name__)

_OTHER_MODEL_OPTION = "Other (type below)"
_OLLAMA_FALLBACK_MODELS = ["qwen3:8b", "qwen3:4b", "qwen3:1.7b", "mistral:latest", "gemma3:9b"]
_OLLAMA_NON_CHAT_PREFIXES = (
    "all-minilm",
    "bge",
    "granite-embedding",
    "jina-embeddings",
    "mxbai",
    "nomic-embed",
    "snowflake-arctic-embed",
)
_OLLAMA_NON_CHAT_TOKENS = ("embed", "embedding", "rerank")


def _normalise_ollama_model_name(model_name: str) -> str:
    """Normalise ``foo`` and ``foo:latest`` to the same logical model."""
    model = model_name.strip()
    if model.endswith(":latest"):
        return model[:-7]
    return model


# Pure downgrade ladder: the ordered list of qwen3 chat tags we prefer when
# the configured model is not installed locally. Memory budget drops left→
# right, so the first installed tag is the largest the host can actually
# serve. Consumed by :func:`preferred_or_installed_downgrade` — exposed so
# tests can extend the ladder without monkey-patching.
QWEN3_DOWNGRADE_LADDER: tuple[str, ...] = (
    "qwen3:32b",
    "qwen3:14b",
    "qwen3:8b",
    "qwen3:4b",
    "qwen3:1.7b",
)


def preferred_or_installed_downgrade(
    preferred: str,
    installed: list[str] | tuple[str, ...],
) -> str | None:
    """Resolve *preferred* against *installed*, downgrading when needed.

    Returns the preferred tag when it (or its `:latest` equivalent) is
    installed. Otherwise walks :data:`QWEN3_DOWNGRADE_LADDER` from the
    preferred size downward and returns the first tag present. Returns
    ``None`` when no qwen3 tag is installed — callers should treat that
    as "ask the operator" rather than silently picking a non-qwen3 tag.

    Hardware reality on the current dev box: ``qwen3:8b`` OOMs at ~3 GiB
    free, so a user configuring ``qwen3:8b`` gets silently downgraded to
    ``qwen3:1.7b`` rather than an inference-time crash.
    """
    if not preferred or not installed:
        return None
    installed_set = {_normalise_ollama_model_name(m) for m in installed}
    preferred_norm = _normalise_ollama_model_name(preferred)
    if preferred_norm in installed_set:
        return preferred
    if preferred_norm not in QWEN3_DOWNGRADE_LADDER:
        return None
    start = QWEN3_DOWNGRADE_LADDER.index(preferred_norm)
    for candidate in QWEN3_DOWNGRADE_LADDER[start:]:
        if candidate in installed_set:
            return candidate
    for candidate in QWEN3_DOWNGRADE_LADDER[:start]:
        if candidate in installed_set:
            return candidate
    return None


def _ollama_models_match(candidate: str, desired: str) -> bool:
    """Return True when two Ollama names differ only by the implicit tag."""
    return bool(candidate and desired) and (
        _normalise_ollama_model_name(candidate) == _normalise_ollama_model_name(desired)
    )


def _is_ollama_chat_model(model_name: str) -> bool:
    """Hide obvious embedding/reranker tags from the chat-model selector."""
    lowered = _normalise_ollama_model_name(model_name).lower()
    if not lowered:
        return False
    if lowered.startswith(_OLLAMA_NON_CHAT_PREFIXES):
        return False
    return not any(token in lowered for token in _OLLAMA_NON_CHAT_TOKENS)


def _clean_ollama_model_names(raw_models: list[str]) -> list[str]:
    """Return deduplicated model names in a stable sorted order."""
    names = sorted({name.strip() for name in raw_models if name and name.strip()})
    return names


def _build_ollama_selector_state(
    discovered_models: list[str],
    *,
    source: str,
    saved_model: str,
    configured_model: str,
    default_model: str,
) -> dict[str, Any]:
    """Build safe selector options for Ollama chat models.

    When model discovery falls back to static defaults, keep the selector on a
    known configured model if possible, otherwise force manual confirmation
    instead of auto-picking an arbitrary local tag.
    """
    real_models = [model for model in discovered_models if model != _OTHER_MODEL_OPTION]
    chat_models = [model for model in real_models if _is_ollama_chat_model(model)]
    hidden_models = [model for model in real_models if model not in chat_models]
    options = [*chat_models, _OTHER_MODEL_OPTION]

    sticky_preferences = [saved_model.strip(), configured_model.strip()]
    selected_model = next(
        (
            candidate
            for preferred in sticky_preferences
            if preferred
            for candidate in chat_models
            if _ollama_models_match(candidate, preferred)
        ),
        None,
    )
    if selected_model is None and source in {"api", "cli"} and chat_models:
        selected_model = chat_models[0]
    if selected_model is None:
        selected_model = _OTHER_MODEL_OPTION

    fallback_hint = next(
        (preferred for preferred in [*sticky_preferences, default_model] if preferred),
        default_model,
    )
    if selected_model != _OTHER_MODEL_OPTION:
        fallback_hint = selected_model

    return {
        "options": options,
        "index": options.index(selected_model),
        "fallback_hint": fallback_hint,
        "hidden_models": hidden_models,
    }


def _get_ollama_base_url() -> str:
    """Return the Ollama API base URL for UI model discovery."""
    return get_ollama_base_url()


def _try_start_ollama(base_url: str, *, max_wait: int = 10) -> bool:
    """Attempt to start Ollama if not reachable.  Returns *True* if now reachable."""
    # Already reachable?
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as resp:  # noqa: S310
            resp.read()
        return True
    except (urllib.error.URLError, OSError):
        pass
    # Try launching the server
    try:
        subprocess.Popen(
            ["ollama", "serve"],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False
    # Poll until ready
    for _ in range(max_wait):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as resp:  # noqa: S310
                resp.read()
            return True
        except (urllib.error.URLError, OSError):
            continue
    return False


def _get_ollama_models() -> tuple[list[str], str]:
    """Return models currently installed in the local Ollama server.

    Tries the Ollama REST API first (``/api/tags``), falls back to
    ``ollama list`` via subprocess.  If both fail, attempts to auto-start
    Ollama and retries once before returning hardcoded defaults.
    """
    base_url = _get_ollama_base_url()
    fallback = [*_OLLAMA_FALLBACK_MODELS, _OTHER_MODEL_OPTION]

    # 1. REST API
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"{base_url}/api/tags", timeout=2
        ) as resp:
            data = json.loads(resp.read())
        names = _clean_ollama_model_names(
            [
                m.get("name", "").strip() or m.get("model", "").strip()
                for m in data.get("models", [])
                if isinstance(m, dict)
            ]
        )
        if names:
            return [*names, _OTHER_MODEL_OPTION], "api"
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        pass

    # 2. subprocess fallback
    try:
        ollama_bin = subprocess.run(
            ["ollama", "list"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        ollama_bin = None
    if ollama_bin is not None and ollama_bin.returncode == 0:
        names = _clean_ollama_model_names(
            [line.split()[0] for line in ollama_bin.stdout.splitlines()[1:] if line.strip()]
        )
        if names:
            return [*names, _OTHER_MODEL_OPTION], "cli"

    # 3. Auto-start Ollama and retry the API once
    if _try_start_ollama(base_url):
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"{base_url}/api/tags", timeout=2
            ) as resp:
                data = json.loads(resp.read())
            names = _clean_ollama_model_names(
                [
                    m.get("name", "").strip() or m.get("model", "").strip()
                    for m in data.get("models", [])
                    if isinstance(m, dict)
                ]
            )
            if names:
                return [*names, _OTHER_MODEL_OPTION], "api"
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
            pass

    return fallback, "fallback"


_PROVIDER_CONFIG: dict[str, dict] = {
    # Ollama is listed first — works offline, no API key required.
    # Pull a model with: ollama pull qwen3:8b
    "Ollama (local)": {
        "provider": "ollama",
        "env_var": None,
        "default_model": "qwen3:8b",
        "needs_key": False,
        "models": [
            "qwen3:1.7b",
            "qwen3:4b",
            "qwen3:8b",
            "qwen3:14b",
            "qwen3:32b",
            "mistral:latest",
            "gemma3:9b",
            "deepseek-r1:8b",
            _OTHER_MODEL_OPTION,
        ],
    },
    "Anthropic": {
        "provider": "anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-sonnet-4-6",
        "needs_key": True,
        "models": [
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-opus-4-5-20251101",
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5-20251001",
            _OTHER_MODEL_OPTION,
        ],
    },
    "OpenAI": {
        "provider": "openai",
        "env_var": "OPENAI_API_KEY",
        "default_model": "gpt-4.1",
        "needs_key": True,
        "models": [
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o",
            "gpt-4o-mini",
            "o4-mini-2025-04-16",
            "o3-2025-04-16",
            _OTHER_MODEL_OPTION,
        ],
    },
    "Google Gemini": {
        "provider": "google-genai",
        "env_var": "GOOGLE_API_KEY",
        "default_model": "gemini-3-flash",
        "needs_key": True,
        "models": [
            "gemini-3-flash",
            "gemini-3-pro",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            _OTHER_MODEL_OPTION,
        ],
    },
    "NVIDIA AI Endpoints": {
        "provider": "nvidia-ai-endpoints",
        "env_var": "NVIDIA_API_KEY",
        "default_model": "moonshotai/kimi-k2.5",
        "needs_key": True,
        "models": [
            "moonshotai/kimi-k2.5",
            "meta/llama-3.3-70b-instruct",
            "mistralai/mistral-large-2-instruct",
            "nvidia/llama-3.1-nemotron-ultra-253b-v1",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen3-235b-a22b",
            _OTHER_MODEL_OPTION,
        ],
    },
}


def _default_provider_label() -> str:
    """Map the live config provider id back to the UI label."""
    for label, provider_cfg in _PROVIDER_CONFIG.items():
        if provider_cfg["provider"] == config.LLM_PROVIDER:
            return label
    return "Ollama (local)"
