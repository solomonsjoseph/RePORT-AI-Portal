"""Shared Ollama runtime configuration."""

from __future__ import annotations

import os

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def get_ollama_base_url() -> str:
    """Return the configured Ollama API base URL.

    ``OLLAMA_BASE_URL`` is the documented RePORTALIN setting. ``OLLAMA_HOST``
    remains supported because Ollama itself uses that name.
    """
    host = (os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_HOST") or "").strip()
    if not host:
        return DEFAULT_OLLAMA_BASE_URL
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return host.rstrip("/")
