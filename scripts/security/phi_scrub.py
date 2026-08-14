"""PHI scrubber — structural-field honest-broker catalog for RePORT AI Portal.

Eight structural-field action classes, evaluated in strict priority order
(first match wins per field):

1. **keep** (``keep_fields``) — allowlist; short-circuits every other rule.
   Used to protect clinical lab / medication / time-of-day / categorical
   indicators from being swept up by broader patterns.
2. **birthdate** (``birthdate_field``) — posture-dependent:

   - ``safe_harbor`` (default) → field dropped entirely per HIPAA
     §164.514(b)(2)(i)(C) + DPDPA. Age fidelity is lost.
   - ``limited_dataset`` → field jittered with the same per-subject offset
     as other dates (SANT method), preserving age-at-event. Requires an
     IRB-approved protocol + DUA; the module refuses to run in this
     mode unless ``authorities/phi_limited_dataset.md`` exists.

3. **drop** (``drop_fields``) — field removed from every row. Covers
   names, initials, signatures, staff identifiers, national IDs (Aadhaar /
   PAN / voter / passport / DL / ration / ESIC / PM-JAY / Nikshay / ABHA),
   contact info, exact geography, free-text narratives, system timestamps,
   and batch/scan artefacts.
4. **cap** (``cap_fields``) — numeric values strictly greater than
   ``threshold`` are replaced with ``label`` (default age > 89 → "90+",
   HIPAA §164.514(b)(2)(i)(C)).
5. **generalize** (``generalize_fields`` + ``generalization_maps``) —
   value-level categorical mapping (e.g. marital status → Married / Single
   / Other; facility type → Government / Private / Other).
6. **suppress_small_cell** (``suppress_small_cell_fields``) — numeric
   values strictly greater than ``small_cell_threshold`` are clamped to the
   threshold (ICMR §11.7 k-anonymity proxy for household-contact counts).
7. **date** (``date_fields``) — per-subject deterministic offset in
   ``[-max_jitter_days, +max_jitter_days]``. Offset = ``HMAC-SHA256(key,
   subject_id)[:4] as int mod (2*N+1) - N``. SANT-method interval
   preservation for epidemiological survival / incidence / person-time
   analyses.
8. **id** (``id_fields``) — replaced with
   ``"RID_" + label + "_" + alpha12(hmac_sha256(key, label + ":" + raw_id))``.
   Deterministic cross-file linkage preserved; non-reversible without key
   possession. The ``RID_`` envelope plus alphabet-only tag keeps generated
   pseudonyms from matching raw subject-ID, phone, or date regexes in the
   pre-publication leak gate.

Free-text PHI residuals are handled conservatively by dropping narrative
fields wholesale. Current narrative fields like ``*COMMENT``, ``*REMARK``,
``WITHDRAWEXPLAIN``, and ``*SPECIFY`` are removed before publication; the
agent-boundary PHI gate remains defense-in-depth for returned text.

Rule catalog is declared in ``phi_scrub.yaml`` (Indo-VAP-calibrated).

Zone boundary
-------------
* Reads + rewrites ``tmp/{STUDY}/datasets/*.jsonl`` in place (write_zone).
* Optionally writes orphan rows to ``tmp/{STUDY}/quarantine/{file}.jsonl``
  when a row lacks a resolvable subject_id (write_zone).
* Emits a single audit envelope at :data:`config.AUDIT_SCRUB_REPORT_PATH`
  (output_zone). The audit records **counts only** — no raw values, no
  before/after pairs.

Ordering in the host publish path
---------------------------------
Runs as Step 1.6 — AFTER Step 1+3 (raw extraction) and BEFORE Step 1.7
(dataset cleanup). This keeps ``dataset_cleanup_report.json`` free of raw
subject IDs and raw dates, so the dataset-leg audit never contains PHI.

Key management
--------------
The HMAC key is a sidecar file at
``$XDG_CONFIG_HOME/report_ai_portal/phi_key`` (default ``~/.config/report_ai_portal/phi_key``).
Mode must be ``0600``. Missing key = hard-fail for developer/operator host
publish runs. Normal users create it through the web UI's Load Study flow.
Developers can bootstrap explicitly::

    python -m scripts.security.phi_scrub bootstrap-key

Rotating the key invalidates every previously-scrubbed artifact — full
re-ingestion from raw is required. This is a one-way property: deletion of
the key forfeits the ability to re-derive the same pseudonyms.

Idempotency
-----------
Each scrubbed record gets a ``_phi_scrubbed: "v1"`` marker. A second run
with the same key is a no-op (the sentinel file
``tmp/{STUDY}/.phi_scrub_complete`` short-circuits the orchestrator).

Threat-model summary
--------------------
* HMAC-SHA256 with a secret key is non-reversible without key possession.
* 12 hex (48 bits) collision surface is adequate for single-study cohorts
  under 100 000 subjects. Larger cohorts should widen the slice.
* Same (key, subject_id) always yields the same pseudonym → cross-run
  joins remain stable across re-ingestion.
* Different machines with different keys → different pseudonyms → hard
  cross-site joins. This is deliberate: collaborator key distribution is
  an operational, not pipeline, concern.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.audit.ledger import (
    PHI_LEDGER_FILENAME,
    LedgerWriter,
    dataset_phi_ledger_path,
    ensure_no_llm_sentinel,
    remove_dataset_no_llm_sentinels,
)
from scripts.extraction.io import atomic_write_json, atomic_write_jsonl, parse_date
from scripts.security.secure_env import assert_output_zone, assert_write_zone
from scripts.utils.integrity import hash_file

logger = logging.getLogger(__name__)

__all__ = [
    "PHI_DISPOSITION_BASELINE_FILENAME",
    "PHI_DISPOSITION_FILENAME",
    "PHI_REVIEW_QUEUE_FILENAME",
    "PHI_REVIEW_SIGNOFF_FILENAME",
    "PHI_TRANSFORMED_FILENAME",
    "CapRule",
    "GeneralizeRule",
    "IdRule",
    "KeepOverrideRule",
    "MustRule",
    "PHIDateParseError",
    "PHIKeyMissingError",
    "PHIKeyPermissionError",
    "PHIPolicyViolationError",
    "PHIQuarantineOverflowError",
    "PHIReviewPendingError",
    "PHIRuleConflictError",
    "PHIScrubConfig",
    "PHIScrubError",
    "bootstrap_key",
    "cap_numeric",
    "crosscheck_sot_policy",
    "date_offset_days",
    "generalize_value",
    "load_key",
    "load_scrub_config",
    "pseudo_id",
    "resolve_action",
    "run_scrub",
    "shift_date",
    "suppress_small_cell",
    "validate_rule_catalog",
]

# ── Constants ────────────────────────────────────────────────────────────────

# Row-level ``_phi_scrubbed`` marker. Acts as an idempotency guard so a
# second scrub pass over the same staging file is a no-op. The full
# catalog version + rule counts live in the audit report, not the row.
_SCRUB_VERSION = "v3"
# v3: ID pseudonyms carry the semantic category inside an opaque RID envelope.
# Format: ``RID_<LABEL>_<alpha12>`` where the HMAC input is
# ``f"{label}:{raw_value}"``. Same raw value under different labels → different
# pseudonyms (prevents cross-category correlation), same raw value under the
# same label → same pseudonym (preserves in-category longitudinal linkage
# across files). The ``RID_`` prefix and alphabet-only tag prevent generated
# pseudonyms from matching raw subject-ID or phone regexes. Bumping the marker
# forces re-scrub of any row written under the v2 ``<LABEL>_<hmac12>`` scheme.
_SCRUB_MARKER_FIELD = "_phi_scrubbed"
_SENTINEL_NAME = ".phi_scrub_complete"

# Step 8/11 artifacts, written beside the audit envelope
# (config.AUDIT_SCRUB_REPORT_PATH.parent).
PHI_TRANSFORMED_FILENAME = "phi_scrub_transformed.json"
PHI_DISPOSITION_FILENAME = "phi_scrub_disposition.json"
PHI_REVIEW_QUEUE_FILENAME = "phi_review_queue.json"
PHI_DISPOSITION_BASELINE_FILENAME = "phi_disposition_baseline.json"
# Operator-maintained sign-off file, also beside the audit envelope.
PHI_REVIEW_SIGNOFF_FILENAME = "phi_review_signoff.yaml"

_DEFAULT_MAX_JITTER_DAYS = 30
_DEFAULT_ORPHAN_THRESHOLD = 10
_DEFAULT_AGE_CAP_THRESHOLD = 89
_DEFAULT_AGE_CAP_LABEL = "90+"
_DEFAULT_SMALL_CELL_THRESHOLD = 5
_PSEUDO_TAG_CHARS = 12  # 48-bit HMAC tag encoded as a-p letters
_OFFSET_DIGEST_BYTES = 4  # first N bytes of digest for offset computation
_HEX_TO_ALPHA = str.maketrans("0123456789abcdef", "abcdefghijklmnop")

_POSTURE_SAFE_HARBOR = "safe_harbor"
_POSTURE_LIMITED_DATASET = "limited_dataset"
_POSTURE_ICMR_CODED = "icmr_coded_dataset"
_VALID_POSTURES = frozenset(
    {_POSTURE_SAFE_HARBOR, _POSTURE_LIMITED_DATASET, _POSTURE_ICMR_CODED}
)

_KEY_FILE_MODE = 0o600
_KEY_HEX_LEN = 64  # 32 bytes = 64 hex chars

_LIMITED_DATASET_AUTHORITY = "authorities/phi_limited_dataset.md"
_ICMR_CODED_AUTHORITY = "authorities/phi_icmr_coded_dataset.md"

# Action priority (first match wins when walking a row's fields).
# keep > birthdate > drop > cap > generalize > suppress > date > id
_ACTION_KEEP = "keep"
_ACTION_DROP = "drop"
_ACTION_CAP = "cap"
_ACTION_GENERALIZE = "generalize"
_ACTION_SUPPRESS = "suppress_small_cell"
_ACTION_DATE = "date"
_ACTION_ID = "id"
_ACTION_BIRTHDATE_DROP = "birthdate-drop"


# ── Exceptions ───────────────────────────────────────────────────────────────


class PHIScrubError(Exception):
    """Base class for PHI scrub errors."""


class PHIKeyMissingError(PHIScrubError):
    """Raised when the sidecar key file is absent."""


class PHIKeyPermissionError(PHIScrubError):
    """Raised when the sidecar key file has unsafe permissions."""


class PHIQuarantineOverflowError(PHIScrubError):
    """Raised when orphan-row count exceeds the configured threshold."""


class PHIDateParseError(PHIScrubError):
    """Raised when a date-classified column holds unparseable non-sentinel
    values past ``date_unparsed_threshold`` (Step 1 — fail-closed jitter)."""


class PHIPolicyViolationError(PHIScrubError):
    """Raised when a resolved action violates a ``must_drop`` or
    ``must_keep`` guard (Step 3 — disposition policy)."""


class PHIRuleConflictError(PHIScrubError):
    """Raised when a ``keep`` rule shadows birthdate/drop/date/id/
    redundant_subject_id with no acknowledging ``keep_overrides`` entry."""


class PHIReviewPendingError(PHIScrubError):
    """Raised when an unresolved destructive review-queue entry from the
    previous run blocks this run until an operator signs off (Step 11e)."""

# ── Config ───────────────────────────────────────────────────────────────────


class CapRule:
    """Compiled cap rule — pattern + threshold + label.

    Each ``cap_fields`` entry yields one ``CapRule``. When a row's field name
    matches ``pattern``, numeric values strictly greater than ``threshold``
    are replaced with ``label``. Values ≤ threshold pass through unchanged.
    """

    __slots__ = ("label", "pattern", "threshold")

    def __init__(self, pattern: re.Pattern[str], threshold: int, label: str) -> None:
        self.pattern = pattern
        self.threshold = threshold
        self.label = label

    def matches(self, name: str) -> bool:
        return bool(self.pattern.search(name))


class IdRule:
    """Compiled id rule — pattern + semantic label.

    Each ``id_fields`` entry yields one ``IdRule``. When a row's field name
    matches ``pattern``, the field value is pseudonymized via
    :func:`pseudo_id` with the attached ``label``. The label is propagated
    both inside the visible output token (``RID_<LABEL>_<alpha12>``) and as
    the HMAC domain-separator, so the same raw value under two different
    labels yields two different pseudonyms.

    Keep the label short (3-5 chars, uppercase). It becomes part of every
    pseudonymized output and of the IRB-facing audit log.
    """

    __slots__ = ("label", "pattern")

    def __init__(self, pattern: re.Pattern[str], label: str) -> None:
        self.pattern = pattern
        self.label = label

    def matches(self, name: str) -> bool:
        return bool(self.pattern.search(name))


class GeneralizeRule:
    """Compiled generalize rule — pattern + named value mapping.

    Each ``generalize_fields`` entry pairs a field-name pattern with the
    name of a value-to-value mapping under ``generalization_maps``. At
    scrub time the value is lower-cased, looked up in the mapping, and
    replaced; missing values fall through unchanged (audit event still
    recorded with count=0 for that row).
    """

    __slots__ = ("mapping", "mapping_name", "pattern")

    def __init__(
        self,
        pattern: re.Pattern[str],
        mapping_name: str,
        mapping: dict[str, str],
    ) -> None:
        self.pattern = pattern
        self.mapping_name = mapping_name
        self.mapping = mapping

    def matches(self, name: str) -> bool:
        return bool(self.pattern.search(name))


class MustRule:
    """Compiled ``must_drop`` / ``must_keep`` guard — citation + patterns.

    Absolute: :func:`validate_rule_catalog` raises when a matching column
    resolves to anything other than the mandated action. Never overridable
    by ``keep_overrides`` or an SoT declaration.
    """

    __slots__ = ("basis", "patterns")

    def __init__(self, basis: str, patterns: list[re.Pattern[str]]) -> None:
        self.basis = basis
        self.patterns = patterns

    def matches(self, name: str) -> bool:
        return any(p.search(name) for p in self.patterns)


class KeepOverrideRule:
    """Declared acknowledgement that a ``keep`` rule shadows a more-specific
    PHI rule for *column* — a genuine false-positive guard, not an oversight.

    Consumed by :func:`validate_rule_catalog`: a keep rule shadowing
    birthdate/drop/date/id/redundant_subject_id with no matching
    ``keep_overrides`` entry (same column pattern *and* matching
    ``shadows`` value) is a hard failure. Grants no permission against
    ``must_drop``.
    """

    __slots__ = ("pattern", "rationale", "shadows")

    def __init__(self, pattern: re.Pattern[str], shadows: str, rationale: str) -> None:
        self.pattern = pattern
        self.shadows = shadows
        self.rationale = rationale

    def matches(self, name: str) -> bool:
        return bool(self.pattern.search(name))


class PHIScrubConfig:
    """Parsed + compiled scrub configuration.

    Regex patterns from YAML are compiled once at load time; config is a
    throwaway struct (not persisted beyond the host publish run).

    Rule priority (first match wins within :func:`_scrub_row`):
        1. ``keep_patterns``       — allowlist, short-circuits every other rule
        2. ``birthdate_pattern``   — posture-dependent drop or jitter
        3. ``redundant_subject_id_patterns`` — value-conditional drop/pseudonymize
        4. ``drop_patterns``       — field removed from row
        5. ``cap_rules``           — numeric capped to label
        6. ``generalize_rules``    — value mapped to broad category
        7. ``suppress_small_cell_patterns`` — numeric clamped to threshold
        8. ``date_patterns``       — jitter via SANT
        9. ``id_patterns``         — HMAC-SHA256 pseudonymize

    ``must_drop_rules`` / ``must_keep_rules`` are absolute guards evaluated
    by :func:`resolve_action` / :func:`validate_rule_catalog` ahead of
    everything else — see the Disposition Policy in the hardening plan.
    """

    __slots__ = (
        "age_cap_label",
        "age_cap_threshold",
        "age_reference_date",
        "birthdate_pattern",
        "cap_rules",
        "compliance_posture",
        "content_verification_required",
        "date_patterns",
        "date_sentinels",
        "date_value_sentinels",
        "date_unparsed_accept",
        "date_unparsed_threshold",
        "drop_patterns",
        "generalize_rules",
        "id_patterns",
        "keep_override_rules",
        "keep_patterns",
        "max_jitter_days",
        "must_drop_rules",
        "must_keep_rules",
        "non_subject_datasets",
        "orphan_quarantine_threshold",
        "redundant_subject_id_patterns",
        "small_cell_threshold",
        "subject_id_fields",
        "suppress_small_cell_patterns",
    )

    def __init__(
        self,
        *,
        compliance_posture: str,
        subject_id_fields: tuple[str, ...],
        date_patterns: list[re.Pattern[str]],
        id_patterns: list[IdRule],
        birthdate_pattern: re.Pattern[str] | None,
        max_jitter_days: int,
        orphan_quarantine_threshold: int,
        keep_patterns: list[re.Pattern[str]] | None = None,
        drop_patterns: list[re.Pattern[str]] | None = None,
        cap_rules: list[CapRule] | None = None,
        generalize_rules: list[GeneralizeRule] | None = None,
        suppress_small_cell_patterns: list[re.Pattern[str]] | None = None,
        age_cap_threshold: int = _DEFAULT_AGE_CAP_THRESHOLD,
        age_cap_label: str = _DEFAULT_AGE_CAP_LABEL,
        small_cell_threshold: int = _DEFAULT_SMALL_CELL_THRESHOLD,
        date_sentinels: frozenset[str] = frozenset(),
        date_value_sentinels: frozenset[str] = frozenset(),
        date_unparsed_threshold: int = 0,
        date_unparsed_accept: frozenset[str] = frozenset(),
        non_subject_datasets: tuple[str, ...] = (),
        must_drop_rules: list[MustRule] | None = None,
        must_keep_rules: list[MustRule] | None = None,
        keep_override_rules: list[KeepOverrideRule] | None = None,
        redundant_subject_id_patterns: list[IdRule] | None = None,
        age_reference_date: date | None = None,
        content_verification_required: tuple[str, ...] = (),
    ) -> None:
        if compliance_posture not in _VALID_POSTURES:
            raise PHIScrubError(
                f"Unknown compliance_posture {compliance_posture!r}. "
                f"Valid values: {sorted(_VALID_POSTURES)}"
            )
        if max_jitter_days < 1:
            raise PHIScrubError(f"max_jitter_days must be >= 1, got {max_jitter_days}")
        if not subject_id_fields:
            raise PHIScrubError("subject_id_fields must contain at least one field name")
        if age_cap_threshold < 0:
            raise PHIScrubError(f"age_cap_threshold must be >= 0, got {age_cap_threshold}")
        if small_cell_threshold < 1:
            raise PHIScrubError(f"small_cell_threshold must be >= 1, got {small_cell_threshold}")
        if date_unparsed_threshold < 0:
            raise PHIScrubError(
                f"date_unparsed_threshold must be >= 0, got {date_unparsed_threshold}"
            )
        self.compliance_posture = compliance_posture
        self.subject_id_fields = subject_id_fields
        self.date_patterns = date_patterns
        self.id_patterns = id_patterns
        self.birthdate_pattern = birthdate_pattern
        self.max_jitter_days = max_jitter_days
        self.orphan_quarantine_threshold = orphan_quarantine_threshold
        self.keep_patterns = keep_patterns or []
        self.drop_patterns = drop_patterns or []
        self.cap_rules = cap_rules or []
        self.generalize_rules = generalize_rules or []
        self.suppress_small_cell_patterns = suppress_small_cell_patterns or []
        self.age_cap_threshold = age_cap_threshold
        self.age_cap_label = age_cap_label
        self.small_cell_threshold = small_cell_threshold
        self.date_sentinels = date_sentinels
        self.date_value_sentinels = date_value_sentinels
        self.date_unparsed_threshold = date_unparsed_threshold
        self.date_unparsed_accept = date_unparsed_accept
        self.non_subject_datasets = non_subject_datasets
        self.must_drop_rules = must_drop_rules or []
        self.must_keep_rules = must_keep_rules or []
        self.keep_override_rules = keep_override_rules or []
        self.redundant_subject_id_patterns = redundant_subject_id_patterns or []
        self.age_reference_date = age_reference_date
        self.content_verification_required = content_verification_required

    def field_is_keep(self, name: str) -> bool:
        """Return True if *name* matches any ``keep_fields`` pattern.

        Keep rules short-circuit every other rule — a kept field passes
        through the scrubber unchanged with no audit event recorded.
        """
        return any(p.search(name) for p in self.keep_patterns)

    def field_is_drop(self, name: str) -> bool:
        return any(p.search(name) for p in self.drop_patterns)

    def cap_rule_for(self, name: str) -> CapRule | None:
        """Return the first matching :class:`CapRule` for *name*, or None."""
        for rule in self.cap_rules:
            if rule.matches(name):
                return rule
        return None

    def generalize_rule_for(self, name: str) -> GeneralizeRule | None:
        """Return the first matching :class:`GeneralizeRule` for *name*, or None."""
        for rule in self.generalize_rules:
            if rule.matches(name):
                return rule
        return None

    def field_is_suppress_small_cell(self, name: str) -> bool:
        return any(p.search(name) for p in self.suppress_small_cell_patterns)

    def field_is_date(self, name: str) -> bool:
        """Return True if *name* matches any ``date_fields`` pattern.

        Birthdate fields are excluded here — they are handled separately via
        :meth:`field_is_birthdate` so drop can be distinguished from jitter.
        """
        if self.birthdate_pattern is not None and self.birthdate_pattern.search(name):
            return False
        return any(p.search(name) for p in self.date_patterns)

    def id_label_for(self, name: str) -> str | None:
        """Return the semantic label for *name*, or None if no rule matches.

        First-match wins — the YAML order determines precedence when a
        field name is ambiguous (e.g. a generic ``(?:patient|subject)[-_]?id``
        pattern listed AFTER a specific ``^SUBJID$`` rule keeps the specific
        rule's label).
        """
        for rule in self.id_patterns:
            if rule.matches(name):
                return rule.label
        return None

    def field_is_id(self, name: str) -> bool:
        """Compatibility shim — True when any id rule matches *name*."""
        return self.id_label_for(name) is not None

    def field_is_birthdate(self, name: str) -> bool:
        return self.birthdate_pattern is not None and bool(self.birthdate_pattern.search(name))

    def value_is_date_sentinel(self, value: Any) -> bool:
        """True when *value* is a declared non-date literal allowed in a
        date column (Step 1). Absent config -> empty set -> always False."""
        if not isinstance(value, str):
            return False
        return value.strip().lower() in self.date_sentinels

    def value_is_date_value_sentinel(self, value: Any) -> bool:
        """True when *value* is a documented placeholder date that IS
        syntactically valid (e.g. ``1900-01-01`` for "unknown date") — checked
        BEFORE ``shift_date`` is attempted, because a value this recognizes
        would otherwise parse successfully and be silently jittered into a
        plausible-looking fake historical date. The caller redacts a match
        to ``None`` rather than preserving it raw — unlike a string sentinel
        such as ``"na"``, this value could coincidentally be a genuine
        calendar date, so it must never appear verbatim in published output.
        Distinct from ``date_sentinels``, which only ever applies after a
        parse failure. Absent config -> empty set -> always False."""
        if not isinstance(value, str):
            return False
        return value.strip().lower() in self.date_value_sentinels

    def redundant_subject_id_label_for(self, name: str) -> str | None:
        """Return the semantic label for *name* if it matches a
        ``redundant_subject_id_fields`` pattern, else None (Step 4)."""
        for rule in self.redundant_subject_id_patterns:
            if rule.matches(name):
                return rule.label
        return None

    def must_drop_match(self, name: str) -> MustRule | None:
        """Return the first matching ``must_drop`` :class:`MustRule`, or None."""
        for rule in self.must_drop_rules:
            if rule.matches(name):
                return rule
        return None

    def must_keep_match(self, name: str) -> MustRule | None:
        """Return the first matching ``must_keep`` :class:`MustRule`, or None."""
        for rule in self.must_keep_rules:
            if rule.matches(name):
                return rule
        return None

    def keep_override_for(self, name: str) -> KeepOverrideRule | None:
        """Return the first matching :class:`KeepOverrideRule`, or None."""
        for rule in self.keep_override_rules:
            if rule.matches(name):
                return rule
        return None

    def dataset_has_subject_column(self, dataset_file_name: str) -> bool:
        """False when *dataset_file_name* matches a ``non_subject_datasets``
        substring (Step 12b) — such datasets share one per-file date offset
        instead of a per-subject offset."""
        return not any(token in dataset_file_name for token in self.non_subject_datasets)


def _resolve_authority_note(path: Path, suffix: str) -> Path:
    """Resolve an authority-note path for the study pack that *path* lives in.

    Tries *path*'s own directory first — the natural location when *path*
    was itself resolved from a study pack (``config.resolve_study_pack``
    returns the pack dir; ``phi_scrub.yaml`` and ``authorities/`` are
    siblings there). Falls back to the active study's pack under
    ``config.BASE_DIR`` for callers that pass a scrub config living outside
    any pack layout (e.g. a bare tmp-path config in tests).
    """
    candidate = path.parent / suffix
    if candidate.is_file():
        return candidate
    return Path(config.BASE_DIR) / "study_packs" / config.STUDY_NAME / suffix


def load_scrub_config(path: Path | None = None) -> PHIScrubConfig | None:
    """Load + compile the scrub config. Returns ``None`` if file is absent.

    An absent config is NOT an error — it means phi_scrub is a no-op for this
    study, and the pipeline continues. This lets users opt in per-study by
    dropping a YAML file in place.

    When ``compliance_posture: limited_dataset`` is set, the function also
    verifies the authority note exists alongside *path* (the study pack
    directory) at :data:`_LIMITED_DATASET_AUTHORITY`.

    Loads the full rule set: keep / drop / cap / generalize / suppress /
    date / id patterns plus generalization_maps, age_cap, and
    small_cell_threshold constants.
    """
    path = path or config.PHI_SCRUB_CONFIG_PATH
    if not path.is_file():
        return None

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise PHIScrubError(f"phi_scrub config at {path} must be a mapping at the top level")

    posture = str(raw.get("compliance_posture", _POSTURE_SAFE_HARBOR))
    if posture == _POSTURE_LIMITED_DATASET:
        authority = _resolve_authority_note(path, _LIMITED_DATASET_AUTHORITY)
        if not authority.is_file():
            raise PHIScrubError(
                f"compliance_posture is 'limited_dataset' but the required "
                f"authority note is missing: {authority}. Create it to document "
                f"IRB approval + Data Use Agreement before running."
            )
    elif posture == _POSTURE_ICMR_CODED:
        authority = _resolve_authority_note(path, _ICMR_CODED_AUTHORITY)
        if not authority.is_file():
            raise PHIScrubError(
                f"compliance_posture is 'icmr_coded_dataset' but the required "
                f"authority note is missing: {authority}. Create it to document "
                f"the ICMR 2017 s2.3 basis, key-separation practice, and "
                f"small_cell_threshold rationale before running."
            )

    # Accept either `subject_id_fields` (plural, list) or legacy
    # `subject_id_field` (singular, str). Plural wins when both present.
    _raw_plural = raw.get("subject_id_fields")
    _raw_singular = raw.get("subject_id_field")
    if _raw_plural is not None:
        if not isinstance(_raw_plural, list) or not _raw_plural:
            raise PHIScrubError("subject_id_fields must be a non-empty list of column names")
        subject_id_fields = tuple(str(f) for f in _raw_plural)
    elif _raw_singular is not None:
        subject_id_fields = (str(_raw_singular),)
    else:
        subject_id_fields = ("SUBJID",)

    def _compile_list(key: str) -> list[re.Pattern[str]]:
        patterns = raw.get(key) or []
        if not isinstance(patterns, list):
            raise PHIScrubError(f"{key} must be a list of regex strings")
        return [re.compile(str(p), re.IGNORECASE) for p in patterns]

    date_patterns = _compile_list("date_fields")
    keep_patterns = _compile_list("keep_fields")
    drop_patterns = _compile_list("drop_fields")
    suppress_patterns = _compile_list("suppress_small_cell_fields")

    # id_fields is structured: each entry must be a mapping with
    # ``pattern`` (regex) and ``label`` (short semantic category).
    # Plain-string entries are rejected — an unlabelled id field would
    # lose its category in the pseudonym output, which defeats the
    # whole point of the v3 scheme.
    raw_id_rules = raw.get("id_fields") or []
    if not isinstance(raw_id_rules, list):
        raise PHIScrubError("id_fields must be a list of {pattern, label} mappings")
    id_patterns: list[IdRule] = []
    for idx, entry in enumerate(raw_id_rules):
        if not isinstance(entry, dict):
            raise PHIScrubError(
                f"id_fields[{idx}] must be a mapping with 'pattern' + 'label'; "
                f"plain strings are no longer accepted in v3"
            )
        pat_str = entry.get("pattern")
        label = entry.get("label")
        if not pat_str or not label:
            raise PHIScrubError(
                f"id_fields[{idx}] requires both 'pattern' (regex) and 'label' "
                f"(short semantic category such as SUBJ / FAM / LAB)"
            )
        id_patterns.append(
            IdRule(
                pattern=re.compile(str(pat_str), re.IGNORECASE),
                label=str(label),
            )
        )

    birthdate_raw = raw.get("birthdate_field")
    birthdate_pattern = re.compile(str(birthdate_raw), re.IGNORECASE) if birthdate_raw else None

    max_jitter_days = int(raw.get("max_jitter_days", _DEFAULT_MAX_JITTER_DAYS))
    orphan_threshold = int(raw.get("orphan_quarantine_threshold", _DEFAULT_ORPHAN_THRESHOLD))
    small_cell_threshold = int(raw.get("small_cell_threshold", _DEFAULT_SMALL_CELL_THRESHOLD))

    # Age cap — top-level constants, also default for cap_fields entries
    # that do not specify their own threshold/label.
    age_cap_raw = raw.get("age_cap") or {}
    if not isinstance(age_cap_raw, dict):
        raise PHIScrubError("age_cap must be a mapping with threshold + label")
    default_cap_threshold = int(age_cap_raw.get("threshold", _DEFAULT_AGE_CAP_THRESHOLD))
    default_cap_label = str(age_cap_raw.get("label", _DEFAULT_AGE_CAP_LABEL))

    # Cap rules. Each entry is a dict with `pattern` and optional `threshold`/
    # `label` overrides. If no overrides are given, the top-level age_cap
    # defaults apply.
    raw_cap_rules = raw.get("cap_fields") or []
    if not isinstance(raw_cap_rules, list):
        raise PHIScrubError("cap_fields must be a list of mappings")
    cap_rules: list[CapRule] = []
    for idx, entry in enumerate(raw_cap_rules):
        if not isinstance(entry, dict):
            raise PHIScrubError(
                f"cap_fields[{idx}] must be a mapping with at least a 'pattern' key"
            )
        pat_str = entry.get("pattern")
        if not pat_str:
            raise PHIScrubError(f"cap_fields[{idx}] is missing 'pattern'")
        threshold = int(entry.get("threshold", default_cap_threshold))
        label = str(entry.get("label", default_cap_label))
        cap_rules.append(
            CapRule(
                pattern=re.compile(str(pat_str), re.IGNORECASE),
                threshold=threshold,
                label=label,
            )
        )

    # Generalization maps — normalized to lower-case keys so we can do a
    # case-insensitive lookup at scrub time without allocating per-row.
    raw_gen_maps = raw.get("generalization_maps") or {}
    if not isinstance(raw_gen_maps, dict):
        raise PHIScrubError("generalization_maps must be a mapping of name → {value: value}")
    gen_maps: dict[str, dict[str, str]] = {}
    for name, mapping in raw_gen_maps.items():
        if not isinstance(mapping, dict):
            raise PHIScrubError(f"generalization_maps[{name}] must be a mapping of string → string")
        gen_maps[str(name)] = {str(src).strip().lower(): str(dst) for src, dst in mapping.items()}

    # Generalize rules. Each entry references a named mapping above.
    raw_gen_rules = raw.get("generalize_fields") or []
    if not isinstance(raw_gen_rules, list):
        raise PHIScrubError("generalize_fields must be a list of mappings")
    generalize_rules: list[GeneralizeRule] = []
    for idx, entry in enumerate(raw_gen_rules):
        if not isinstance(entry, dict):
            raise PHIScrubError(
                f"generalize_fields[{idx}] must be a mapping with 'pattern' + 'mapping'"
            )
        pat_str = entry.get("pattern")
        mapping_name = entry.get("mapping")
        if not pat_str or not mapping_name:
            raise PHIScrubError(f"generalize_fields[{idx}] requires 'pattern' and 'mapping'")
        mapping = gen_maps.get(str(mapping_name))
        if mapping is None:
            raise PHIScrubError(
                f"generalize_fields[{idx}] references unknown mapping "
                f"{mapping_name!r}; define it under generalization_maps"
            )
        generalize_rules.append(
            GeneralizeRule(
                pattern=re.compile(str(pat_str), re.IGNORECASE),
                mapping_name=str(mapping_name),
                mapping=mapping,
            )
        )

    # must_drop / must_keep — absolute guards (Step 3). Each entry is a
    # mapping with 'basis' (citation string) + 'patterns' (list of regex).
    def _compile_must_rules(key: str) -> list[MustRule]:
        raw_rules = raw.get(key) or []
        if not isinstance(raw_rules, list):
            raise PHIScrubError(f"{key} must be a list of {{basis, patterns}} mappings")
        rules: list[MustRule] = []
        for idx, entry in enumerate(raw_rules):
            if not isinstance(entry, dict):
                raise PHIScrubError(f"{key}[{idx}] must be a mapping with 'basis' + 'patterns'")
            basis = entry.get("basis")
            raw_patterns = entry.get("patterns")
            if not basis or not isinstance(raw_patterns, list) or not raw_patterns:
                raise PHIScrubError(
                    f"{key}[{idx}] requires 'basis' (citation string) and a "
                    f"non-empty 'patterns' list"
                )
            rules.append(
                MustRule(
                    basis=str(basis),
                    patterns=[re.compile(str(p), re.IGNORECASE) for p in raw_patterns],
                )
            )
        return rules

    must_drop_rules = _compile_must_rules("must_drop")
    must_keep_rules = _compile_must_rules("must_keep")

    # keep_overrides — declared acknowledgement of a keep-shadows-PHI-rule
    # conflict (Step 6c / consumed by validate_rule_catalog, Step 10a).
    raw_keep_overrides = raw.get("keep_overrides") or []
    if not isinstance(raw_keep_overrides, list):
        raise PHIScrubError("keep_overrides must be a list of {column, shadows, rationale} mappings")
    keep_override_rules: list[KeepOverrideRule] = []
    for idx, entry in enumerate(raw_keep_overrides):
        if not isinstance(entry, dict):
            raise PHIScrubError(f"keep_overrides[{idx}] must be a mapping")
        column = entry.get("column")
        shadows = entry.get("shadows")
        rationale = entry.get("rationale")
        if not column or not shadows or not rationale:
            raise PHIScrubError(
                f"keep_overrides[{idx}] requires 'column' (regex), 'shadows' "
                f"(the action it shadows), and 'rationale'"
            )
        keep_override_rules.append(
            KeepOverrideRule(
                pattern=re.compile(str(column), re.IGNORECASE),
                shadows=str(shadows),
                rationale=str(rationale),
            )
        )

    # redundant_subject_id_fields — value-conditional (Step 4). Same shape
    # as id_fields: {pattern, label}.
    raw_redundant = raw.get("redundant_subject_id_fields") or []
    if not isinstance(raw_redundant, list):
        raise PHIScrubError("redundant_subject_id_fields must be a list of {pattern, label} mappings")
    redundant_subject_id_patterns: list[IdRule] = []
    for idx, entry in enumerate(raw_redundant):
        if not isinstance(entry, dict):
            raise PHIScrubError(f"redundant_subject_id_fields[{idx}] must be a mapping")
        pat_str = entry.get("pattern")
        label = entry.get("label")
        if not pat_str or not label:
            raise PHIScrubError(
                f"redundant_subject_id_fields[{idx}] requires 'pattern' and 'label'"
            )
        redundant_subject_id_patterns.append(
            IdRule(pattern=re.compile(str(pat_str), re.IGNORECASE), label=str(label))
        )

    # date_sentinels — declared non-date literals (Step 1). No built-in
    # default; absent key -> empty set -> every unparsed value is redacted.
    raw_sentinels = raw.get("date_sentinels") or []
    if not isinstance(raw_sentinels, list):
        raise PHIScrubError("date_sentinels must be a list of strings")
    date_sentinels = frozenset(str(s).strip().lower() for s in raw_sentinels)

    # date_value_sentinels — documented placeholder dates that ARE
    # syntactically valid (Step 1 hardening follow-up). Checked before
    # shift_date, unlike date_sentinels which only applies post-parse-failure.
    raw_value_sentinels = raw.get("date_value_sentinels") or []
    if not isinstance(raw_value_sentinels, list):
        raise PHIScrubError("date_value_sentinels must be a list of strings")
    date_value_sentinels = frozenset(str(s).strip().lower() for s in raw_value_sentinels)

    date_unparsed_threshold = int(raw.get("date_unparsed_threshold", 0))
    raw_accept = raw.get("date_unparsed_accept") or []
    if not isinstance(raw_accept, list):
        raise PHIScrubError("date_unparsed_accept must be a list of '<file>:<COLUMN>' strings")
    date_unparsed_accept = frozenset(str(s) for s in raw_accept)

    # non_subject_datasets — Step 12b. Absent -> every dataset is treated
    # as subject-bearing (fail-closed: unresolvable rows quarantine).
    raw_non_subject = raw.get("non_subject_datasets") or []
    if not isinstance(raw_non_subject, list):
        raise PHIScrubError("non_subject_datasets must be a list of filename substrings")
    non_subject_datasets = tuple(str(s) for s in raw_non_subject)

    # content_verification_required — Step 11e. Columns that were manually
    # un-shadowed from `keep` to `drop` because content ambiguity could not
    # be resolved by pattern alone (e.g. CXR_SIGN / SC_PROCSIG: signature
    # field by name, but may hold a numeric measurement). Drop is applied
    # now; each listed column still surfaces a one-time destructive review
    # entry until an operator's phi_review_signoff.yaml confirms the drop
    # (or the column moves to must_keep + keep_overrides instead).
    raw_content_verify = raw.get("content_verification_required") or []
    if not isinstance(raw_content_verify, list):
        raise PHIScrubError("content_verification_required must be a list of column names")
    content_verification_required = tuple(str(s) for s in raw_content_verify)

    # age_reference_date — only required when the Step 5b no-age-column
    # branch activates; a missing value there is a hard failure at scrub
    # time (not here, since load_scrub_config has no header knowledge yet).
    raw_age_ref = raw.get("age_reference_date")
    age_reference_date: date | None = None
    if raw_age_ref is not None:
        if isinstance(raw_age_ref, date):
            age_reference_date = raw_age_ref if not isinstance(raw_age_ref, datetime) else raw_age_ref.date()
        else:
            try:
                age_reference_date = datetime.strptime(str(raw_age_ref), "%Y-%m-%d").date()
            except ValueError as exc:
                raise PHIScrubError(
                    f"age_reference_date must be an ISO date (YYYY-MM-DD), got {raw_age_ref!r}"
                ) from exc

    return PHIScrubConfig(
        compliance_posture=posture,
        subject_id_fields=subject_id_fields,
        date_patterns=date_patterns,
        id_patterns=id_patterns,
        birthdate_pattern=birthdate_pattern,
        max_jitter_days=max_jitter_days,
        orphan_quarantine_threshold=orphan_threshold,
        keep_patterns=keep_patterns,
        drop_patterns=drop_patterns,
        cap_rules=cap_rules,
        generalize_rules=generalize_rules,
        suppress_small_cell_patterns=suppress_patterns,
        age_cap_threshold=default_cap_threshold,
        age_cap_label=default_cap_label,
        small_cell_threshold=small_cell_threshold,
        date_sentinels=date_sentinels,
        date_value_sentinels=date_value_sentinels,
        date_unparsed_threshold=date_unparsed_threshold,
        date_unparsed_accept=date_unparsed_accept,
        non_subject_datasets=non_subject_datasets,
        must_drop_rules=must_drop_rules,
        must_keep_rules=must_keep_rules,
        keep_override_rules=keep_override_rules,
        redundant_subject_id_patterns=redundant_subject_id_patterns,
        age_reference_date=age_reference_date,
        content_verification_required=content_verification_required,
    )

# ── Disposition policy resolution (Step 3, Step 10a) ───────────────────────

_RESOLVE_BIRTHDATE_DROP = "birthdate_drop"
_RESOLVE_JITTER_DATE = "jitter_date"
_RESOLVE_PSEUDONYMIZE = "pseudonymize"
_RESOLVE_REDUNDANT_SUBJECT_ID = "redundant_subject_id"
_RESOLVE_NONE = "none"


def _would_jitter_birthdate(cfg: PHIScrubConfig, *, age_variable_present: bool) -> bool:
    """Whether birthdate is jittered rather than dropped — mirrors the
    Step 5c decision applied per-row in :func:`_scrub_row`."""
    return cfg.compliance_posture == _POSTURE_LIMITED_DATASET or not age_variable_present


def resolve_action(
    cfg: PHIScrubConfig,
    name: str,
    *,
    age_variable_present: bool = True,
) -> str:
    """Resolve the column-NAME-only disposition for *name* — never reads a
    dataset value.

    Mirrors the row-level rule priority in :func:`_scrub_row` for every
    rule that is name-only. The one value-conditional rule
    (redundant-subject-id drop-vs-pseudonymize) resolves here to the
    constant tag ``"redundant_subject_id"`` since the actual choice depends
    on a row value this function never sees. Used by
    :func:`validate_rule_catalog` (Step 10a) and
    :func:`crosscheck_sot_policy` (Step 10b).

    Resolution order: must_drop -> keep -> birthdate -> redundant_subject_id
    -> drop -> cap -> generalize -> suppress -> date -> id -> none.

    Class B preference: when the raw resolution is "drop" *and* an
    ``id_fields`` pattern also matches *and* ``must_drop`` does not match,
    the resolution upgrades to "pseudonymize" — a Class A/B disagreement
    never resolves to deletion.
    """
    if cfg.must_drop_match(name) is not None:
        return _ACTION_DROP

    if cfg.field_is_keep(name):
        action = _ACTION_KEEP
    elif cfg.field_is_birthdate(name):
        jitter = _would_jitter_birthdate(cfg, age_variable_present=age_variable_present)
        action = _RESOLVE_JITTER_DATE if jitter else _RESOLVE_BIRTHDATE_DROP
    elif cfg.redundant_subject_id_label_for(name) is not None:
        action = _RESOLVE_REDUNDANT_SUBJECT_ID
    elif cfg.field_is_drop(name):
        action = _ACTION_DROP
    elif cfg.cap_rule_for(name) is not None:
        action = _ACTION_CAP
    elif cfg.generalize_rule_for(name) is not None:
        action = _ACTION_GENERALIZE
    elif cfg.field_is_suppress_small_cell(name):
        action = _ACTION_SUPPRESS
    elif cfg.field_is_date(name):
        action = _RESOLVE_JITTER_DATE
    elif cfg.id_label_for(name) is not None:
        action = _RESOLVE_PSEUDONYMIZE
    else:
        action = _RESOLVE_NONE

    if action == _ACTION_DROP and cfg.id_label_for(name) is not None:
        action = _RESOLVE_PSEUDONYMIZE

    return action


def _shadowed_action_for(
    cfg: PHIScrubConfig, name: str, *, age_variable_present: bool
) -> str | None:
    """What action would apply to *name* if ``keep`` did not short-circuit
    it, or None when nothing more specific would have matched (an
    unremarkable false-positive-guard keep). Restricted to
    birthdate/redundant_subject_id/drop/date/id per the plan — cap /
    generalize / suppress shadows are not guarded here."""
    if cfg.field_is_birthdate(name):
        jitter = _would_jitter_birthdate(cfg, age_variable_present=age_variable_present)
        return "date" if jitter else "birthdate"
    if cfg.redundant_subject_id_label_for(name) is not None:
        return "redundant_subject_id"
    if cfg.field_is_drop(name):
        return "drop"
    if cfg.field_is_date(name):
        return "date"
    if cfg.id_label_for(name) is not None:
        return "id"
    return None


def validate_rule_catalog(
    cfg: PHIScrubConfig,
    headers: Iterable[str],
    *,
    age_variable_present: bool = True,
) -> None:
    """Validate the compiled rule catalog against *headers* — column NAMES
    only, never dataset values — before any row is scrubbed (Step 10a).

    Raises:
        PHIPolicyViolationError: a ``must_drop`` name resolves to anything
            but drop, or a ``must_keep`` name resolves to drop / birthdate
            drop / no rule at all.
        PHIRuleConflictError: a ``keep`` rule shadows birthdate/drop/date/
            id/redundant_subject_id with no matching ``keep_overrides``
            entry (same column, matching ``shadows`` tag).
    """
    for name in headers:
        must_drop_rule = cfg.must_drop_match(name)
        must_keep_rule = cfg.must_keep_match(name)
        action = resolve_action(cfg, name, age_variable_present=age_variable_present)

        if must_drop_rule is not None and action != _ACTION_DROP:
            raise PHIPolicyViolationError(
                f"must_drop violation: column={name!r} guard={must_drop_rule.basis!r} "
                f"resolved_action={action!r} (must resolve to drop)"
            )
        if must_keep_rule is not None and action in (
            _ACTION_DROP,
            _RESOLVE_BIRTHDATE_DROP,
            _RESOLVE_NONE,
        ):
            raise PHIPolicyViolationError(
                f"must_keep violation: column={name!r} guard={must_keep_rule.basis!r} "
                f"resolved_action={action!r} (must not resolve to drop/null)"
            )

        if cfg.field_is_keep(name):
            shadowed = _shadowed_action_for(cfg, name, age_variable_present=age_variable_present)
            if shadowed is not None:
                override = cfg.keep_override_for(name)
                if override is None or override.shadows != shadowed:
                    raise PHIRuleConflictError(
                        f"column={name!r} keep_pattern shadows a {shadowed!r} rule with "
                        f"no matching keep_overrides entry (shadows={shadowed!r})"
                    )


# ── Key management ──────────────────────────────────────────────────────────


def load_key(path: Path | None = None) -> bytes:
    """Load the HMAC key from the sidecar file.

    Raises :class:`PHIKeyMissingError` if the file is absent and
    :class:`PHIKeyPermissionError` if the file mode is not ``0600``.
    """
    path = path or config.PHI_KEY_PATH
    if not path.is_file():
        raise PHIKeyMissingError(
            f"PHI HMAC key not found at {path.name}. Use the web UI Load Study flow, "
            "or ask a developer/operator to provision the sidecar PHI key."
        )

    mode = path.stat().st_mode & 0o777
    if mode != _KEY_FILE_MODE:
        raise PHIKeyPermissionError(
            f"PHI key file {path.name} has mode {oct(mode)}; must be {oct(_KEY_FILE_MODE)}. "
            f"Fix with: chmod 600 {path.name}"
        )

    text = path.read_text(encoding="utf-8").strip()
    if len(text) != _KEY_HEX_LEN:
        raise PHIScrubError(
            f"PHI key at {path.name} must be {_KEY_HEX_LEN} hex chars (32 bytes); got {len(text)}"
        )
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise PHIScrubError(f"PHI key at {path.name} is not valid hex: {exc}") from exc


def bootstrap_key(path: Path | None = None) -> Path:
    """Generate a new 32-byte HMAC key and write it to the sidecar location.

    Refuses to overwrite an existing key (would silently invalidate every
    prior pseudonym). Returns the path on success.
    """
    path = path or config.PHI_KEY_PATH
    if path.exists():
        raise FileExistsError(
            f"PHI key already exists at {path}. Refusing to overwrite. "
            f"To rotate, delete the file explicitly — this will invalidate "
            f"every prior pseudonym and require full re-ingestion."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    # Restrict parent dir perms best-effort (umask-dependent).
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)

    key_hex = secrets.token_hex(32)
    with path.open("w", encoding="utf-8") as _kf:
        _kf.write(key_hex)
        _kf.flush()
        os.fsync(_kf.fileno())
    path.chmod(_KEY_FILE_MODE)
    return path


# ── Scrub primitives ────────────────────────────────────────────────────────


def pseudo_id(raw_id: str, *, key: bytes, label: str = "ID") -> str:
    """Return ``RID_<LABEL>_<alpha12>`` with cryptographic domain separation.

    The HMAC input is ``f"{label}:{raw_id}"`` so the same raw value under
    different ``label`` arguments produces different pseudonyms. This
    implements the domain-separation property used by HKDF's ``info``
    parameter (RFC 5869 §3.2): if an adversary obtains two datasets where
    the same person appears under different id categories (e.g. ``FID``
    and ``SUBJID``), they cannot link records by pseudonym equality.

    Same ``(label, raw_id, key)`` always yields the same output → in-category
    longitudinal linkage is preserved across files, which is what the agent
    needs for cohort-level joins. Different ``key`` → disjoint pseudonym
    namespace.

    Args:
        raw_id: the raw identifier string (already stripped by the caller).
        key: 32-byte HMAC key loaded from the sidecar keyfile.
        label: short semantic category (e.g. ``"SUBJ"``, ``"FAM"``, ``"LAB"``).
            Propagated into the HMAC input for domain separation and retained
            inside the opaque ``RID_`` output token for audit clarity.

    Returns:
        ``f"RID_{label}_{alpha12}"`` — the output stays self-describing while
        avoiding raw-ID and phone-like shapes.
    """
    domain_input = f"{label}:{raw_id}".encode()
    raw_tag = hmac.new(key, domain_input, hashlib.sha256).hexdigest()[:_PSEUDO_TAG_CHARS]
    tag = raw_tag.translate(_HEX_TO_ALPHA)
    return f"RID_{label}_{tag}"


def date_offset_days(subject_id: str, *, key: bytes, max_days: int) -> int:
    """Per-subject deterministic offset in ``[-max_days, +max_days]`` inclusive.

    Algorithm: ``int.from_bytes(hmac_sha256(key, subject_id)[:4], 'big') %
    (2*max_days + 1) - max_days``.
    """
    if max_days < 1:
        raise ValueError(f"max_days must be >= 1, got {max_days}")
    digest = hmac.new(key, subject_id.encode("utf-8"), hashlib.sha256).digest()
    raw = int.from_bytes(digest[:_OFFSET_DIGEST_BYTES], "big")
    return (raw % (2 * max_days + 1)) - max_days


def _format_date(dt: datetime, *, fmt: str, has_time: bool, ampm: str | None) -> str:
    """Re-serialize *dt* in the detected source format.

    Preserves ISO / M-D-Y / D-M-Y layout. Two-digit years are promoted to
    four-digit on output (minor, not a correctness concern).
    """
    if fmt == "iso":
        if has_time:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d")

    if fmt == "mdy":
        date_part = f"{dt.month}/{dt.day}/{dt.year:04d}"
    elif fmt == "dmy":
        date_part = f"{dt.day}/{dt.month}/{dt.year:04d}"
    elif fmt == "compact_dmy":
        return f"{dt.day:02d}{dt.month:02d}{dt.year:04d}"
    elif fmt == "compact_mdy":
        return f"{dt.month:02d}{dt.day:02d}{dt.year:04d}"
    elif fmt == "compact_ymd":
        return f"{dt.year:04d}{dt.month:02d}{dt.day:02d}"
    else:
        raise PHIScrubError(f"unsupported date format: {fmt}")

    if not has_time:
        return date_part

    if ampm:
        # Preserve 12-hour AM/PM layout
        hour_12 = dt.hour % 12 or 12
        time_part = f"{hour_12}:{dt.minute:02d}:{dt.second:02d} {ampm}"
    else:
        time_part = f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    return f"{date_part} {time_part}"


def shift_date(
    value: str,
    offset_days: int,
    *,
    field_name: str | None = None,
    date_locales: dict[str, str] | None = None,
) -> str | None:
    """Parse *value*, shift by ``offset_days``, re-emit in the same format.

    Returns ``None`` if the string does not parse as a date. Non-string
    inputs must be handled by the caller.

    Parameters
    ----------
    value:
        Raw date string to shift.
    offset_days:
        Number of days to add (may be negative).
    field_name:
        Column name for locale resolution (DMY allowlist + manifest lookup).
    date_locales:
        Per-column locale overrides from the study's ``_forms_manifest.yaml``
        ``date_locales:`` section.  Passed through to :func:`parse_date`.
    """
    parsed = parse_date(value, field_name=field_name, date_locales=date_locales)
    if parsed is None:
        return None
    try:
        new_dt = parsed.dt + timedelta(days=offset_days)
    except (OverflowError, ValueError):
        return None
    return _format_date(
        new_dt,
        fmt=parsed.format,
        has_time=parsed.has_time,
        ampm=parsed.ampm,
    )


def _coerce_numeric(value: Any) -> float | None:
    """Return *value* as a float if convertible, else None.

    Accepts int, float, and numeric strings ("89", "89.0", " 89 "). Empty
    strings, None, and un-numeric text return None — caller should leave
    the field unchanged.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None  # guard: bool is an int subclass in Python
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def cap_numeric(value: Any, *, threshold: int, label: str) -> tuple[Any, bool]:
    """Cap numeric *value* to *label* when strictly greater than *threshold*.

    Returns ``(new_value, was_capped)``. Non-numeric / empty values pass
    through unchanged with ``was_capped=False``. Values ≤ threshold also
    pass through unchanged — capping affects the tail only.

    Used for HIPAA §164.514(b)(2)(i)(C) age-over-89 aggregation and any
    similarly-shaped numeric-tail collapse rule. Because capping runs
    per-cell (not per-distribution), it is safe to apply in a streaming
    scrubber without seeing the rest of the dataset.
    """
    num = _coerce_numeric(value)
    if num is None:
        return value, False
    if num > threshold:
        return label, True
    return value, False


def generalize_value(value: Any, *, mapping: dict[str, str]) -> tuple[Any, bool]:
    """Map *value* to a broader category via *mapping* (case-insensitive).

    Returns ``(new_value, was_generalized)``. Non-string / empty values
    pass through unchanged. Strings not present in the mapping also pass
    through unchanged — operators must curate the mapping to cover every
    valid value; unknown values surface as-is so the audit report flags
    coverage gaps (via the false-count per field).
    """
    if value is None:
        return value, False
    if not isinstance(value, str):
        return value, False
    key = value.strip().lower()
    if not key:
        return value, False
    replaced = mapping.get(key)
    if replaced is None:
        return value, False
    return replaced, True


def suppress_small_cell(value: Any, *, threshold: int) -> tuple[Any, bool]:
    """Clamp numeric *value* to at most *threshold*.

    Returns ``(new_value, was_clamped)``. Non-numeric / empty values pass
    through unchanged. Values strictly greater than the threshold collapse
    to the threshold itself (NOT to a label) so downstream numeric
    analyses remain type-stable.

    ICMR §11.7 recommends ``threshold=5`` for household / contact counts
    in cohort studies where unique household demographics could re-identify
    a subject. For counts at or below the threshold, the value passes
    through — small cells here are an analytic concern, not a privacy
    concern.
    """
    num = _coerce_numeric(value)
    if num is None:
        return value, False
    if num > threshold:
        # Preserve original type where possible: int stays int, float stays float.
        if isinstance(value, int | float) and not isinstance(value, bool):
            return type(value)(threshold), True
        return threshold, True
    return value, False


# ── Orchestration ───────────────────────────────────────────────────────────


def _apply_field_only_rules(row: dict[str, Any], *, cfg: PHIScrubConfig) -> dict[str, int]:
    """Remove fields that can be scrubbed without a subject ID.

    Mutates row in place; returns drop counts (same ``phi-scrub-<scope>:<field>``
    shape as ``_scrub_row``'s second return value).

    Applies in-place to *row*:
    * ``drop_fields``   — field removed entirely (rule 3 in the main scrub loop).
    * ``birthdate_field`` — field dropped unconditionally regardless of posture.
      Under ``limited_dataset``, jitter (rule 7) requires a subject_id; orphans by
      definition lack one, so the only safe option is drop.

    Deliberately omits rules that need per-subject state: date jitter,
    ID pseudonymization, cap, generalize, and small-cell suppression.
    This is the "partial scrub" applied to orphan rows before quarantine write.
    """
    counts: dict[str, int] = {}

    def _bump(scope: str, field: str) -> None:
        k = f"phi-scrub-{scope}:{field}"
        counts[k] = counts.get(k, 0) + 1

    for field in list(row.keys()):
        if field.startswith("__"):
            continue
        if cfg.field_is_keep(field):
            continue
        if cfg.field_is_birthdate(field):
            del row[field]
            _bump("birthdate-drop", field)
            continue
        if cfg.field_is_drop(field):
            del row[field]
            _bump("drop", field)

    return counts


def _resolve_subject_id(
    row: dict[str, Any],
    candidates: tuple[str, ...],
    dataset_has_subject_col: bool = True,
) -> str:
    """Resolve a subject ID value from *row* by trying *candidates* in order.

    Matching strategy:
      1. Exact field match on any candidate (e.g. ``SUBJID``, ``FID``).
      2. Suffix match on any candidate — ``SUBJID`` also matches
         form-prefixed variants like ``NC_SUBJID``, ``IS_SUBJID``,
         ``_<PREFIX>_SUBJID`` etc.

    Exact match always wins over suffix match so deterministic date-offset
    keying is preserved across heterogeneous CRF datasets.

    Returns the first non-empty stripped value, or an empty string if the
    row has no resolvable subject identifier (caller quarantines).
    """
    for cand in candidates:
        val = row.get(cand)
        if val is not None:
            s = str(val).strip()
            if s:
                return s
    for cand in candidates:
        suffix = "_" + cand
        for key, val in row.items():
            if not key.endswith(suffix):
                continue
            if val is None:
                continue
            s = str(val).strip()
            if s:
                return s

    if not dataset_has_subject_col:
        return row.get("source_file", "SYSTEM")
    return ""


def _now_utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Legacy "all nines" missing/unknown-value convention extended to dates
# (e.g. "99-99-9999", "9999-99-99", "99999999"). Every one of these is
# already guaranteed unparseable as a calendar date (99 is never a valid
# month or day), so this only ever matches inside the post-parse-failure
# branch below — it can never misclassify a real date, since a real date
# always has at least one non-9 digit somewhere in day/month/year.
_ALL_NINES_SENTINEL_MIN_DIGITS = 2


def _looks_like_all_nines_placeholder(value: str) -> bool:
    """True when *value*'s digits are ALL '9' (e.g. '99-99-9999',
    '99999999') — the classic legacy missing/unknown-date convention."""
    digits_only = re.sub(r"\D", "", value)
    return len(digits_only) >= _ALL_NINES_SENTINEL_MIN_DIGITS and set(digits_only) == {"9"}


def _jitter_or_redact_date(
    raw_val: Any,
    *,
    field: str,
    offset: int,
    date_locales: dict[str, str] | None,
    cfg: PHIScrubConfig,
    bump: Callable[[str, str], None],
) -> Any:
    """Shared Step-1 fail-closed date transform.

    Four-way outcome: a documented placeholder that would otherwise parse
    successfully (e.g. "1900-01-01" for "unknown date") -> redacted to
    ``None`` before shift_date is even tried — never preserved raw, because
    unlike a string sentinel this value COULD coincidentally be a genuine
    calendar date, so nothing about this path may ever publish it verbatim;
    parses -> jittered value; unparsed but a declared string sentinel or an
    all-nines placeholder (e.g. "99-99-9999", structurally never a valid
    calendar date under any locale) -> value preserved unchanged; unparsed
    and undeclared -> redacted to ``None``. The raw value never survives a
    failed parse silently — that was the fail-open behind 24,669 raw
    published dates.
    """
    raw_str = str(raw_val)
    if cfg.value_is_date_value_sentinel(raw_val):
        bump("date-value-sentinel-redacted", field)
        return None
    shifted = shift_date(raw_str, offset, field_name=field, date_locales=date_locales)
    if shifted is not None:
        bump("date", field)
        return shifted
    if cfg.value_is_date_sentinel(raw_val) or _looks_like_all_nines_placeholder(raw_str):
        bump("date-sentinel", field)
        return raw_val
    bump("date-unparsed-redacted", field)
    return None


def _scrub_row(
    row: dict[str, Any],
    *,
    cfg: PHIScrubConfig,
    key: bytes,
    date_locales: dict[str, str] | None = None,
    dataset_has_subject_col: bool = True,
    age_variable_present: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    """Scrub a single row. Return (scrubbed_row_or_None, per-field-counts).

    Priority (first match wins per field):
        1. keep_patterns          — allowlist, short-circuits every other rule
        2. birthdate_pattern      — posture-dependent drop or jitter
        3. redundant_subject_id_patterns — value-conditional drop/pseudonymize
        4. drop_patterns          — field removed from row entirely
        5. cap_rules              — numeric > threshold -> label
        6. generalize_rules       — value looked up in mapping
        7. suppress_small_cell    — numeric > threshold -> threshold
        8. date_patterns          — jitter via SANT per-subject offset,
                                     fail-closed: unparsed + undeclared -> null
        9. id_patterns            — HMAC-SHA256 pseudonymize

    Returns ``None`` for the row when no resolvable subject_id — caller
    quarantines. Per-field counts are keyed by scope label
    (``phi-scrub-drop:FIELD``, ``phi-scrub-cap:FIELD`` etc.).

    *age_variable_present* is computed once per study (Step 5b, study
    scope) and passed through unchanged for every row.
    """
    if "_metadata" in row and isinstance(row["_metadata"], dict) and row["_metadata"].get("type") == "column_structure":
        row[_SCRUB_MARKER_FIELD] = _SCRUB_VERSION
        return row, {}

    subj_id = _resolve_subject_id(row, cfg.subject_id_fields, dataset_has_subject_col=dataset_has_subject_col)
    if not subj_id:
        return None, {}

    offset = date_offset_days(subj_id, key=key, max_days=cfg.max_jitter_days)
    counts: dict[str, int] = {}

    def _bump(scope: str, field: str) -> None:
        k = f"phi-scrub-{scope}:{field}"
        counts[k] = counts.get(k, 0) + 1

    jitter_birthdate = _would_jitter_birthdate(cfg, age_variable_present=age_variable_present)

    # Iterate a snapshot of keys so we can mutate row in place.
    for field in list(row.keys()):
        # Skip pipeline-internal metadata
        if field.startswith("__"):
            continue

        # 1. KEEP — allowlist short-circuits every other rule
        if cfg.field_is_keep(field):
            if cfg.keep_override_for(field) is not None:
                _bump("keep-override", field)
            continue

        # 2. BIRTHDATE — posture-dependent drop or jitter (Step 5c). Under
        # icmr_coded_dataset/safe_harbor with an age variable present the
        # field is dropped; under limited_dataset, or when the study has no
        # separate age variable at all, it is jittered like any other date
        # (dropping it in that case would destroy age entirely).
        if cfg.field_is_birthdate(field):
            if not jitter_birthdate:
                del row[field]
                _bump("birthdate-drop", field)
                continue
            raw_val = row[field]
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue
            if not age_variable_present and cfg.age_reference_date is not None:
                # Step 5d: age is no longer separately capped once DOB is
                # jittered instead of dropped — recompute it from the RAW
                # DOB against the fixed reference date and drop the row's
                # DOB outright when the derived age exceeds the HIPAA cap,
                # instead of jittering it.
                parsed_dob = parse_date(str(raw_val), field_name=field, date_locales=date_locales)
                if parsed_dob is not None:
                    age_years = (cfg.age_reference_date - parsed_dob.dt.date()).days // 365
                    if age_years > cfg.age_cap_threshold:
                        del row[field]
                        _bump("birthdate-age-cap-drop", field)
                        continue
            row[field] = _jitter_or_redact_date(
                raw_val, field=field, offset=offset, date_locales=date_locales, cfg=cfg, bump=_bump
            )
            continue

        # 3. REDUNDANT SUBJECT ID — value-conditional (Step 4). Identical to
        # the row's resolved subject id -> pure duplicate, drop (Class A).
        # Different -> a mis-collated CRF page or transcription error;
        # pseudonymise and flag rather than silently discard the discrepancy.
        rsid_label = cfg.redundant_subject_id_label_for(field)
        if rsid_label is not None:
            raw_val = row[field]
            if raw_val is None or not str(raw_val).strip():
                continue
            if str(raw_val).strip().casefold() == subj_id.strip().casefold():
                del row[field]
                _bump("redundant-subjid-drop", field)
            else:
                row[field] = pseudo_id(str(raw_val).strip(), key=key, label=rsid_label)
                _bump("subjid-mismatch-pseudonymize", field)
            continue

        # 4. DROP — field removed entirely from this row
        if cfg.field_is_drop(field):
            del row[field]
            _bump("drop", field)
            continue

        # 5. CAP — numeric > threshold collapsed to label
        cap_rule = cfg.cap_rule_for(field)
        if cap_rule is not None:
            raw_val = row[field]
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue
            new_val, was_capped = cap_numeric(
                raw_val, threshold=cap_rule.threshold, label=cap_rule.label
            )
            if was_capped:
                row[field] = new_val
                _bump("cap", field)
            continue

        # 6. GENERALIZE — value mapped to broader category
        gen_rule = cfg.generalize_rule_for(field)
        if gen_rule is not None:
            raw_val = row[field]
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue
            new_val, was_generalized = generalize_value(raw_val, mapping=gen_rule.mapping)
            if was_generalized:
                row[field] = new_val
                _bump("generalize", field)
            continue

        # 7. SUPPRESS_SMALL_CELL — numeric > threshold clamped to threshold
        if cfg.field_is_suppress_small_cell(field):
            raw_val = row[field]
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue
            new_val, was_suppressed = suppress_small_cell(
                raw_val, threshold=cfg.small_cell_threshold
            )
            if was_suppressed:
                row[field] = new_val
                _bump("suppress-small-cell", field)
            continue

        # 8. DATE — per-subject constant-offset jitter, fail-closed: a
        # value that neither parses nor is a declared sentinel is redacted
        # to null rather than published raw (Step 1).
        if cfg.field_is_date(field):
            raw_val = row[field]
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue
            row[field] = _jitter_or_redact_date(
                raw_val, field=field, offset=offset, date_locales=date_locales, cfg=cfg, bump=_bump
            )
            continue

        # 9. ID — HMAC-SHA256 pseudonymize with domain-separated label
        id_label = cfg.id_label_for(field)
        if id_label is not None:
            raw_val = row[field]
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue
            row[field] = pseudo_id(str(raw_val).strip(), key=key, label=id_label)
            _bump("id", field)

    row[_SCRUB_MARKER_FIELD] = _SCRUB_VERSION
    return row, counts


def _scrub_file(
    jsonl_path: Path,
    *,
    cfg: PHIScrubConfig,
    key: bytes,
    date_locales: dict[str, str] | None = None,
    age_variable_present: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Read *jsonl_path*, scrub each row, return (kept, orphans, counts)."""
    kept: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    # A dataset is subject-specific unless declared otherwise via
    # non_subject_datasets (Step 12b) — study-portable, not a hard-coded
    # study name.
    dataset_has_subject_col = cfg.dataset_has_subject_column(jsonl_path.name)

    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue

            # Idempotency guard: pre-scrubbed rows pass through unchanged.
            if row.get(_SCRUB_MARKER_FIELD) == _SCRUB_VERSION:
                kept.append(row)
                continue

            scrubbed, row_counts = _scrub_row(
                row,
                cfg=cfg,
                key=key,
                date_locales=date_locales,
                dataset_has_subject_col=dataset_has_subject_col,
                age_variable_present=age_variable_present,
            )
            if scrubbed is None:
                orphans.append(row)
            else:
                kept.append(scrubbed)
                for scope, n in row_counts.items():
                    counts[scope] = counts.get(scope, 0) + n

    return kept, orphans, counts


def _events_from_counts(
    counts_by_file: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    """Flatten per-file count dicts into a sorted list of audit events."""
    events: list[dict[str, Any]] = []
    for file_name in sorted(counts_by_file):
        for scope_field, count in sorted(counts_by_file[file_name].items()):
            scope, _, field = scope_field.partition(":")
            events.append(
                {
                    "scope": scope,
                    "field": field,
                    "file": file_name,
                    "count": count,
                }
            )
    return events


def _emit_audit(
    *,
    study_name: str,
    posture: str,
    events: list[dict[str, Any]],
    orphans: dict[str, int],
    audit_path: Path,
) -> None:
    """Write the single-leg scrub audit atomically under the output zone."""
    assert_output_zone(audit_path.parent)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "study": study_name,
        "generated_utc": _now_utc_iso(),
        "leg": "phi-scrub",
        "compliance_posture": posture,
        "scrubbed": events,
        "orphan_rows": orphans,
    }
    atomic_write_json(audit_path, payload)


_SCOPE_TO_ACTION: dict[str, str] = {
    "phi-scrub-drop": "drop",
    "phi-scrub-birthdate-drop": "birthdate_drop",
    "phi-scrub-id": "pseudonymize",
    "phi-scrub-date": "jitter_date",
    "phi-scrub-cap": "cap",
    "phi-scrub-generalize": "generalize",
    "phi-scrub-suppress-small-cell": "suppress_small_cell",
}


def _compute_input_dataset_hash(datasets_dir: Path) -> str:
    """Return a stable SHA-256 over a sorted manifest of *datasets_dir* contents.

    **What.** Hex SHA-256 of a UTF-8 manifest string.
    **Why.** Seals the exact byte-content of every raw input file into the
    audit ledger so drift detection can prove which ``llm_source/`` artifacts
    correspond to which raw input snapshot.
    **How.** Build one line per ``*.jsonl`` file under *datasets_dir*, sorted
    by relative path::

        <relpath>\\t<size_bytes>\\t<sha256_of_file_bytes>

    Concatenate, encode as UTF-8, SHA-256 the result.

    Only ``*.jsonl`` files are included; non-JSONL files (e.g. crash-recovery
    ``.tmp*`` artefacts written by :func:`atomic_write_jsonl`) are excluded so
    transient files do not affect reproducibility across runs.
    """
    lines: list[str] = []
    for fpath in sorted(datasets_dir.rglob("*.jsonl")):
        if not fpath.is_file():
            continue
        relpath = fpath.relative_to(datasets_dir).as_posix()
        size = fpath.stat().st_size
        try:
            file_hash = hash_file(fpath)
        except OSError as exc:
            raise PHIScrubError(f"input manifest unhashable: {fpath} — {exc}") from exc
        lines.append(f"{relpath}\t{size}\t{file_hash}")
    manifest = "\n".join(lines)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def _emit_as_written_ledger(
    *,
    events: list[dict[str, Any]],
    audit_path: Path,
    study_name: str | None,
    compliance_posture: str | None,
    dataset_files: list[str] | None = None,
    scrub_config_hash: str | None = None,
    input_dataset_hash: str | None = None,
) -> None:
    """Write one PHI as-written ledger under each dataset audit folder."""
    audit_dir = audit_path.parent
    assert_output_zone(audit_dir)
    ensure_no_llm_sentinel(audit_dir)
    remove_dataset_no_llm_sentinels(audit_dir)
    (audit_dir / PHI_LEDGER_FILENAME).unlink(missing_ok=True)

    display_names = {Path(name).stem: name for name in dataset_files or []}
    grouped_events: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        action = _SCOPE_TO_ACTION.get(event["scope"])
        if action is None:
            # phi-scrub-keep and any unrecognized scopes are not PHI handling actions
            continue
        dataset_file = event["file"]
        stem = Path(dataset_file).stem
        display_names.setdefault(stem, dataset_file)
        grouped_events.setdefault(stem, []).append(event)

    for stem in sorted(display_names):
        writer = LedgerWriter(
            output_path=dataset_phi_ledger_path(audit_dir, display_names[stem]),
            scrub_config_hash=scrub_config_hash,
            input_dataset_hash=input_dataset_hash,
            study=study_name,
            leg="phi-scrub",
            compliance_posture=compliance_posture,
            sentinel_dir=audit_dir,
        )
        for event in grouped_events.get(stem, []):
            writer.add_phi_event(
                form=Path(event["file"]).stem,
                variable_id=event["field"],
                action=_SCOPE_TO_ACTION[event["scope"]],
                rule_taxonomy=None,
                rule_project_category=None,
                rationale="Applied by PHI scrubber per phi_scrub.yaml configuration",
                dataset_file=event["file"],
                pdf_source=None,
                count=event["count"],
            )
        writer.flush()

# ── Step 5b/8/11 helpers ────────────────────────────────────────────────────


def _collect_staging_headers(staging_datasets: Path) -> set[str]:
    """Union of column names across every row of every staged dataset file.

    Reads only ``.keys()`` — never a value. Feeds :func:`validate_rule_catalog`
    (Step 10a) and the ``age_variable_present`` computation (Step 5b), both of
    which are column-name-only operations.

    Unions across *all* rows in a file, not just the first, because JSONL
    rows are not guaranteed to carry identical key sets (a column present
    only in a later, sparser row must still be validated).
    """
    headers: set[str] = set()
    for jsonl_file in sorted(staging_datasets.glob("*.jsonl")):
        with jsonl_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    headers.update(k for k in row if not k.startswith("__"))
    return headers


_TRANSFORMED_SCOPE_GROUPS: dict[str, str] = {
    "date": "date",
    "date-sentinel": "date",
    "date-unparsed-redacted": "date",
    "date-value-sentinel-redacted": "date",
    "id": "id",
    "subjid-mismatch-pseudonymize": "id",
}


def _build_transformed_manifest(
    counts_by_file: dict[str, dict[str, int]],
) -> dict[str, dict[str, list[str]]]:
    """Per-file sets of fields the scrubber actually TRANSFORMED a value
    for, grouped by action family (Step 8) — evidence the publish gate
    consults instead of trusting "classified by a date rule" as a proxy
    for "transformed". A field appears in the "date" group whether it was
    jittered, sentinel-preserved, or redacted-to-null — all three are
    fail-closed outcomes of the Step 1 date branch, never a raw
    passthrough."""
    manifest: dict[str, dict[str, set[str]]] = {}
    for file_name, scope_counts in counts_by_file.items():
        for scope_field in scope_counts:
            scope, _, field = scope_field.partition(":")
            scope = scope.removeprefix("phi-scrub-")
            group = _TRANSFORMED_SCOPE_GROUPS.get(scope)
            if group is None:
                continue
            manifest.setdefault(file_name, {}).setdefault(group, set()).add(field)
    return {
        file_name: {group: sorted(fields) for group, fields in groups.items()}
        for file_name, groups in manifest.items()
    }


def _class_for_action(action: str) -> str:
    """Map a resolved action string to its Disposition-Policy class letter."""
    if action in (_ACTION_DROP, "birthdate_drop"):
        return "A"
    if action in ("pseudonymize", "redundant_subject_id"):
        return "B"
    if action == "jitter_date":
        return "C"
    if action in (_ACTION_CAP, _ACTION_GENERALIZE, _ACTION_SUPPRESS):
        return "D"
    if action == _ACTION_KEEP:
        return "E"
    return "none"


def _build_disposition_manifest(
    cfg: PHIScrubConfig,
    headers: Iterable[str],
    counts_by_file: dict[str, dict[str, int]],
    *,
    age_variable_present: bool,
    sot_declarations: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Emit an explicit, per-column disposition record for every observed
    header (Step 11b) — the coverage argument for "no leak" on columns
    that match no scrub rule at all: an explicit ``none`` disposition plus
    the value-level publish-gate sweep (Steps 8/9), rather than silent
    trust. ``value_count`` sums the scrub-time event counts recorded for
    that field across every dataset file — names, rule text, and counts
    only, never a value (built from :func:`_scrub_file`'s already-computed
    counts; the data is never re-read)."""
    field_counts: dict[str, int] = {}
    field_rule: dict[str, str] = {}
    for scope_counts in counts_by_file.values():
        for scope_field, n in scope_counts.items():
            scope, _, field = scope_field.partition(":")
            scope = scope.removeprefix("phi-scrub-")
            field_counts[field] = field_counts.get(field, 0) + n
            field_rule.setdefault(field, scope)

    sot_declarations = sot_declarations or {}
    columns: dict[str, Any] = {}
    for name in sorted(set(headers)):
        action = resolve_action(cfg, name, age_variable_present=age_variable_present)
        declared = sot_declarations.get(name)
        columns[name] = {
            "resolved_action": action,
            "class": _class_for_action(action),
            "matched_rule": field_rule.get(name),
            "sot_declared": declared[0] if declared else None,
            "value_count": field_counts.get(name, 0),
        }
    return {
        "generated_utc": _now_utc_iso(),
        "age_variable_present": age_variable_present,
        "compliance_posture": cfg.compliance_posture,
        "columns": columns,
    }


def _build_review_queue(
    cfg: PHIScrubConfig,
    headers: Iterable[str],
    counts_by_file: dict[str, dict[str, int]],
    *,
    age_variable_present: bool,
    sot_disagreements: list[Any],
    baseline: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Build the Step 11c review queue from six deterministic triggers.

    A column already present in *baseline* with an unchanged resolved
    action is skipped (Step 11d) — steady-state review is empty; only new
    or changed columns surface.
    """
    baseline = baseline or {}
    entries: list[dict[str, Any]] = []

    def _is_new_or_changed(column: str, action: str) -> bool:
        return baseline.get(column) != action

    # keep_shadow — every acknowledged keep_overrides entry that actually
    # fired for an observed header. Per the plan's 11c table this trigger
    # is never destructive: the un-shadowed action itself (drop/date/id)
    # never applies here since keep already won — the entry is purely a
    # one-time confirmation surface for the operator, not a live conflict.
    # A genuinely ambiguous shadow (content unclear enough to need a
    # human decision before publish) is Class A un-shadowed to *drop*
    # directly in phi_scrub.yaml, which raises PHIRuleConflictError until
    # explicitly reconciled with a keep_overrides entry.
    for name in sorted(set(headers)):
        if not cfg.field_is_keep(name):
            continue
        shadowed = _shadowed_action_for(cfg, name, age_variable_present=age_variable_present)
        if shadowed is None:
            continue
        action = resolve_action(cfg, name, age_variable_present=age_variable_present)
        if not _is_new_or_changed(name, action):
            continue
        entries.append(
            {
                "column": name,
                "file": None,
                "trigger": "keep_shadow",
                "destructive": False,
                "count": None,
                "competing_rules": ["keep", shadowed],
                "resolution_options": ["add keep_overrides", "narrow the keep"],
                "status": "open",
            }
        )

    # date_unparsed — non-sentinel unparseable values redacted this run.
    for file_name, scope_counts in counts_by_file.items():
        for scope_field, n in scope_counts.items():
            scope, _, field = scope_field.partition(":")
            if scope.removeprefix("phi-scrub-") != "date-unparsed-redacted":
                continue
            entries.append(
                {
                    "column": field,
                    "file": file_name,
                    "trigger": "date_unparsed",
                    "destructive": True,
                    "count": n,
                    "competing_rules": ["date"],
                    "resolution_options": [
                        "declare date_locales",
                        "add date_sentinels",
                        "add date_unparsed_accept",
                    ],
                    "status": "open",
                }
            )

    # sot_disagreement
    for d in sot_disagreements:
        entries.append(
            {
                "column": d.column,
                "file": d.form,
                "trigger": "sot_disagreement",
                "destructive": d.resolved in (_ACTION_DROP, "birthdate_drop"),
                "count": None,
                "competing_rules": [f"sot:{d.declared}", f"scrubber:{d.resolved}"],
                "resolution_options": ["reconcile the SoT declaration", "add must_keep"],
                "status": "open",
            }
        )

    # subjid_mismatch — a page-level SUBJID<n> copy differed from the
    # canonical subject id this run.
    for file_name, scope_counts in counts_by_file.items():
        for scope_field, n in scope_counts.items():
            scope, _, field = scope_field.partition(":")
            if scope.removeprefix("phi-scrub-") != "subjid-mismatch-pseudonymize":
                continue
            entries.append(
                {
                    "column": field,
                    "file": file_name,
                    "trigger": "subjid_mismatch",
                    "destructive": False,
                    "count": n,
                    "competing_rules": ["redundant_subject_id"],
                    "resolution_options": ["investigate the mis-collated CRF page"],
                    "status": "open",
                }
            )

    # content_verification — a column explicitly listed in
    # content_verification_required (Step 11e) still resolves to drop this
    # run. Regenerated every run (no baseline dedup, matching date_unparsed)
    # so a plain unchanged config keeps requiring a live
    # phi_review_signoff.yaml entry; the trigger stops firing on its own
    # once the operator's real remediation (must_keep + keep_overrides)
    # changes the resolved action away from drop.
    header_set = set(headers)
    for name in cfg.content_verification_required:
        if name not in header_set:
            continue
        if resolve_action(cfg, name, age_variable_present=age_variable_present) != _ACTION_DROP:
            continue
        entries.append(
            {
                "column": name,
                "file": None,
                "trigger": "content_verification",
                "destructive": True,
                "count": None,
                "competing_rules": ["drop", "ambiguous-content"],
                "resolution_options": [
                    "confirm free text (drop stands)",
                    "confirm numeric/clinical content (add must_keep + keep_overrides shadows: drop)",
                ],
                "status": "open",
            }
        )

    return entries


def _load_json_if_present(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _check_review_pending(audit_dir: Path) -> None:
    """Step 11e — block this run when the previous run's review queue
    still has an unresolved ``destructive: true`` entry with no matching
    ``phi_review_signoff.yaml`` entry."""
    queue = _load_json_if_present(audit_dir / PHI_REVIEW_QUEUE_FILENAME)
    entries = queue.get("entries", []) if isinstance(queue, dict) else []
    destructive_open = [
        e for e in entries if isinstance(e, dict) and e.get("destructive") and e.get("status") == "open"
    ]
    if not destructive_open:
        return

    signoff_path = audit_dir / PHI_REVIEW_SIGNOFF_FILENAME
    signed: set[tuple[str, str | None]] = set()
    if signoff_path.is_file():
        with signoff_path.open("r", encoding="utf-8") as fh:
            signoff_raw = yaml.safe_load(fh) or []
        if isinstance(signoff_raw, list):
            for item in signoff_raw:
                if isinstance(item, dict) and item.get("column"):
                    signed.add((str(item["column"]), str(item.get("file")) if item.get("file") else None))

    still_pending = [
        e for e in destructive_open
        if (str(e.get("column")), str(e.get("file")) if e.get("file") else None) not in signed
    ]
    if still_pending:
        cols = ", ".join(f"{e.get('column')}@{e.get('file')}" for e in still_pending[:10])
        raise PHIReviewPendingError(
            f"{len(still_pending)} destructive PHI review entr"
            f"{'y' if len(still_pending) == 1 else 'ies'} unresolved from the "
            f"previous run: {cols}. Add matching entries to "
            f"{signoff_path.name} (column, file, decision, rationale, signed_by, "
            f"signed_utc) to proceed."
        )


def run_scrub(
    study_name: str | None = None,
    *,
    run_id: str | None = None,
    runs_dir: Path | None = None,
) -> None:
    """Orchestrate the scrub: load key + config, walk staging, emit audit.

    Pre-conditions:
        * ``tmp/{STUDY}/datasets/*.jsonl`` is populated by Step 1+3.
        * ``PHI_KEY_PATH`` exists and is mode 0600 — else hard-fail.
        * A ``phi_scrub.yaml`` config is present — else the module no-ops and
          writes an empty audit (so downstream audit tooling always finds a
          fourth file).

    Parameters
    ----------
    study_name:
        Override the study name used in the audit report.  Defaults to
        ``config.STUDY_NAME``.
    run_id:
        Optional run identifier.  When provided (together with *runs_dir*),
        a ``scrub.in_progress`` token is written to
        ``runs_dir/{run_id}/scrub.in_progress`` before the scrub loop and
        deleted only on successful completion.  If the process is killed
        mid-loop the token persists, allowing the wrapper CLI to detect the
        partially-scrubbed state and refuse with exit 6.

        **SIGKILL race window:** there is a small window between the
        ``atomic_write_json`` that creates ``scrub.in_progress`` and the
        first call to ``atomic_write_jsonl`` that mutates a dataset row.  A
        SIGKILL in that window leaves the token on disk with *zero* rows
        mutated.  On the next operator retry the wrapper sees the token and
        exits with code 6.

        This is **not** a data-loss condition — no data was written.  If
        ``scrub.in_progress`` is present and the output datasets are still in
        their original pre-scrub state (zero mutations), it is safe for the
        operator to delete the token file and restart the pipeline normally.
    runs_dir:
        Directory under which per-run sidecars are stored
        (e.g. ``output/{STUDY}/runs``).  Required when *run_id* is set.

    Post-conditions:
        * Datasets JSONL rewritten in place with scrubbed values + ``_phi_scrubbed``
          marker.
        * Orphan rows (missing subject_id) land under ``tmp/{STUDY}/quarantine/``.
        * Fourth audit report emitted at :data:`config.AUDIT_SCRUB_REPORT_PATH`.
        * Sentinel ``tmp/{STUDY}/.phi_scrub_complete`` marks the run.
    """
    if study_name is None:
        study_name = config.STUDY_NAME

    audit_path = Path(config.AUDIT_SCRUB_REPORT_PATH)
    staging_root = Path(config.STUDY_STAGING_DIR)
    sentinel = staging_root / _SENTINEL_NAME
    staging_datasets = Path(config.STAGING_DATASETS_DIR)

    cfg = load_scrub_config()
    if cfg is None:
        # Missing scrub config = no rule application = raw PHI flows to
        # ``llm_source/``. That is unsafe for any production run; require
        # an explicit opt-in env var to acknowledge the risk in dev/test.
        if config.production_mode_enabled():
            raise PHIScrubError("REPORTALIN_ALLOW_DISABLED_SCRUB is forbidden in production mode.")
        allow_disabled = os.environ.get("REPORTALIN_ALLOW_DISABLED_SCRUB", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not allow_disabled:
            raise PHIScrubError(
                "phi_scrub: config not found at "
                f"{config.PHI_SCRUB_CONFIG_PATH}. Refusing to publish a trio "
                "bundle without rule application — raw PHI would flow through "
                "unredacted. Either provision the YAML or set "
                "``REPORTALIN_ALLOW_DISABLED_SCRUB=1`` to acknowledge the risk "
                "(dev / test only)."
            )
        logger.warning(
            "phi_scrub: config not found at %s — running in DISABLED mode "
            "(REPORTALIN_ALLOW_DISABLED_SCRUB=1). Raw PHI may flow through.",
            config.PHI_SCRUB_CONFIG_PATH,
        )
        _emit_audit(
            study_name=study_name,
            posture="disabled",
            events=[],
            orphans={},
            audit_path=audit_path,
        )
        # No config file → cannot produce a config hash; hashes stay None.
        _emit_as_written_ledger(
            events=[],
            audit_path=audit_path,
            study_name=study_name,
            compliance_posture="disabled",
            dataset_files=sorted(p.name for p in staging_datasets.glob("*.jsonl"))
            if staging_datasets.is_dir()
            else [],
        )
        return

    # Config is present — seal its hash into every subsequent ledger write.
    scrub_config_hash: str = hash_file(Path(config.PHI_SCRUB_CONFIG_PATH))

    # Step 11e: a destructive review-queue entry left unresolved by the
    # previous run blocks this run until an operator signs off. Checked
    # before the sentinel short-circuit so a re-run attempt still surfaces
    # the block rather than silently no-op'ing.
    _check_review_pending(audit_path.parent)

    # Sentinel short-circuit — prevents accidental double-scrub on restart.
    if sentinel.is_file():
        logger.info(
            "phi_scrub: sentinel %s present — staging already scrubbed, skipping",
            sentinel,
        )
        return

    key = load_key()

    # Load per-column date locale overrides from the study's forms manifest.
    # Backward-compatible: returns {} when the manifest is absent or has no
    # date_locales section.  The manifest lives next to the *raw* datasets dir,
    # not the staging dir, so we read it from config.DATASETS_DIR.
    # Lazy import to avoid the circular:
    #   phi_scrub → dataset_pipeline → extraction.io → utils → security → phi_scrub
    from scripts.extraction.dataset_pipeline import check_forms_manifest

    # Reject-listed files are auto-skipped by the extraction leg, so the
    # scrub leg only needs the date_locales mapping here.
    date_locales: dict[str, str] = check_forms_manifest(config.DATASETS_DIR).date_locales

    if not staging_datasets.is_dir():
        logger.info(
            "phi_scrub: staging datasets dir missing (%s) — emitting empty audit",
            staging_datasets,
        )
        _emit_audit(
            study_name=study_name,
            posture=cfg.compliance_posture,
            events=[],
            orphans={},
            audit_path=audit_path,
        )
        # No input directory → cannot produce an input hash.
        _emit_as_written_ledger(
            events=[],
            audit_path=audit_path,
            study_name=study_name,
            compliance_posture=cfg.compliance_posture,
            dataset_files=[],
            scrub_config_hash=scrub_config_hash,
        )
        return

    # Step 5b/10a: column-name-only header union across staging, computed
    # once per study before any row is scrubbed — never reads a value.
    headers = _collect_staging_headers(staging_datasets)
    age_variable_present = any(cfg.cap_rule_for(n) is not None for n in headers)
    if not age_variable_present and cfg.age_reference_date is None:
        raise PHIScrubError(
            "This study has no age variable (no cap_fields pattern matches "
            "any staged column), so birthdate must be jittered instead of "
            "dropped to preserve age fidelity (Step 5b) — but "
            "age_reference_date is not set in phi_scrub.yaml. Add "
            "age_reference_date: \"YYYY-MM-DD\" (the study's data-cut date) "
            "before running."
        )
    validate_rule_catalog(cfg, headers, age_variable_present=age_variable_present)

    # Snapshot the raw input manifest BEFORE any in-place scrub rewrites so
    # the hash reflects the pre-scrub state, not the post-scrub state.
    dataset_files = sorted(p.name for p in staging_datasets.glob("*.jsonl"))
    input_dataset_hash: str = _compute_input_dataset_hash(staging_datasets)

    assert_write_zone(staging_datasets)

    # Write the in-progress token before any row mutation so a mid-loop crash
    # leaves the token on disk.  The wrapper CLI (P3.1) checks for this token
    # at startup and refuses with exit 6 if one is present.
    in_progress_token: Path | None = None
    if run_id is not None and runs_dir is not None:
        in_progress_token = runs_dir / run_id / "scrub.in_progress"
        in_progress_token.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            in_progress_token,
            {
                "run_id": run_id,
                "study": study_name if study_name is not None else config.STUDY_NAME,
                "started_utc": datetime.now(UTC).isoformat(),
                "scrub_yaml_sha256": scrub_config_hash,
            },
        )

    quarantine_dir = staging_root / "quarantine"
    counts_by_file: dict[str, dict[str, int]] = {}
    orphan_totals: dict[str, int] = {}

    for jsonl_file in sorted(staging_datasets.glob("*.jsonl")):
        kept, orphans, counts = _scrub_file(
            jsonl_file,
            cfg=cfg,
            key=key,
            date_locales=date_locales,
            age_variable_present=age_variable_present,
        )

        if orphans:
            orphan_totals[jsonl_file.name] = len(orphans)
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            assert_write_zone(quarantine_dir)
            orphan_counts: dict[str, int] = {}
            # partial-scrub before write: drop_fields + birthdate
            for _orphan in orphans:
                for scope_k, n in _apply_field_only_rules(_orphan, cfg=cfg).items():
                    orphan_counts[scope_k] = orphan_counts.get(scope_k, 0) + n
            if orphan_counts:
                q_key = f"quarantine/{jsonl_file.name}"
                counts_by_file[q_key] = orphan_counts
            atomic_write_jsonl(quarantine_dir / jsonl_file.name, orphans)
            if len(orphans) > cfg.orphan_quarantine_threshold:
                raise PHIQuarantineOverflowError(
                    f"{jsonl_file.name}: {len(orphans)} orphan rows exceeds "
                    f"threshold {cfg.orphan_quarantine_threshold}. "
                    f"Check subject_id_fields config."
                )

        atomic_write_jsonl(jsonl_file, kept)
        if counts:
            counts_by_file[jsonl_file.name] = counts
        logger.info(
            "phi_scrub %s: kept=%d orphaned=%d scopes=%d",
            jsonl_file.name,
            len(kept),
            len(orphans),
            len(counts),
        )

    # Step 1: fail-closed budget — any (file, column) with more
    # non-sentinel unparseable date values than date_unparsed_threshold,
    # and not explicitly accepted, hard-fails the run. Values are already
    # redacted to null in the written output regardless of this check —
    # it only decides whether the run may proceed silently or must stop.
    _unparsed_violations: list[str] = []
    for _file_name, _scope_counts in counts_by_file.items():
        for _scope_field, _n in _scope_counts.items():
            _scope, _, _field = _scope_field.partition(":")
            if _scope.removeprefix("phi-scrub-") != "date-unparsed-redacted":
                continue
            _accept_key = f"{_file_name}:{_field}"
            if _n > cfg.date_unparsed_threshold and _accept_key not in cfg.date_unparsed_accept:
                _unparsed_violations.append(f"{_accept_key}={_n}")

    events = _events_from_counts(counts_by_file)
    _emit_audit(
        study_name=study_name,
        posture=cfg.compliance_posture,
        events=events,
        orphans=orphan_totals,
        audit_path=audit_path,
    )
    _emit_as_written_ledger(
        events=events,
        audit_path=audit_path,
        study_name=study_name,
        compliance_posture=cfg.compliance_posture,
        dataset_files=dataset_files,
        scrub_config_hash=scrub_config_hash,
        input_dataset_hash=input_dataset_hash,
    )

    # Step 8: per-file transformed-field manifest — evidence the publish
    # gate consults instead of trusting rule membership as a
    # transformation proxy.
    assert_output_zone(audit_path.parent)
    transformed_manifest = _build_transformed_manifest(counts_by_file)
    atomic_write_json(audit_path.parent / PHI_TRANSFORMED_FILENAME, transformed_manifest)

    # Step 10b: cross-check SoT phi: declarations against the compiled
    # catalog. Lazy import — policy_crosscheck imports resolve_action from
    # this module, so a module-level import would be circular.
    from scripts.security.policy_crosscheck import collect_sot_declarations, crosscheck_sot_policy

    sot_root = Path(config.LLM_SOURCE_SOT_DIR)
    sot_disagreements = crosscheck_sot_policy(cfg, sot_root, age_variable_present=age_variable_present)
    sot_declarations = collect_sot_declarations(sot_root)

    # Step 11b: explicit disposition record for every observed column —
    # the coverage argument for "no leak" on columns matching no rule.
    disposition_manifest = _build_disposition_manifest(
        cfg,
        headers,
        counts_by_file,
        age_variable_present=age_variable_present,
        sot_declarations=sot_declarations,
    )
    atomic_write_json(audit_path.parent / PHI_DISPOSITION_FILENAME, disposition_manifest)

    # Step 11d: baseline — a column already present with an unchanged
    # resolved action never re-enters the review queue on a later run.
    baseline_path = audit_path.parent / PHI_DISPOSITION_BASELINE_FILENAME
    previous_baseline_raw = _load_json_if_present(baseline_path)
    previous_baseline: dict[str, str] = (
        previous_baseline_raw.get("columns", {}) if isinstance(previous_baseline_raw, dict) else {}
    )

    # Step 11c: review queue from the five deterministic triggers.
    review_entries = _build_review_queue(
        cfg,
        headers,
        counts_by_file,
        age_variable_present=age_variable_present,
        sot_disagreements=sot_disagreements,
        baseline=previous_baseline,
    )
    atomic_write_json(
        audit_path.parent / PHI_REVIEW_QUEUE_FILENAME,
        {"generated_utc": _now_utc_iso(), "study": study_name, "entries": review_entries},
    )

    new_baseline = {
        name: col["resolved_action"] for name, col in disposition_manifest["columns"].items()
    }
    atomic_write_json(
        baseline_path,
        {"generated_utc": _now_utc_iso(), "study": study_name, "columns": new_baseline},
    )
    # Raised here — after every audit/disposition/queue artifact is on disk —
    # rather than immediately after computing _unparsed_violations. Staging
    # rows are already marked _phi_scrubbed (idempotent) by this point, so a
    # bare rerun with no config change would re-scan zero rows and see zero
    # violations. Writing phi_review_queue.json's date_unparsed entries
    # first means Step 11e's _check_review_pending — called at the top of
    # the *next* invocation — finds the still-open destructive entry and
    # blocks with PHIReviewPendingError instead of silently publishing.
    if _unparsed_violations:
        raise PHIDateParseError(
            f"{len(_unparsed_violations)} date column(s) exceeded "
            f"date_unparsed_threshold={cfg.date_unparsed_threshold}: "
            f"{', '.join(sorted(_unparsed_violations))}. Declare the correct "
            f"date_locales, add a date_sentinels entry, or add an explicit "
            f"date_unparsed_accept opt-out."
        )

    with sentinel.open("w", encoding="utf-8") as _sf:
        _sf.write(_SCRUB_VERSION)
        _sf.flush()
        os.fsync(_sf.fileno())

    # Sentinel is written — scrub completed successfully.  Remove the
    # in-progress token so the wrapper does not see a false-positive on the
    # next invocation.  This must happen AFTER the sentinel write so that a
    # crash between the two leaves the token intact (safer direction: the
    # wrapper will still refuse, and the sentinel guarantees re-run is a no-op).
    if in_progress_token is not None:
        in_progress_token.unlink(missing_ok=True)


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cli_bootstrap_key(args: argparse.Namespace) -> int:
    target = Path(args.path) if args.path else Path(config.PHI_KEY_PATH)
    try:
        written = bootstrap_key(target)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"PHI HMAC key written to: {written}")
    print("File mode: 0600. This key is outside the repo tree and agent scope.")
    print("Rotating (deleting the key) will invalidate every previously-scrubbed")
    print("artifact — downstream consumers must re-ingest from raw.")
    return 0


def _cli_key_path(args: argparse.Namespace) -> int:
    print(config.PHI_KEY_PATH)
    return 0


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phi_scrub")
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser("bootstrap-key", help="Generate a new sidecar HMAC key")
    boot.add_argument("--path", type=str, default=None, help="Override key path")
    boot.set_defaults(func=_cli_bootstrap_key)

    path_cmd = sub.add_parser("key-path", help="Print the resolved key path")
    path_cmd.set_defaults(func=_cli_key_path)

    return parser


def _main(argv: Iterable[str] | None = None) -> int:
    parser = _build_cli()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
