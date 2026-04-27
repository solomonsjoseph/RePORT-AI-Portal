"""In-memory API-key registry.

Replaces the prior pattern of writing user-entered LLM API keys to
``os.environ`` (so any sibling process or sandboxed Python could read
them) with a process-local registry held in Streamlit session state.

The trust boundary is straightforward: keys live ONLY here in memory
and are passed explicitly to LangChain client constructors via
``api_key=``. The single narrow exception is when the wizard launches
the pipeline as a subprocess that needs ``ANTHROPIC_API_KEY`` /
``GOOGLE_API_KEY`` for vision-API calls — :meth:`KeyStore.env_for_subprocess`
returns a *new* dict suitable for ``subprocess.run(env=...)`` without
ever mutating the parent's ``os.environ``.

See ``docs/sphinx/developer_guide/sandbox.rst`` for the threat model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable


# Provider slug → conventional environment-variable name LangChain SDKs
# auto-pick. The names here are the SDK contract; they are NOT the only
# place keys live (see ``KeyStore`` itself), but when keys DO need to
# transit env (e.g. for the pipeline subprocess), this is the mapping.
ENV_VAR_BY_PROVIDER: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
}


class KeyStore:
    """Process-local registry of LLM provider API keys.

    Instances are intended to be held in :data:`streamlit.session_state`
    via :func:`get_keystore`; for non-Streamlit contexts (CLI, tests) a
    fresh instance is fine — the class itself has no global state.

    Keys live in a private dict on the instance. They are never written
    to disk, never copied to ``os.environ`` by this class, and never
    logged (the redaction patterns in ``scripts.utils.log_hygiene``
    catch any accidental leak in log output).
    """

    __slots__ = ("_keys",)

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    @staticmethod
    def _normalise(provider: str) -> str:
        return provider.strip().lower()

    def _ensure_known(self, provider: str) -> str:
        slug = self._normalise(provider)
        if slug not in ENV_VAR_BY_PROVIDER:
            raise ValueError(
                f"Unknown provider {provider!r}. Known: {sorted(ENV_VAR_BY_PROVIDER)}."
            )
        return slug

    def set(self, provider: str, key: str) -> None:
        """Store ``key`` for ``provider``. Raises if provider is unknown."""
        slug = self._ensure_known(provider)
        if not key or not key.strip():
            raise ValueError(f"Refusing to store an empty key for {provider!r}.")
        self._keys[slug] = key

    def get(self, provider: str) -> str | None:
        """Return the stored key for ``provider`` or ``None``."""
        return self._keys.get(self._normalise(provider))

    def has(self, provider: str) -> bool:
        return self._normalise(provider) in self._keys

    def clear(self, provider: str | None = None) -> None:
        """Forget one provider's key (or all if ``provider`` is omitted).

        This only touches the instance's in-memory dict. It does NOT
        touch ``os.environ`` — if the user pre-set a shell env var, that
        remains the user's choice and lives in their shell's session.
        """
        if provider is None:
            self._keys.clear()
            return
        self._keys.pop(self._normalise(provider), None)

    def env_for_subprocess(self, providers: Iterable[str]) -> dict[str, str]:
        """Build an env dict suitable for ``subprocess.run(env=...)``.

        Returns a *new* dict containing ``{ENV_VAR: key}`` for each
        requested provider that has a key set. Providers without a
        stored key are skipped (the caller decides whether that's an
        error). Unknown providers raise ``ValueError`` immediately.

        This method is the ONLY place keys leave the KeyStore in an
        env-shaped form. The returned dict is a pure value — neither
        ``os.environ`` nor the KeyStore is mutated.
        """
        out: dict[str, str] = {}
        for provider in providers:
            slug = self._ensure_known(provider)
            key = self._keys.get(slug)
            if key is not None:
                out[ENV_VAR_BY_PROVIDER[slug]] = key
        return out


# LangChain ``model_provider`` strings → KeyStore slug. Centralised here so
# the wizard / agent_graph / cli all map identically.
_LANGCHAIN_PROVIDER_TO_SLUG: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google-genai": "google",
    "google": "google",
    "gemini": "google",
    "nvidia-ai-endpoints": "nvidia",
    "nvidia": "nvidia",
}


def provider_slug_for(langchain_provider: str) -> str | None:
    """Map a LangChain ``model_provider`` string to a KeyStore slug.

    Returns ``None`` for providers that don't take an API key (e.g.
    ``ollama``) so callers can short-circuit.
    """
    return _LANGCHAIN_PROVIDER_TO_SLUG.get(langchain_provider.strip().lower())


_PROCESS_KEYSTORE: KeyStore | None = None


def get_keystore() -> KeyStore:
    """Return the KeyStore for the current Streamlit session.

    In Streamlit: persisted via ``st.session_state``.
    Outside Streamlit (CLI, scripts): cached on a module global so a single
    process sees one consistent KeyStore across calls.
    Unit tests that want isolation should construct ``KeyStore()`` directly.
    """
    try:
        import streamlit as st

        ss = st.session_state
    except (ImportError, RuntimeError):
        global _PROCESS_KEYSTORE
        if _PROCESS_KEYSTORE is None:
            _PROCESS_KEYSTORE = KeyStore()
        return _PROCESS_KEYSTORE

    existing = ss.get("rpln_keystore")
    if isinstance(existing, KeyStore):
        return existing
    fresh = KeyStore()
    ss["rpln_keystore"] = fresh
    return fresh
