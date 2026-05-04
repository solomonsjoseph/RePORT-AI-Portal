"""Source Truth Builder — reconciles authorized evidence into SoT records.

**What.** ``build_records`` takes three pre-extracted, authorized inputs:

* ``column_inventory`` — dataset column-name metadata only, never raw
  rows (produced by the upstream column-inventory tool).
* ``pdf_extraction``  — PDF annotations, sections, and visible
  option-set definitions (produced by the upstream PDF extractor).
* ``field_policy``    — the per-variable policy draft (handling action,
  PDF section, option-set reference, confidence, review state).

It returns a list of Source of Truth records, one per dataset column,
plus one per PDF/dictionary-only field if the inputs ever surface them
(6_HIV does not — see issue #67 for the source-only stress fixture).

**Why.** Issue #66 needs the first end-to-end SoT tracer bullet. The
PRD makes the SoT first-class because it is the only layer permitted to
combine all interpretation work; downstream catalog cards, evidence
packs, dataset-schema sidecars, and audit ledgers must derive from it.
This module is intentionally a *builder over already-extracted inputs*
— it does not open ``.xlsx`` files or parse PDFs itself, so the
"no raw row values" boundary is enforced at the input contract.

**How.** Each record is built to satisfy
``scripts.source_truth.record.validate_record`` and additionally
carries:

* ``derivation_targets`` — which downstream artifact each record feeds
  into (catalog, dataset_schema, phi_handling_ledger,
  dataset_cleanup_ledger). Mapped from the field-policy ``action``.
* ``normalized.confidence``         — taken from the field-policy entry.
* ``normalized.normalization_basis``— provenance string explaining how
  the normalized label was derived.
* ``normalized.handling_action``    — the field-policy action verbatim.
* ``normalized.section``            — PDF section assignment.
* ``normalized.option_set``         — option-set name when one applies.

The downstream-target mapping (kept narrow to the 6_HIV slice) follows
the PRD's separation of PHI handling from non-PHI cleanup:

============================  ==================================================
field_policy ``action``       derivation_targets
============================  ==================================================
``keep``                      ``[catalog, dataset_schema]``
``pseudonymize``              ``[catalog, dataset_schema, phi_handling_ledger]``
``jitter_date``               ``[catalog, dataset_schema, phi_handling_ledger]``
``generalize``                ``[catalog, dataset_schema, phi_handling_ledger]``
``drop`` + PHI/sensitive      ``[phi_handling_ledger]``
``drop`` + non-PHI cleanup    ``[dataset_cleanup_ledger]``
``review_required``           ``[]`` (review_state="review_required")
============================  ==================================================

Use::

    from scripts.source_truth.builder import build_records

    records = build_records(column_inventory, pdf_extraction, field_policy)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.source_truth.completeness import report_completeness
from scripts.source_truth.record import (
    FORBIDDEN_ARTIFACT_VERSION_KEYS,
    FORBIDDEN_RAW_VALUE_KEYS,
    validate_record,
)

__all__ = [
    "DERIVATION_CATALOG",
    "DERIVATION_CLEANUP_LEDGER",
    "DERIVATION_DATASET_SCHEMA",
    "DERIVATION_PHI_LEDGER",
    "PHI_DROP_REASONS",
    "SourceTruthBuildError",
    "build_records",
    "build_source_truth_artifact",
]


class SourceTruthBuildError(ValueError):
    """Raised when builder inputs cannot be reconciled into SoT records."""


DERIVATION_CATALOG = "catalog"
DERIVATION_DATASET_SCHEMA = "dataset_schema"
DERIVATION_PHI_LEDGER = "phi_handling_ledger"
DERIVATION_CLEANUP_LEDGER = "dataset_cleanup_ledger"

# field_policy ``reason`` values that classify a ``drop`` action as a
# PHI-handling decision rather than a non-PHI cleanup decision. Anything
# not in this set falls through to the cleanup ledger.
PHI_DROP_REASONS: frozenset[str] = frozenset(
    {
        "signature_field",
        "initials_field",
        "participant_identifier",
        "facility_clinic_ictc_or_site_identifier",
        "direct_identifier",
        "name_field",
        "address_field",
        "contact_field",
    }
)

# Actions that always feed the PHI handling ledger when the variable is
# *retained* (kept in the dataset with safe handling applied).
_RETAINED_PHI_ACTIONS: frozenset[str] = frozenset({"pseudonymize", "jitter_date", "generalize"})

_FORBIDDEN_INPUT_KEYS = FORBIDDEN_RAW_VALUE_KEYS | FORBIDDEN_ARTIFACT_VERSION_KEYS


def _forbidden_input_key_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key) in _FORBIDDEN_INPUT_KEYS:
                found.append(child_path)
            found.extend(_forbidden_input_key_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_input_key_paths(child, f"{path}[{index}]"))
    return found


def _reject_forbidden_input_keys(name: str, value: Mapping[str, Any]) -> None:
    paths = _forbidden_input_key_paths(value)
    if paths:
        raise SourceTruthBuildError(
            f"{name} contains source-truth-forbidden key(s): " + ", ".join(sorted(paths))
        )


def _derivation_targets_for_action(
    action: str, reason: str | None, *, dataset_present: bool
) -> list[str]:
    """Map a field-policy action to its downstream derivation targets."""
    if action == "keep":
        return [DERIVATION_CATALOG, *([DERIVATION_DATASET_SCHEMA] if dataset_present else [])]
    if action in _RETAINED_PHI_ACTIONS:
        targets = [DERIVATION_CATALOG, DERIVATION_PHI_LEDGER]
        if dataset_present:
            targets.insert(1, DERIVATION_DATASET_SCHEMA)
        return targets
    if action == "drop":
        if reason and reason in PHI_DROP_REASONS:
            return [DERIVATION_PHI_LEDGER]
        return [DERIVATION_CLEANUP_LEDGER]
    if action == "review_required":
        return []
    raise SourceTruthBuildError(
        f"unknown field_policy action {action!r}; cannot resolve derivation targets"
    )


def _review_state_for_action(action: str) -> str:
    """Map a field-policy action to a record-level review_state."""
    if action == "review_required":
        return "review_required"
    return "auto_normalized"


def _source_kind(dataset_present: bool, pdf_present: bool, action: str) -> str:
    """Classify how a variable's evidence reconciles across sources."""
    if action == "review_required":
        return "review_required"
    if dataset_present and pdf_present:
        return "matched"
    if dataset_present and not pdf_present:
        return "dataset_only"
    if not dataset_present and pdf_present:
        return "source_only"
    return "context_only"


def _normalize_label(variable: str, field_label: Any) -> str:
    """Best-effort normalized label for a dataset column code.

    The 6_HIV pilot ships safe labels for handled identifier fields and
    raw column codes for the rest. The builder uses the safe label when
    present, otherwise a deterministic lowercase column-code fallback.
    The original column code is always preserved in
    ``exact_source_wording.dataset_column``.
    """
    if isinstance(field_label, str) and field_label.strip():
        return field_label.strip().lower()
    return variable.strip().lower()


def _normalization_basis(field_label: Any) -> str:
    if isinstance(field_label, str) and field_label.strip():
        return "field_policy_label"
    return "dataset_column_code_lowercased"


def _column_inventory_columns(column_inventory: Mapping[str, Any]) -> list[str]:
    sheets = column_inventory.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise SourceTruthBuildError("column_inventory.sheets must be a non-empty list")
    columns: list[str] = []
    seen: set[str] = set()
    for sheet in sheets:
        if not isinstance(sheet, Mapping):
            raise SourceTruthBuildError("column_inventory.sheets entries must be mappings")
        for column in sheet.get("columns", []):
            if not isinstance(column, str):
                raise SourceTruthBuildError("column_inventory column names must be strings")
            if column in seen:
                raise SourceTruthBuildError(
                    f"column_inventory contains duplicate column {column!r}"
                )
            seen.add(column)
            columns.append(column)
    return columns


def _option_set_values(
    pdf_extraction: Mapping[str, Any], option_set_name: str | None
) -> list[str] | None:
    if not option_set_name:
        return None
    option_sets = pdf_extraction.get("option_sets") or {}
    if not isinstance(option_sets, Mapping):
        return None
    entry = option_sets.get(option_set_name)
    if not isinstance(entry, Mapping):
        return None
    values = entry.get("values")
    if isinstance(values, list) and all(isinstance(v, str) for v in values):
        return list(values)
    return None


def _annotation_pages_for_column(pdf_extraction: Mapping[str, Any], column: str) -> list[int]:
    pages: list[int] = []
    for page_entry in pdf_extraction.get("annotation_pages", []):
        if not isinstance(page_entry, Mapping):
            continue
        annotations = page_entry.get("annotations")
        page = page_entry.get("page")
        if isinstance(annotations, list) and column in annotations and isinstance(page, int):
            pages.append(page)
    return pages


def _relationships_for_field(
    column: str, field_entry: Mapping[str, Any] | None
) -> list[dict[str, str]]:
    if field_entry is None or "relationships" not in field_entry:
        return []
    raw = field_entry["relationships"]
    if not isinstance(raw, list):
        raise SourceTruthBuildError(f"field_policy.fields[{column!r}].relationships must be a list")

    relationships: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise SourceTruthBuildError(
                f"field_policy.fields[{column!r}].relationships[{index}] must be a mapping"
            )
        relationship: dict[str, str] = {}
        for key, value in item.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SourceTruthBuildError(
                    f"field_policy.fields[{column!r}].relationships[{index}] "
                    "must contain only string keys and values"
                )
            relationship[key] = value
        relationships.append(relationship)
    return relationships


def _sensitivity_flags_for_field(column: str, field_entry: Mapping[str, Any] | None) -> list[str]:
    if field_entry is None or "sensitivity_flags" not in field_entry:
        return []
    raw = field_entry["sensitivity_flags"]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise SourceTruthBuildError(
            f"field_policy.fields[{column!r}].sensitivity_flags must be a list of strings"
        )
    return list(raw)


def _build_record_for_field(
    variable_id: str,
    field_entry: Mapping[str, Any] | None,
    pdf_extraction: Mapping[str, Any],
    pdf_annotated_variables: frozenset[str],
    *,
    dataset_present: bool,
) -> dict[str, Any]:
    if field_entry is None:
        # Dataset column with no field-policy entry: treat as
        # review_required so a human resolves the gap explicitly.
        action = "review_required"
        reason: str | None = "no_field_policy_entry"
        confidence = "low"
        field_label: str | None = None
        field_class: str | None = None
        section: str | None = None
        option_set_name: str | None = None
        pdf_status = "not_annotated"
    else:
        action = str(field_entry.get("action", "review_required"))
        reason_value = field_entry.get("reason")
        reason = reason_value if isinstance(reason_value, str) else None
        confidence = str(field_entry.get("confidence", "low"))
        field_label = field_entry.get("label")
        section_value = field_entry.get("section")
        section = section_value if isinstance(section_value, str) else None
        field_class_value = field_entry.get("field_class")
        field_class = field_class_value if isinstance(field_class_value, str) else None
        option_set_value = field_entry.get("option_set")
        option_set_name = option_set_value if isinstance(option_set_value, str) else None
        pdf_status = str(field_entry.get("pdf_annotation_status", "not_annotated"))

    pdf_present = pdf_status == "direct" or variable_id in pdf_annotated_variables
    derivation_targets = _derivation_targets_for_action(
        action, reason, dataset_present=dataset_present
    )
    review_state = _review_state_for_action(action)
    source_kind = _source_kind(
        dataset_present=dataset_present,
        pdf_present=pdf_present,
        action=action,
    )

    pdf_options = _option_set_values(pdf_extraction, option_set_name)
    annotation_pages = _annotation_pages_for_column(pdf_extraction, variable_id)
    relationships = _relationships_for_field(variable_id, field_entry)
    sensitivity_flags = _sensitivity_flags_for_field(variable_id, field_entry)
    analysis_queryable = dataset_present and DERIVATION_DATASET_SCHEMA in derivation_targets

    presence: dict[str, Any] = {
        "dataset": (
            {"present": True, "column": variable_id} if dataset_present else {"present": False}
        ),
        "pdf": {
            "present": pdf_present,
            "annotation_status": pdf_status,
            **({"section": section} if section else {}),
        },
        "dictionary": {"present": False},
    }

    record: dict[str, Any] = {
        "variable_id": variable_id,
        "source_kind": source_kind,
        "review_state": review_state,
        "presence": presence,
        "exact_source_wording": {
            "dataset_column": variable_id if dataset_present else None,
            "pdf_question": None,
            "pdf_options": pdf_options,
            "dictionary_label": None,
        },
        "normalized": {
            "label": _normalize_label(variable_id, field_label),
            "confidence": confidence,
            "normalization_basis": _normalization_basis(field_label),
            "handling_action": action,
            **({"handling_reason": reason} if reason else {}),
            **({"field_class": field_class} if field_class else {}),
            **({"sensitivity_flags": sensitivity_flags} if sensitivity_flags else {}),
            "analysis_queryable": analysis_queryable,
            **({"section": section} if section else {}),
            **({"option_set": option_set_name} if option_set_name else {}),
            **({"source_defined_options": pdf_options} if pdf_options else {}),
            **({"relationships": relationships} if relationships else {}),
        },
        "source_references": {
            "dataset": ({"column": variable_id} if dataset_present else {"present": False}),
            "pdf": {
                "annotation_status": pdf_status,
                "annotation_pages": annotation_pages,
                **({"option_set": option_set_name} if option_set_name else {}),
                **({"relationships": relationships} if relationships else {}),
            },
        },
        "derivation_targets": derivation_targets,
    }

    validate_record(record)
    return record


def build_records(
    column_inventory: Mapping[str, Any],
    pdf_extraction: Mapping[str, Any],
    field_policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Reconcile authorized evidence into validated Source of Truth records.

    Args:
        column_inventory: Mapping shaped like the ``*_column_inventory.json``
            output (``sheets[*].columns``). Column-name metadata only.
        pdf_extraction:   Mapping shaped like the ``*_pdf_extraction.json``
            output (``annotation_pages``, ``option_sets``,
            ``real_annotation_variables``). Authorized PDF evidence only.
        field_policy:     Mapping shaped like the ``*_field_policy.draft.yaml``
            output (per-variable ``action`` / ``confidence`` /
            ``section`` / ``pdf_annotation_status`` / ``option_set``).

    Returns:
        A list of Source of Truth records, one per dataset column. Every
        returned record has been passed through
        :func:`scripts.source_truth.record.validate_record`.

    Raises:
        SourceTruthBuildError: When the inputs cannot be reconciled
            (duplicate columns, unknown actions, malformed structure).
    """
    if not isinstance(column_inventory, Mapping):
        raise SourceTruthBuildError("column_inventory must be a mapping")
    if not isinstance(pdf_extraction, Mapping):
        raise SourceTruthBuildError("pdf_extraction must be a mapping")
    if not isinstance(field_policy, Mapping):
        raise SourceTruthBuildError("field_policy must be a mapping")
    _reject_forbidden_input_keys("column_inventory", column_inventory)
    _reject_forbidden_input_keys("pdf_extraction", pdf_extraction)
    _reject_forbidden_input_keys("field_policy", field_policy)

    columns = _column_inventory_columns(column_inventory)
    fields = field_policy.get("fields") or {}
    if not isinstance(fields, Mapping):
        raise SourceTruthBuildError("field_policy.fields must be a mapping")

    pdf_annotated_variables = frozenset(
        v for v in (pdf_extraction.get("real_annotation_variables") or []) if isinstance(v, str)
    )

    records: list[dict[str, Any]] = []
    seen_columns: set[str] = set()
    for column in columns:
        if column in seen_columns:
            raise SourceTruthBuildError(
                f"dataset column {column!r} appeared more than once during build"
            )
        seen_columns.add(column)
        entry = fields.get(column)
        if entry is not None and not isinstance(entry, Mapping):
            raise SourceTruthBuildError(f"field_policy.fields[{column!r}] must be a mapping")
        records.append(
            _build_record_for_field(
                variable_id=column,
                field_entry=entry,
                pdf_extraction=pdf_extraction,
                pdf_annotated_variables=pdf_annotated_variables,
                dataset_present=True,
            )
        )

    for variable_id, entry in fields.items():
        if variable_id in seen_columns:
            continue
        if not isinstance(variable_id, str):
            raise SourceTruthBuildError("field_policy.fields keys must be strings")
        if not isinstance(entry, Mapping):
            raise SourceTruthBuildError(f"field_policy.fields[{variable_id!r}] must be a mapping")
        if entry.get("source_kind") != "source_only" and entry.get("dataset_present") is not False:
            raise SourceTruthBuildError(
                f"field_policy.fields[{variable_id!r}] is absent from column_inventory; "
                "mark it source_kind='source_only' before building source-only metadata"
            )
        records.append(
            _build_record_for_field(
                variable_id=variable_id,
                field_entry=entry,
                pdf_extraction=pdf_extraction,
                pdf_annotated_variables=pdf_annotated_variables,
                dataset_present=False,
            )
        )
    return records


def build_source_truth_artifact(
    column_inventory: Mapping[str, Any],
    pdf_extraction: Mapping[str, Any],
    field_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the 6_HIV-style source-truth artifact and completeness report.

    The artifact is intentionally compact: it carries source identity,
    validated per-variable records, and the completeness report. It does
    not copy generated timestamps, footer/version-date metadata, or raw
    dataset values into the source-truth layer.
    """
    records = build_records(column_inventory, pdf_extraction, field_policy)
    coverage = field_policy.get("coverage")
    field_policy_boundary = coverage.get("boundary") if isinstance(coverage, Mapping) else None
    return {
        "artifact_type": "study_variable_source_truth",
        "study": column_inventory.get("study") or field_policy.get("study"),
        "source_file": column_inventory.get("source_file") or field_policy.get("source_file"),
        "source_pdf": field_policy.get("source_pdf"),
        "extraction_boundary": column_inventory.get("extraction_boundary") or field_policy_boundary,
        "records": records,
        "completeness": report_completeness(
            records=records,
            column_inventory=column_inventory,
            pdf_extraction=pdf_extraction,
        ),
    }
