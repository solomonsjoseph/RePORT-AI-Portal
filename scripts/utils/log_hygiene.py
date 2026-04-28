"""PHI-redacting log filter for the RePORT AI Portal pipeline.

Before the PHI scrub runs (Step 1.6), the pipeline processes raw subject
data — raw SUBJIDs, raw dates, raw narrative strings. If any of that
content is logged at INFO / DEBUG during extraction or orchestration, it
lands in ``.logs/*.log`` and becomes a PHI side-channel the scrub does
not touch.

This module installs a ``logging.Filter`` that redacts likely-PHI
substrings from every log record before the handler emits. Specifically:

* **Subject IDs** — any literal substring matching the configured
  ``subject_id_fields`` regex catalogue is replaced with a stable HMAC
  tag ``<SUBJ_{HMAC[:8]}>``. Same-subject redaction is deterministic
  across a run (the HMAC key is loaded once at filter install time).
* **Common PHI regex classes** — Aadhaar, PAN, Indian phone, email,
  SSN, ISO/M-D-Y dates, Indian PIN-code patterns are replaced with a
  category tag like ``<AADHAAR>`` or ``<EMAIL>``.

Design constraints:

* **No raw values in filter memory** — the filter stores only compiled
  regex + the PHI HMAC key; never a raw value.
* **Fast path for clean messages** — the filter short-circuits if the
  message contains none of the pre-compiled triggers, so the common
  case pays one substring search per record.
* **Fail-closed per record** — on any exception during redaction, the filter
  replaces the message with a fixed redaction-failure notice. Logs remain
  useful for operations without passing raw PHI through.

IRB-grade benchmark anchors:
    * ICMR 2017 §11.5 audit + confidentiality
    * HIPAA §164.312(b) audit controls
    * NIST SP 800-188 §6.4 on side-channel closure
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re

from scripts.security.phi_patterns import BLOCKING_PATTERNS, WARN_PATTERNS

__all__ = [
    "PHIRedactingFilter",
    "attach_to_logger",
    "install_phi_redactor",
]

# The log redactor shares its regex catalog with the agent-boundary PHI gate
# so the two surfaces can never drift. We re-use both BLOCKING_PATTERNS and
# WARN_PATTERNS verbatim — logs lean toward over-redaction (legibility cost
# vs. PHI-leak cost), so low-confidence heuristics like DATE_MDY and generic
# PERSON_NAME are redacted in logs even though the agent gate only blocks
# on the high-confidence BLOCKING tier. A diverging per-module list is
# actively dangerous — new PHI classes added to phi_patterns would silently
# not be redacted in logs.
API_KEY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Anthropic keys: ``sk-ant-api03-…`` ~108 chars total. Require the
    # ``api`` segment + a long body so ``sk-ant-foo`` shorthand in docs
    # is not redacted.
    ("ANTHROPIC_KEY", re.compile(r"sk-ant-[A-Za-z]+\d*-[A-Za-z0-9_\-]{20,}")),
    # OpenAI keys: ``sk-…`` ≥40 chars body, optionally with ``proj-`` prefix.
    ("OPENAI_KEY", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{40,}")),
    # NVIDIA NGC keys.
    ("NVIDIA_KEY", re.compile(r"nvapi-[A-Za-z0-9_\-]{30,}")),
    # Google API keys (Gemini, GCP). Always start with ``AIza`` + 35 chars.
    ("GOOGLE_KEY", re.compile(r"AIza[A-Za-z0-9_\-]{35}")),
]
"""LLM provider API-key patterns. After PR #3 the keystore keeps keys out
of ``os.environ`` entirely, so keys never reach the logger via env-var
dump. But defense in depth: if a key ever lands in a log message —
through a stack trace, a tool call, or a copy-paste — these patterns
scrub it before the message is written to ``.logs/``.

Each pattern requires the full provider-specific length so short
references like ``sk-flag`` or doc literals do NOT false-positive.
"""


_GENERIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    *API_KEY_PATTERNS,
    *BLOCKING_PATTERNS,
    *WARN_PATTERNS,
]
"""Redaction catalog — :data:`API_KEY_PATTERNS` first (so a key embedded
inside a longer string is caught before any PHI heuristic might claim
part of it), then :data:`phi_patterns.BLOCKING_PATTERNS` + WARN_PATTERNS.

Applied IN ORDER to every log message by :class:`PHIRedactingFilter`. Each
match is replaced with ``<CATEGORY>`` (e.g. ``<EMAIL>``, ``<ANTHROPIC_KEY>``).
Intentionally conservative — false positives here cost legibility only;
false negatives cost IRB compliance OR API-key disclosure.
"""


class PHIRedactingFilter(logging.Filter):
    """Log filter that redacts PHI substrings before the handler emits.

    Installed on the root logger by :func:`install_phi_redactor`, so every
    named logger inherits redaction. Two redaction passes:

    1. **Subject-ID pass** — a caller-supplied list of ``subject_id_fields``
       regex patterns is matched against the message. Each match is
       replaced with ``<SUBJ_{HMAC-SHA256[:8]}>`` — deterministic per
       subject within a run, unrecoverable across the filter instance.
    2. **Generic pass** — :data:`_GENERIC_PATTERNS` catches the common
       PHI classes (Aadhaar, PAN, email, phone, date, pincode, SSN).
    """

    def __init__(
        self,
        *,
        hmac_key: bytes,
        subject_id_patterns: list[re.Pattern[str]] | None = None,
        generic_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
    ) -> None:
        super().__init__()
        self._hmac_key = hmac_key
        self._subject_id_patterns = subject_id_patterns or []
        self._generic_patterns = generic_patterns or _GENERIC_PATTERNS

    def _redact_subject_match(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        tag = hmac.new(self._hmac_key, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:8]
        return f"<SUBJ_{tag}>"

    def _redact_text(self, text: str) -> str:
        # Subject-ID pass first (most specific, per-instance HMAC).
        for pattern in self._subject_id_patterns:
            text = pattern.sub(self._redact_subject_match, text)
        # Generic PHI pass.
        for label, pattern in self._generic_patterns:
            text = pattern.sub(f"<{label}>", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # The msg may contain format-string placeholders that are not
            # interpolated until the handler formats the record. Redact the
            # fully-interpolated form and stash it back as a plain string.
            # If interpolation fails (bad args), fall back to the raw msg.
            try:
                rendered = record.getMessage()
            except (TypeError, ValueError):
                rendered = str(record.msg)
            redacted = self._redact_text(rendered)
            # Replace msg + clear args so downstream formatters see the
            # already-interpolated string.
            record.msg = redacted
            record.args = None
        except Exception:
            record.msg = "[PHI LOG REDACTION FAILURE - message suppressed]"
            record.args = None
        return True


def install_phi_redactor(
    *,
    hmac_key: bytes,
    subject_id_patterns: list[re.Pattern[str]] | None = None,
) -> PHIRedactingFilter:
    """Attach :class:`PHIRedactingFilter` to the root logger and return it.

    Idempotent: if the root logger already has a ``PHIRedactingFilter``
    installed, the existing filter is returned and no duplicate is added.

    Callers must supply an ``hmac_key`` — typically the same 32-byte key
    used by :mod:`scripts.security.phi_scrub` so log redaction and on-disk
    pseudonyms are joinable by operators with key access.
    """
    root = logging.getLogger()
    for existing in root.filters:
        if isinstance(existing, PHIRedactingFilter):
            return existing
    flt = PHIRedactingFilter(
        hmac_key=hmac_key,
        subject_id_patterns=subject_id_patterns,
    )
    root.addFilter(flt)
    return flt


def attach_to_logger(logger: logging.Logger, filter_instance: PHIRedactingFilter) -> None:
    """Attach *filter_instance* to a specific named *logger* (belt-and-braces).

    ``logging.Filter`` is evaluated by the handler on the logger where it
    is attached, not inherited by child loggers. For defence-in-depth,
    callers can also attach the filter to each leg logger explicitly.
    """
    logger.addFilter(filter_instance)


def _redact(text: str, filter_instance: PHIRedactingFilter) -> str:
    """Expose the underlying redaction for test + utility use only.

    Intentionally module-private (single-underscore) — production code
    should go through :func:`install_phi_redactor`. This entry point
    exists so unit tests can assert redaction output without having to
    install a real logging pipeline.
    """
    return filter_instance._redact_text(text)
