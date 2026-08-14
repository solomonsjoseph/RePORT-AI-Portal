"""Pin the property that already holds so it cannot regress: the base
install (``project.dependencies`` in pyproject.toml) pulls in zero AI/LLM
vendor SDKs. Vendor packages live only in the optional ``llm``,
``ai_assistant``, and ``web`` dependency groups.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]

_VENDOR_MARKERS = (
    "openai",
    "anthropic",
    "langchain",
    "google-genai",
    "ollama",
    "streamlit",
)


def test_base_install_has_no_vendor_dependencies() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject["project"]["dependencies"]

    bad = [d for d in deps if any(marker in d.lower() for marker in _VENDOR_MARKERS)]
    assert not bad, f"base install must have no vendor deps, found: {bad}"
