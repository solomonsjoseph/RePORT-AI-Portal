"""Source Truth Completeness Reporter.

**What.** ``report_completeness`` summarizes how well a set of Source of
Truth records covers the available authorized evidence: which dataset
columns are represented, which PDF-annotated variables are represented,
which items remain unmatched, which records are still review-required,
and how much footer/version-date content was excluded upstream.

**Why.** Issue #66 acceptance criteria require a *Completeness Report*
that shows dataset columns covered, PDF fields covered, unmatched
items, evidence gaps, review-required items, and excluded
footer/version content. The report is the audit artifact that proves
the SoT layer covers every dataset column exactly once and that no
authorized PDF evidence has been silently dropped.

**How.** The reporter is a pure function over already-built SoT records
plus the same ``column_inventory`` and ``pdf_extraction`` inputs the
builder consumed. It does no I/O, raises no errors on incomplete data
(incompleteness *is* the report), and never reads raw dataset rows.
Footer/version-date keys are excluded *upstream* at extraction time, so
the reporter scans the supplied PDF extraction for any forbidden keys
and reports the count plus an explicit boundary note.

Use::

    from scripts.source_truth.builder import build_records
    from scripts.source_truth.completeness import report_completeness

    records = build_records(column_inventory, pdf_extraction, field_policy)
    report = report_completeness(records, column_inventory, pdf_extraction)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from scripts.source_truth.record import (
    FORBIDDEN_ARTIFACT_VERSION_KEYS,
    FORBIDDEN_RAW_VALUE_KEYS,
)

__all__ = [
    "FOOTER_EXCLUSION_BOUNDARY_NOTE",
    "report_completeness",
]


FOOTER_EXCLUSION_BOUNDARY_NOTE = (
    "footers, form version dates, PDF creation/print/export timestamps, and "
    "artifact-version metadata are excluded upstream at the PDF extraction "
    "boundary; the source-truth layer never sees them"
)


def _all_inventory_columns(column_inventory: Mapping[str, Any]) -> list[str]:
    sheets = column_inventory.get("sheets") or []
    columns: list[str] = []
    for sheet in sheets:
        if isinstance(sheet, Mapping):
            columns.extend(column for column in sheet.get("columns", []) if isinstance(column, str))
    return columns


def _pdf_annotated_variables(pdf_extraction: Mapping[str, Any]) -> list[str]:
    raw = pdf_extraction.get("real_annotation_variables") or []
    return [v for v in raw if isinstance(v, str)]


def _scan_for_forbidden_keys(value: Any) -> list[str]:
    """Return any forbidden raw-value or artifact-version keys found anywhere.

    The SoT builder excludes these by contract; the reporter scans the
    *PDF extraction input* so any upstream slip is surfaced in the
    completeness report rather than swallowed silently.
    """
    found: list[str] = []
    forbidden = FORBIDDEN_RAW_VALUE_KEYS | FORBIDDEN_ARTIFACT_VERSION_KEYS
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in forbidden:
                found.append(key)
            found.extend(_scan_for_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_scan_for_forbidden_keys(child))
    return found


def report_completeness(
    records: Sequence[Mapping[str, Any]],
    column_inventory: Mapping[str, Any],
    pdf_extraction: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarize SoT coverage for one form's authorized evidence.

    Args:
        records:          The output of :func:`build_records`.
        column_inventory: The column-inventory input the builder consumed.
        pdf_extraction:   The PDF-extraction input the builder consumed.

    Returns:
        A mapping with the following keys (counts default to zero
        when the underlying list is empty)::

            {
                "dataset_columns_total":          int,
                "dataset_columns_covered":        list[str],
                "unmatched_dataset_columns":      list[str],
                "pdf_fields_total":               int,
                "pdf_fields_covered":             list[str],
                "unmatched_pdf_fields":           list[str],
                "review_required_fields":         list[str],
                "evidence_gaps":                  list[str],
                "blocking_errors":                list[str],
                "warnings":                       list[str],
                "excluded_footer_version_content": {
                    "count":    int,
                    "note":     str,
                    "found":    list[str],
                },
            }
    """
    inventory_columns = _all_inventory_columns(column_inventory)
    pdf_variables = _pdf_annotated_variables(pdf_extraction)

    record_dataset_columns: list[str] = []
    pdf_present_columns: set[str] = set()
    review_required: list[str] = []
    evidence_gaps: list[str] = []
    duplicate_columns: list[str] = []
    seen_dataset_columns: set[str] = set()

    for record in records:
        dataset_block = record.get("presence", {}).get("dataset", {})
        column = dataset_block.get("column") or record.get("variable_id")
        if isinstance(column, str) and dataset_block.get("present") is True:
            if column in seen_dataset_columns:
                duplicate_columns.append(column)
            seen_dataset_columns.add(column)
            record_dataset_columns.append(column)
        pdf_block = record.get("presence", {}).get("pdf", {})
        if pdf_block.get("present") is True:
            variable_id = record.get("variable_id")
            if isinstance(variable_id, str):
                pdf_present_columns.add(variable_id)
        if record.get("review_state") == "review_required":
            variable_id = record.get("variable_id")
            if isinstance(variable_id, str):
                review_required.append(variable_id)
        normalized = record.get("normalized", {})
        if normalized.get("handling_reason") == "no_field_policy_entry":
            variable_id = record.get("variable_id")
            if isinstance(variable_id, str):
                evidence_gaps.append(f"{variable_id}: dataset column has no field-policy entry")

    inventory_set = set(inventory_columns)
    covered_dataset_columns = sorted(inventory_set & set(record_dataset_columns))
    unmatched_dataset_columns = sorted(inventory_set - set(record_dataset_columns))

    pdf_set = set(pdf_variables)
    covered_pdf_fields = sorted(pdf_set & pdf_present_columns)
    unmatched_pdf_fields = sorted(pdf_set - pdf_present_columns)

    forbidden_found = _scan_for_forbidden_keys(pdf_extraction)

    blocking_errors: list[str] = []
    warnings: list[str] = []
    if duplicate_columns:
        blocking_errors.append(
            "duplicate dataset columns in records: " + ", ".join(sorted(set(duplicate_columns)))
        )
    if unmatched_dataset_columns:
        blocking_errors.append(
            "dataset columns not represented in records: " + ", ".join(unmatched_dataset_columns)
        )
    if forbidden_found:
        blocking_errors.append(
            "PDF extraction input leaked forbidden keys: " + ", ".join(sorted(set(forbidden_found)))
        )
    if review_required:
        warnings.append("review_required fields: " + ", ".join(sorted(set(review_required))))
    if unmatched_pdf_fields:
        warnings.append(
            "PDF-annotated fields not represented as PDF-present in records: "
            + ", ".join(unmatched_pdf_fields)
        )

    return {
        "dataset_columns_total": len(inventory_columns),
        "dataset_columns_covered": covered_dataset_columns,
        "unmatched_dataset_columns": unmatched_dataset_columns,
        "pdf_fields_total": len(pdf_variables),
        "pdf_fields_covered": covered_pdf_fields,
        "unmatched_pdf_fields": unmatched_pdf_fields,
        "review_required_fields": sorted(set(review_required)),
        "evidence_gaps": evidence_gaps,
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "excluded_footer_version_content": {
            "count": len(forbidden_found),
            "note": FOOTER_EXCLUSION_BOUNDARY_NOTE,
            "found": sorted(set(forbidden_found)),
        },
    }
