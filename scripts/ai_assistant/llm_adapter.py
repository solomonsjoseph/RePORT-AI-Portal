"""Minimal JSON-structured LLM client for the PHI AI-assist subsystem (Notes 7 + 9).

This is the single, shared bridge the publish-path skills use to call an LLM for
the two AI-assisted PHI steps:

* **N7** — extract structured rules from PUBLIC regulation text.
* **N9** — align a column NAME to a rulebook rule (regex + action).

The LLM **never receives a dataset row value** (GR-1). N7 sends public regulation
text; N9 sends column NAMES + the value-free rulebook JSON. The caller is
responsible for the value-free guarantee on its inputs; this module additionally
refuses to send any text that trips the shared PHI value-marker scan.

Design:

* **Import-light / lazy** — importing this module constructs nothing; a chat
  model is built (via the AI assistant's vetted ``agent_graph._build_llm``) only
  when :meth:`LLMJsonClient.invoke_json` is first called. So the publish path can
  import it unconditionally with zero LLM dependency at module load.
* **Dependency-injectable** — N7/N9 accept an injected client, so the
  deterministic test suite passes a fake and never builds a real model or touches
  the network. Production builds :class:`LLMJsonClient` from ``config.LLM_*``.
* This module lives under ``scripts/`` (the sanctioned ``plugins/ → scripts/``
  bridge; skills already import ``scripts.ai_assistant.*``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import config

__all__ = [
    "JsonLLM",
    "LLMJsonClient",
    "PromptValueLeakError",
    "extract_json",
    "guard_prompt_value_free",
]

# Markers that must never appear in a prompt we send to an LLM — mirrors the
# value-free contract used by phi_review.verify_approval_payload. A prompt that
# trips this is a programming error (a caller leaked a value), and we fail closed.
_FORBIDDEN_PROMPT_MARKERS: tuple[str, ...] = (
    "raw_value",
    "sample_value",
    "synthetic_value",
    "_phi_scrubbed",
)


class PromptValueLeakError(RuntimeError):
    """Raised when a prompt bound for the LLM contains a value-like marker."""


def guard_prompt_value_free(text: str) -> None:
    """Fail closed if *text* contains a value-like marker (defense in depth)."""
    leaked = [m for m in _FORBIDDEN_PROMPT_MARKERS if m in text]
    if leaked:
        raise PromptValueLeakError(f"prompt contains value-like marker(s): {leaked}")


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array from an LLM reply (tolerant of fences).

    Models often wrap JSON in ```json fences or prose; this extracts the first
    balanced ``{...}`` or ``[...]`` and parses it. Raises ``ValueError`` if no
    valid JSON is found (the caller's verifier then drives a retry).
    """
    text = text.strip()
    # Strip a leading ```json / ``` fence if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    # Fall back: find the first { or [ and parse just that value (ignore any
    # trailing prose) via raw_decode.
    start = next((i for i, c in enumerate(text) if c in "{["), -1)
    if start < 0:
        raise ValueError("no JSON object/array found in LLM reply")
    obj, _end = json.JSONDecoder().raw_decode(text, start)
    return obj


class JsonLLM(Protocol):
    """Structural type for an injectable JSON LLM client (real or fake)."""

    def invoke_json(self, system_prompt: str, user_prompt: str) -> Any: ...


class LLMJsonClient:
    """Production JSON LLM client — lazily wraps ``agent_graph._build_llm``."""

    def __init__(self, provider: str | None = None, model: str | None = None) -> None:
        self._provider = provider or config.LLM_PROVIDER
        self._model = model or config.LLM_MODEL
        self._model_obj: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model_obj is None:
            from scripts.ai_assistant.agent_graph import _build_llm

            self._model_obj = _build_llm(self._provider, self._model)
        return self._model_obj

    def invoke_json(self, system_prompt: str, user_prompt: str) -> Any:
        guard_prompt_value_free(system_prompt)
        guard_prompt_value_free(user_prompt)
        from langchain_core.messages import HumanMessage, SystemMessage

        model = self._ensure_model()
        response = model.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        text = getattr(response, "content", None) or str(response)
        return extract_json(text if isinstance(text, str) else str(text))
