"""Schema + validator for Study Variable Source of Truth records.

**What.** A single Source of Truth (SoT) record is the canonical per-variable
artifact that combines authorized evidence from the dataset header, the
form PDF, and (optionally) the dictionary, plus normalized metadata,
handling classification, and review state. This module defines the
contract that every SoT record must satisfy and provides a pure
``validate_record`` function that raises on any contract violation.

**Why.** The PRD makes the SoT the first-class internal artifact: catalog
cards, evidence packs, dataset schema sidecars, and audit ledgers are all
derived from it. If the contract is loose, every derivative artifact can
drift in its own direction. If the contract is tight, every derivative
artifact inherits the same correctness floor — no raw row values, no
footer/version-date noise, exact source wording preserved separately
from normalized labels, source-defined options separated from observed
values, and presence in each evidence source recorded explicitly.

**How.** A SoT record is a plain ``dict[str, Any]``. Validation walks the
top-level required keys, checks enum-valued fields against fixed sets,
verifies the per-source presence sub-records, and recursively scans the
whole record for forbidden keys (raw row values, artifact-version
metadata). The validator is pure — it does no I/O and returns ``None``
on success or raises ``SourceTruthValidationError`` on the first
contract violation.

The full record shape (all required unless noted)::

    {
        "variable_id":              str,         # stable internal id
        "source_kind":              str,         # one of SOURCE_KIND_VALUES
        "review_state":             str,         # one of REVIEW_STATE_VALUES
        "presence": {
            "dataset":      {"present": bool, ...},
            "pdf":          {"present": bool, ...},
            "dictionary":   {"present": bool, ...},
        },
        "exact_source_wording": {                # raw labels, never normalized
            "dataset_column": str | None,
            "pdf_question":   str | None,
            "pdf_options":    list[str] | None,
            "dictionary_label": str | None,
        },
        "normalized": {                          # normalized labels + concept
            "label": str,
            ...                                   # concept, options, role, ...
        },
    }

Forbidden anywhere in the record (raw row values)::

    observed_values, observed_value_counts, row_data, rows, dataset_rows,
    raw_values, sample_values

Forbidden anywhere in the record (artifact-version / footer metadata)::

    footer_text, version_date, print_timestamp, creation_date,
    export_timestamp, form_version_date, pdf_creation_date

Use::

    from scripts.source_truth.record import validate_record

    validate_record(record)   # raises SourceTruthValidationError on bad shape
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "FORBIDDEN_ARTIFACT_VERSION_KEYS",
    "FORBIDDEN_RAW_VALUE_KEYS",
    "PRESENCE_SOURCES",
    "REQUIRED_TOP_LEVEL_KEYS",
    "REVIEW_STATE_VALUES",
    "SOURCE_KIND_VALUES",
    "SourceTruthValidationError",
    "validate_record",
]


class SourceTruthValidationError(ValueError):
    """Raised when a Source of Truth record violates its schema contract."""


# Allowed values for ``source_kind`` — see PRD §"Implementation Decisions"
# (Record source-only, dataset-only, matched, context-only, and
# review-required items explicitly).
SOURCE_KIND_VALUES: frozenset[str] = frozenset(
    {
        "matched",
        "dataset_only",
        "source_only",
        "context_only",
        "review_required",
    }
)

# Allowed values for ``review_state``. ``review_required`` aligns with the
# PRD requirement that review-required fields record exact uncertainty.
REVIEW_STATE_VALUES: frozenset[str] = frozenset(
    {
        "auto_normalized",
        "review_required",
        "reviewed",
    }
)

# The three independent presence sources. The PRD requires that dataset,
# PDF, and dictionary presence be recorded *separately* so provenance is
# explicit even when a variable is missing from one source.
PRESENCE_SOURCES: frozenset[str] = frozenset({"dataset", "pdf", "dictionary"})

# Top-level keys every SoT record must carry.
REQUIRED_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "variable_id",
        "source_kind",
        "review_state",
        "presence",
        "exact_source_wording",
        "normalized",
    }
)

# Keys that, if present anywhere in the record, indicate raw dataset row
# values have leaked into the SoT layer. The SoT is metadata-only and must
# not carry observed values — those belong in the dataset interface, not
# the source of truth.
FORBIDDEN_RAW_VALUE_KEYS: frozenset[str] = frozenset(
    {
        "observed_values",
        "observed_value_counts",
        "row_data",
        "rows",
        "dataset_rows",
        "raw_values",
        "sample_values",
    }
)

# Keys that, if present anywhere in the record, indicate footer or
# artifact-version metadata has leaked into the SoT. The PRD is explicit:
# footers, form version dates, PDF creation dates, and print/export
# timestamps must be excluded.
FORBIDDEN_ARTIFACT_VERSION_KEYS: frozenset[str] = frozenset(
    {
        "footer_text",
        "version_date",
        "print_timestamp",
        "creation_date",
        "export_timestamp",
        "form_version_date",
        "pdf_creation_date",
    }
)


def _walk_keys(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Yield ``(path, key)`` pairs for every key in nested mappings/lists.

    Used by ``validate_record`` to scan the whole record for forbidden
    keys regardless of nesting depth.
    """
    found: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            found.append((child_path, str(key)))
            found.extend(_walk_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            found.extend(_walk_keys(child, child_path))
    return found


def _check_presence_block(presence: Any) -> None:
    if not isinstance(presence, Mapping):
        raise SourceTruthValidationError(
            "presence must be a mapping with 'dataset', 'pdf', and 'dictionary' sub-records"
        )
    missing = PRESENCE_SOURCES - presence.keys()
    if missing:
        raise SourceTruthValidationError(
            "presence missing required source(s): " + ", ".join(sorted(missing))
        )
    for source in PRESENCE_SOURCES:
        sub = presence[source]
        if not isinstance(sub, Mapping):
            raise SourceTruthValidationError(
                f"presence.{source} must be a mapping with a 'present' boolean"
            )
        if "present" not in sub:
            raise SourceTruthValidationError(
                f"presence.{source} is missing required 'present' boolean"
            )
        if not isinstance(sub["present"], bool):
            raise SourceTruthValidationError(
                f"presence.{source}.present must be a boolean"
            )


def _check_exact_source_wording(wording: Any) -> None:
    if not isinstance(wording, Mapping):
        raise SourceTruthValidationError(
            "exact_source_wording must be a mapping that preserves raw labels"
        )
    # Source-defined options live in ``exact_source_wording.pdf_options``
    # and must never co-exist with observed dataset values in the same
    # block — the SoT layer keeps source-defined options strictly
    # separate from observed values.
    if "observed_values" in wording or "observed_value_counts" in wording:
        raise SourceTruthValidationError(
            "exact_source_wording must not carry observed_values; "
            "observed values belong in the dataset interface, not the source of truth"
        )


def _check_normalized(normalized: Any) -> None:
    if not isinstance(normalized, Mapping):
        raise SourceTruthValidationError(
            "normalized must be a mapping with at least a 'label'"
        )
    if "label" not in normalized or not isinstance(normalized["label"], str):
        raise SourceTruthValidationError(
            "normalized.label is required and must be a string"
        )
    # Source-defined options may live in ``normalized.source_defined_options``,
    # but the PRD requires that source-defined options be kept separate
    # from observed dataset values. Reject any record that pairs the two
    # in the same block.
    if (
        "source_defined_options" in normalized
        and (
            "observed_values" in normalized
            or "observed_value_counts" in normalized
        )
    ):
        raise SourceTruthValidationError(
            "normalized.source_defined_options must not co-exist with observed_values; "
            "source-defined options describe the form, not the dataset"
        )


def validate_record(record: Mapping[str, Any]) -> None:
    """Validate a single Source of Truth record.

    Args:
        record: The candidate record. Must be a mapping.

    Raises:
        SourceTruthValidationError: On the first contract violation found.
            The message identifies the rule and, where useful, the offending
            path inside the record.
    """
    if not isinstance(record, Mapping):
        raise SourceTruthValidationError("Source of Truth record must be a mapping")

    missing = REQUIRED_TOP_LEVEL_KEYS - record.keys()
    if missing:
        raise SourceTruthValidationError(
            "record missing required top-level keys: " + ", ".join(sorted(missing))
        )

    variable_id = record["variable_id"]
    if not isinstance(variable_id, str) or not variable_id.strip():
        raise SourceTruthValidationError("variable_id must be a non-empty string")

    source_kind = record["source_kind"]
    if source_kind not in SOURCE_KIND_VALUES:
        raise SourceTruthValidationError(
            f"source_kind {source_kind!r} is not one of "
            + ", ".join(sorted(SOURCE_KIND_VALUES))
        )

    review_state = record["review_state"]
    if review_state not in REVIEW_STATE_VALUES:
        raise SourceTruthValidationError(
            f"review_state {review_state!r} is not one of "
            + ", ".join(sorted(REVIEW_STATE_VALUES))
        )

    _check_presence_block(record["presence"])
    _check_exact_source_wording(record["exact_source_wording"])
    _check_normalized(record["normalized"])

    # Recursive forbidden-key scan. Catches raw row values and artifact-
    # version / footer metadata regardless of how deeply they were nested
    # by an upstream extractor.
    for path, key in _walk_keys(record):
        if key in FORBIDDEN_RAW_VALUE_KEYS:
            raise SourceTruthValidationError(
                f"raw dataset row values are forbidden in the source of truth; "
                f"found key {key!r} at {path}"
            )
        if key in FORBIDDEN_ARTIFACT_VERSION_KEYS:
            raise SourceTruthValidationError(
                f"footer / artifact-version metadata is forbidden in the source of truth; "
                f"found key {key!r} at {path}"
            )
