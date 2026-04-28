"""LLM capability detection for the PDF-extraction pipeline.

The PDF pipeline runs in three tiers (see
``docs/sphinx/developer_guide/pdf_pipeline.rst``):

1. **Code path** — pdfplumber-based, always runs, fast, deterministic.
2. **LLM path** — runs ONLY when a "capable" model is configured.
   Capable means the model can reliably extract structured form
   metadata from CRF text without hallucinating columns.
3. **Backup snapshot** — falls back to a human-verified snapshot
   baseline when neither path produces valid output.

This module decides tier 2's eligibility. The default capable set is
hardcoded but env-overridable via ``REPORTALIN_PDF_LLM_CAPABLE_MODELS``
(comma-separated list of model name *prefixes*; matches model names by
``startswith`` after lowercasing).

Why a hardcoded list + env override (rather than asking the model itself):
the LLM can't reliably self-report its own capabilities, and we don't
want a one-shot completion to incur cost just to find out it shouldn't
have been called. The list is conservative — if your model is excluded
but you've validated it works, set the env var.
"""

from __future__ import annotations

import logging
import os

__all__ = [
    "DEFAULT_CAPABLE_MODEL_PREFIXES",
    "is_capable_model",
]


logger = logging.getLogger(__name__)


# Conservative defaults. Expand cautiously — capability for PDF schema
# extraction is the bar, not raw chat ability. Env override
# ``REPORTALIN_PDF_LLM_CAPABLE_MODELS`` REPLACES this list (not extends),
# so operators take full responsibility when they override.
DEFAULT_CAPABLE_MODEL_PREFIXES: tuple[str, ...] = (
    # Anthropic — Opus 4.6+ and Sonnet 4.6+ are capable; older Sonnet
    # struggles on multi-section CRFs.
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-7",
    "claude-sonnet-5",
    # OpenAI — GPT-5 line is the threshold. GPT-4 family is borderline
    # on complex CRFs, so off by default.
    "gpt-5",
    "gpt-6",
    # Google — Gemini 2.5 Pro is the threshold. Flash is excluded by
    # default (good for chat, weaker on table-heavy PDFs).
    "gemini-2.5-pro",
    "gemini-3",
    # NVIDIA NIM — only the 405B-class Llama models. Smaller variants
    # cannot consistently produce the variable schema.
    "meta/llama-3.3-405b-instruct",
    "meta/llama-3.3-405b",
    "meta/llama-4",
)


def _override_prefixes() -> tuple[str, ...] | None:
    raw = os.environ.get("REPORTALIN_PDF_LLM_CAPABLE_MODELS", "").strip()
    if not raw:
        return None
    prefixes = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return prefixes or None


def is_capable_model(provider: str | None, model: str | None) -> bool:
    """Return True when ``(provider, model)`` is on the LLM-extraction allowlist.

    Provider-aware: Ollama is excluded by default regardless of the
    model name, because local Ollama models historically can't sustain
    a JSON-schema response on a 30-page CRF. If you've validated a
    specific local model, override via the env var.

    Empty / None inputs return False. Comparison is case-insensitive
    against the configured prefix list (default or env-overridden).
    """
    if not provider or not model:
        return False

    provider_l = provider.strip().lower()
    model_l = model.strip().lower()

    # Ollama disabled by default (local resource constraints + JSON
    # schema reliability). Enable only via explicit env override.
    if provider_l in ("ollama", "ollama-local"):
        override = _override_prefixes()
        if override is None:
            return False
        return any(model_l.startswith(p) for p in override)

    prefixes = _override_prefixes() or DEFAULT_CAPABLE_MODEL_PREFIXES
    capable = any(model_l.startswith(p) for p in prefixes)
    if not capable:
        logger.debug(
            "llm_capabilities: model %r/%r not in capable allowlist — "
            "PDF pipeline will skip LLM extraction tier",
            provider,
            model,
        )
    return capable
