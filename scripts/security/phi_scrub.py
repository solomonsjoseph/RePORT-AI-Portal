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
   ``"SUBJ_" + hmac_sha256(key, raw_id).hexdigest()[:12]``. Deterministic
   cross-file linkage preserved; non-reversible without key possession.

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

Ordering in the pipeline
------------------------
Runs as Step 1.6 — AFTER Step 1+3 (raw extraction) and BEFORE Step 1.7
(dataset cleanup). This keeps ``dataset_cleanup_report.json`` free of raw
subject IDs and raw dates, so the dataset-leg audit never contains PHI.

Key management
--------------
The HMAC key is a sidecar file at
``$XDG_CONFIG_HOME/report_ai_portal/phi_key`` (default ``~/.config/report_ai_portal/phi_key``).
Mode must be ``0600``. Missing key = hard-fail for developer/operator CLI
pipeline runs. Normal users create it through the web UI's Load Study flow.
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
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

import config
from scripts.audit.ledger import LedgerWriter
from scripts.extraction.io import atomic_write_json, atomic_write_jsonl, parse_date
from scripts.security.secure_env import assert_output_zone, assert_write_zone
from scripts.utils.integrity import hash_file

logger = logging.getLogger(__name__)

__all__ = [
    "CapRule",
    "GeneralizeRule",
    "IdRule",
    "PHIKeyMissingError",
    "PHIKeyPermissionError",
    "PHIQuarantineOverflowError",
    "PHIScrubConfig",
    "PHIScrubError",
    "bootstrap_key",
    "cap_numeric",
    "date_offset_days",
    "generalize_value",
    "load_key",
    "load_scrub_config",
    "pseudo_id",
    "run_scrub",
    "shift_date",
    "suppress_small_cell",
]

# ── Constants ────────────────────────────────────────────────────────────────

# Row-level ``_phi_scrubbed`` marker. Acts as an idempotency guard so a
# second scrub pass over the same staging file is a no-op. The full
# catalog version + rule counts live in the audit report, not the row.
_SCRUB_VERSION = "v2"
# v2: ID pseudonyms carry the semantic category as both a visible prefix AND
# cryptographic domain separator. Format: ``<LABEL>_<hmac12hex>`` where the
# HMAC input is ``f"{label}:{raw_value}"``. Same raw value under different
# labels → different pseudonyms (prevents cross-category correlation), same
# raw value under the same label → same pseudonym (preserves in-category
# longitudinal linkage across files). See :func:`pseudo_id`. Bumping the
# marker forces re-scrub of any row written under the flat v1 ``SUBJ_``
# scheme.
_SCRUB_MARKER_FIELD = "_phi_scrubbed"
_SENTINEL_NAME = ".phi_scrub_complete"

_DEFAULT_MAX_JITTER_DAYS = 30
_DEFAULT_ORPHAN_THRESHOLD = 10
_DEFAULT_AGE_CAP_THRESHOLD = 89
_DEFAULT_AGE_CAP_LABEL = "90+"
_DEFAULT_SMALL_CELL_THRESHOLD = 5
_PSEUDO_TAG_BYTES = 12  # hex chars taken from HMAC digest
_OFFSET_DIGEST_BYTES = 4  # first N bytes of digest for offset computation

_POSTURE_SAFE_HARBOR = "safe_harbor"
_POSTURE_LIMITED_DATASET = "limited_dataset"
_VALID_POSTURES = frozenset({_POSTURE_SAFE_HARBOR, _POSTURE_LIMITED_DATASET})

_KEY_FILE_MODE = 0o600
_KEY_HEX_LEN = 64  # 32 bytes = 64 hex chars

_LIMITED_DATASET_AUTHORITY = "authorities/phi_limited_dataset.md"

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
    both as the visible output prefix (``<LABEL>_<hmac12>``) AND as the
    HMAC domain-separator, so the same raw value under two different labels
    yields two different pseudonyms.

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


class PHIScrubConfig:
    """Parsed + compiled scrub configuration.

    Regex patterns from YAML are compiled once at load time; config is a
    throwaway struct (not persisted beyond the pipeline run).

    Rule priority (first match wins within :func:`_scrub_row`):
        1. ``keep_patterns`` — allowlist, short-circuits every other rule
        2. ``birthdate_pattern`` — posture-dependent drop or jitter
        3. ``drop_patterns`` — field removed from row
        4. ``cap_rules`` — numeric capped to label
        5. ``generalize_rules`` — value mapped to broad category
        6. ``suppress_small_cell_patterns`` — numeric clamped to threshold
        7. ``date_patterns`` — jitter via SANT
        8. ``id_patterns`` — HMAC-SHA256 pseudonymize
    """

    __slots__ = (
        "age_cap_label",
        "age_cap_threshold",
        "birthdate_pattern",
        "cap_rules",
        "compliance_posture",
        "date_patterns",
        "drop_patterns",
        "generalize_rules",
        "id_patterns",
        "keep_patterns",
        "max_jitter_days",
        "orphan_quarantine_threshold",
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
        :meth:`field_is_birthdate` so Safe Harbor drops can be distinguished
        from jitter events.
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


def load_scrub_config(path: Path | None = None) -> PHIScrubConfig | None:
    """Load + compile the scrub config. Returns ``None`` if file is absent.

    An absent config is NOT an error — it means phi_scrub is a no-op for this
    study, and the pipeline continues. This lets users opt in per-study by
    dropping a YAML file in place.

    When ``compliance_posture: limited_dataset`` is set, the function also
    verifies the authority note exists at :data:`_LIMITED_DATASET_AUTHORITY`.

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
        authority = Path(config.BASE_DIR) / _LIMITED_DATASET_AUTHORITY
        if not authority.is_file():
            raise PHIScrubError(
                f"compliance_posture is 'limited_dataset' but the required "
                f"authority note is missing: {authority}. Create it to document "
                f"IRB approval + Data Use Agreement before running."
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
    # whole point of the v2 scheme.
    raw_id_rules = raw.get("id_fields") or []
    if not isinstance(raw_id_rules, list):
        raise PHIScrubError("id_fields must be a list of {pattern, label} mappings")
    id_patterns: list[IdRule] = []
    for idx, entry in enumerate(raw_id_rules):
        if not isinstance(entry, dict):
            raise PHIScrubError(
                f"id_fields[{idx}] must be a mapping with 'pattern' + 'label'; "
                f"plain strings are no longer accepted in v2"
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
            f"PHI HMAC key not found at {path}. Use the web UI Load Study flow, "
            "or ask a developer/operator to provision the sidecar PHI key."
        )

    mode = path.stat().st_mode & 0o777
    if mode != _KEY_FILE_MODE:
        raise PHIKeyPermissionError(
            f"PHI key file {path} has mode {oct(mode)}; must be {oct(_KEY_FILE_MODE)}. "
            f"Fix with: chmod 600 {path}"
        )

    text = path.read_text(encoding="utf-8").strip()
    if len(text) != _KEY_HEX_LEN:
        raise PHIScrubError(
            f"PHI key at {path} must be {_KEY_HEX_LEN} hex chars (32 bytes); got {len(text)}"
        )
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise PHIScrubError(f"PHI key at {path} is not valid hex: {exc}") from exc


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
    """Return ``<LABEL>_<hmac12hex>`` with cryptographic domain separation.

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
            Propagated both into the HMAC input (domain separation) and as
            the visible output prefix (debuggability + IRB-audit clarity).

    Returns:
        ``f"{label}_{hex12}"`` — the visible prefix mirrors the label so
        the output is self-describing in audit logs and downstream tools.
    """
    domain_input = f"{label}:{raw_id}".encode()
    tag = hmac.new(key, domain_input, hashlib.sha256).hexdigest()[:_PSEUDO_TAG_BYTES]
    return f"{label}_{tag}"


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
) -> str | None:
    """Parse *value*, shift by ``offset_days``, re-emit in the same format.

    Returns ``None`` if the string does not parse as a date. Non-string
    inputs must be handled by the caller.
    """
    parsed = parse_date(value, field_name=field_name)
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


def _resolve_subject_id(row: dict[str, Any], candidates: tuple[str, ...]) -> str:
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
    return ""


def _now_utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scrub_row(
    row: dict[str, Any],
    *,
    cfg: PHIScrubConfig,
    key: bytes,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    """Scrub a single row. Return (scrubbed_row_or_None, per-field-counts).

    Priority (first match wins per field):
        1. keep_patterns       — allowlist, short-circuits every other rule
        2. birthdate_pattern   — posture-dependent drop or jitter
        3. drop_patterns       — field removed from row entirely
        4. cap_rules           — numeric > threshold → label
        5. generalize_rules    — value looked up in mapping
        6. suppress_small_cell — numeric > threshold → threshold
        7. date_patterns       — jitter via SANT per-subject offset
        8. id_patterns         — HMAC-SHA256 pseudonymize

    Returns ``None`` for the row when no resolvable subject_id — caller
    quarantines. Per-field counts are keyed by scope label
    (``phi-scrub-drop:FIELD``, ``phi-scrub-cap:FIELD`` etc.).
    """
    subj_id = _resolve_subject_id(row, cfg.subject_id_fields)
    if not subj_id:
        return None, {}

    offset = date_offset_days(subj_id, key=key, max_days=cfg.max_jitter_days)
    counts: dict[str, int] = {}

    def _bump(scope: str, field: str) -> None:
        k = f"phi-scrub-{scope}:{field}"
        counts[k] = counts.get(k, 0) + 1

    # Iterate a snapshot of keys so we can mutate row in place.
    for field in list(row.keys()):
        # Skip pipeline-internal metadata
        if field.startswith("__"):
            continue

        # 1. KEEP — allowlist short-circuits every other rule
        if cfg.field_is_keep(field):
            continue

        # 2. BIRTHDATE — posture-dependent drop or jitter.
        # Safe Harbor drops; Limited Dataset falls through to rule 7 (date jitter).
        if cfg.field_is_birthdate(field) and cfg.compliance_posture == _POSTURE_SAFE_HARBOR:
            del row[field]
            _bump("birthdate-drop", field)
            continue

        # 3. DROP — field removed entirely from this row
        if cfg.field_is_drop(field):
            del row[field]
            _bump("drop", field)
            continue

        # 4. CAP — numeric > threshold collapsed to label
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

        # 5. GENERALIZE — value mapped to broader category
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

        # 6. SUPPRESS_SMALL_CELL — numeric > threshold clamped to threshold
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

        # 7. DATE — per-subject constant-offset jitter (includes birthdate
        # when posture = limited_dataset)
        if cfg.field_is_date(field) or (
            cfg.field_is_birthdate(field) and cfg.compliance_posture == _POSTURE_LIMITED_DATASET
        ):
            raw_val = row[field]
            if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
                continue
            shifted = shift_date(str(raw_val), offset, field_name=field)
            if shifted is not None:
                row[field] = shifted
                _bump("date", field)
            continue

        # 8. ID — HMAC-SHA256 pseudonymize with domain-separated label
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Read *jsonl_path*, scrub each row, return (kept, orphans, counts)."""
    kept: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

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

            scrubbed, row_counts = _scrub_row(row, cfg=cfg, key=key)
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
    """
    lines: list[str] = []
    for fpath in sorted(datasets_dir.rglob("*")):
        if not fpath.is_file():
            continue
        relpath = fpath.relative_to(datasets_dir).as_posix()
        size = fpath.stat().st_size
        file_hash = hash_file(fpath)
        lines.append(f"{relpath}\t{size}\t{file_hash}")
    manifest = "\n".join(lines)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def _emit_as_written_ledger(
    *,
    events: list[dict[str, Any]],
    audit_path: Path,
    scrub_config_hash: str | None = None,
    input_dataset_hash: str | None = None,
) -> None:
    """Write phi_handling_ledger.as_written.json alongside the legacy audit report."""
    assert_output_zone(audit_path.parent)
    ledger_path = audit_path.parent / "phi_handling_ledger.as_written.json"
    writer = LedgerWriter(
        output_path=ledger_path,
        scrub_config_hash=scrub_config_hash,
        input_dataset_hash=input_dataset_hash,
    )
    for event in events:
        action = _SCOPE_TO_ACTION.get(event["scope"])
        if action is None:
            # phi-scrub-keep and any unrecognized scopes are not PHI handling actions
            continue
        writer.add_phi_event(
            form=Path(event["file"]).stem,
            variable_id=event["field"],
            action=action,
            rule_taxonomy=None,
            rule_project_category=None,
            rationale="Applied by PHI scrubber per phi_scrub.yaml configuration",
            dataset_file=event["file"],
            pdf_source=None,
            count=event["count"],
        )
    writer.flush()


def run_scrub(study_name: str | None = None) -> None:
    """Orchestrate the scrub: load key + config, walk staging, emit audit.

    Pre-conditions:
        * ``tmp/{STUDY}/datasets/*.jsonl`` is populated by Step 1+3.
        * ``PHI_KEY_PATH`` exists and is mode 0600 — else hard-fail.
        * A ``phi_scrub.yaml`` config is present — else the module no-ops and
          writes an empty audit (so downstream audit tooling always finds a
          fourth file).

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

    cfg = load_scrub_config()
    if cfg is None:
        # Missing scrub config = no rule application = raw PHI flows to
        # ``llm_source/``. That is unsafe for any production run; require
        # an explicit opt-in env var to acknowledge the risk in dev/test.
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
        _emit_as_written_ledger(events=[], audit_path=audit_path)
        return

    # Config is present — seal its hash into every subsequent ledger write.
    scrub_config_hash: str = hash_file(Path(config.PHI_SCRUB_CONFIG_PATH))

    # Sentinel short-circuit — prevents accidental double-scrub on restart.
    if sentinel.is_file():
        logger.info(
            "phi_scrub: sentinel %s present — staging already scrubbed, skipping",
            sentinel,
        )
        return

    key = load_key()

    staging_datasets = Path(config.STAGING_DATASETS_DIR)
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
            scrub_config_hash=scrub_config_hash,
        )
        return

    # Snapshot the raw input manifest BEFORE any in-place scrub rewrites so
    # the hash reflects the pre-scrub state, not the post-scrub state.
    input_dataset_hash: str = _compute_input_dataset_hash(staging_datasets)

    assert_write_zone(staging_datasets)

    quarantine_dir = staging_root / "quarantine"
    counts_by_file: dict[str, dict[str, int]] = {}
    orphan_totals: dict[str, int] = {}

    for jsonl_file in sorted(staging_datasets.glob("*.jsonl")):
        kept, orphans, counts = _scrub_file(jsonl_file, cfg=cfg, key=key)

        if orphans:
            orphan_totals[jsonl_file.name] = len(orphans)
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            assert_write_zone(quarantine_dir)
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
        scrub_config_hash=scrub_config_hash,
        input_dataset_hash=input_dataset_hash,
    )

    with sentinel.open("w", encoding="utf-8") as _sf:
        _sf.write(_SCRUB_VERSION)
        _sf.flush()
        os.fsync(_sf.fileno())


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
