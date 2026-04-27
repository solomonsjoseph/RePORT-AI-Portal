"""Shared PHI regex catalog used by phi_gate, log_hygiene, and future NER.

Single source of truth for "what does a PHI-like substring look like" so
the query-time gate, the log redactor, and the narrative scrub all agree.

Three tiers:

* **Blocking patterns** — high-confidence PHI (Aadhaar, PAN, SSN, email,
  phone, Indian PIN). A blocking hit in any tool return blocks the
  response.
* **Warn patterns** — lower-confidence heuristics (bare NUMERIC_ID,
  DATE_MDY, PERSON_NAME). Logged but do not block. Over-aggressive in
  mixed clinical text; surfaced for audit, not enforcement.
* **Subject-ID patterns** — Indo-VAP-specific subject-ID shapes
  (``SC\\d{4,}``, ``SUBJ-\\d+``, ``SUBJID_N``). Used to key per-subject
  HMAC redaction in the log wrapper.

Regulatory anchors: HIPAA §164.514(b)(2)(i)(A-P), DPDPA §2(t), Aadhaar
Act §29, SPDI Rule 3, ICMR 2017 §11.4.
"""

from __future__ import annotations

import re

__all__ = [
    "BLOCKING_PATTERNS",
    "SUBJECT_ID_PATTERNS",
    "WARN_PATTERNS",
]


BLOCKING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # ── Indian government IDs ────────────────────────────────────────────
    ("AADHAAR", re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("INDIAN_VOTER_ID", re.compile(r"\b[A-Z]{3}\d{7}\b")),
    ("INDIAN_DL", re.compile(r"\b[A-Z]{2}\d{2}\s?\d{4}\d{7}\b")),
    ("INDIAN_PASSPORT", re.compile(r"\b[A-Z]\d{7}\b")),
    # ── Contact ──────────────────────────────────────────────────────────
    (
        "INDIAN_PHONE",
        re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"),
    ),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("URL", re.compile(r"\bhttps?://[^\s/$.?#].[^\s]*\b", re.I)),
    (
        "INDIAN_PIN",
        re.compile(
            r"(?i:pin\s*(?:code)?|postal\s*code|zip)\s*[:=\-]?\s*\b(\d{6})\b"
        ),
    ),
    # ── US identifier shapes (cross-site collaboration hedge) ────────────
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("MRN", re.compile(r"\bMRN[-:]?\s*\d{6,10}\b", re.I)),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # ── Dates (HIPAA §164.514(b)(2)(i)(C)) ───────────────────────────────
    (
        "DATE_ISO",
        re.compile(
            r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
            r"(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"
        ),
    ),
    (
        "PERSON_NAME_PREFIX",
        re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"),
    ),
]
"""High-confidence PHI patterns — a hit blocks the response."""


WARN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # These fire frequently on legitimate clinical text. Use for audit,
    # not enforcement. The archive's is_clinical_phrase / is_clinical_free_text
    # allowlist is meant to suppress these before they reach the gate.
    ("NUMERIC_ID_SHORT", re.compile(r"\b\d{6,7}\b")),
    ("DATE_MDY", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    ("PERSON_NAME_GENERIC", re.compile(r"\b[A-Z][a-z]{2,15}\s+[A-Z][a-z]{2,15}\b")),
]
"""Low-confidence PHI heuristics — recorded for audit, do NOT block."""


SUBJECT_ID_PATTERNS: list[re.Pattern[str]] = [
    # Indo-VAP / RePORT India subject ID shapes.
    re.compile(r"\bSUBJ[-_]?\d+\b"),
    re.compile(r"\bSC\d{4,}\b"),
    re.compile(r"\bFID\d*\b"),
]
"""Literal subject-ID substrings that the log wrapper HMAC-redacts per-subject."""
