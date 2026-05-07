"""Tests for SoT reconciliation ledger readers.

Pure-function readers that take parsed audit dicts and return per-form
dropped-column sets. They consume the *as-written* shapes emitted by:

    - scripts/security/phi_scrub.py::_emit_audit
        → {study, generated_utc, leg, compliance_posture,
           scrubbed: [{scope, field, file, count}], orphan_rows: {}}
    - scripts/extraction/dataset_cleanup.py::_serialize_audit
        → {study, generated_utc, leg, removed: [{scope, name, file, sheet,
           reason, kept}], skipped, errors}
"""

from __future__ import annotations

from scripts.source_truth.ledger_readers import (
    load_cleanup_dropped_columns,
    load_phi_dropped_columns,
)


# ---------------------------------------------------------------------------
# load_phi_dropped_columns
# ---------------------------------------------------------------------------


def test_phi_reader_extracts_drop_events_grouped_by_form() -> None:
    report = {
        "study": "Mini",
        "generated_utc": "2026-05-07T00:00:00Z",
        "leg": "phi-scrub",
        "compliance_posture": "research-deid",
        "scrubbed": [
            {"scope": "drop", "field": "PATIENT_NAME", "file": "1A_ICScreening.jsonl", "count": 3},
            {"scope": "drop", "field": "ADDRESS", "file": "1A_ICScreening.jsonl", "count": 3},
            {"scope": "birthdate-drop", "field": "DOB", "file": "2A_ICBaseline.jsonl", "count": 5},
            # non-drop scopes must be ignored
            {"scope": "id", "field": "FID", "file": "1A_ICScreening.jsonl", "count": 3},
            {"scope": "date", "field": "VISIT_DT", "file": "2A_ICBaseline.jsonl", "count": 5},
            {"scope": "cap", "field": "AGE", "file": "2A_ICBaseline.jsonl", "count": 5},
            {"scope": "generalize", "field": "ZIP", "file": "1A_ICScreening.jsonl", "count": 3},
            {"scope": "suppress-small-cell", "field": "CELL", "file": "1A_ICScreening.jsonl", "count": 3},
        ],
        "orphan_rows": {},
    }

    result = load_phi_dropped_columns(report)

    assert result == {
        "1A_ICScreening": frozenset({"PATIENT_NAME", "ADDRESS"}),
        "2A_ICBaseline": frozenset({"DOB"}),
    }
    # Returned values must be frozensets so callers cannot mutate them.
    for forms_drops in result.values():
        assert isinstance(forms_drops, frozenset)


def test_phi_reader_returns_empty_when_no_drops() -> None:
    report = {
        "study": "Mini",
        "generated_utc": "2026-05-07T00:00:00Z",
        "leg": "phi-scrub",
        "compliance_posture": "research-deid",
        "scrubbed": [
            {"scope": "id", "field": "FID", "file": "10_TST.jsonl", "count": 3},
        ],
        "orphan_rows": {},
    }

    assert load_phi_dropped_columns(report) == {}


def test_phi_reader_handles_missing_scrubbed_key() -> None:
    # An empty/partial report should not error.
    assert load_phi_dropped_columns({}) == {}
    assert load_phi_dropped_columns({"scrubbed": []}) == {}


def test_phi_reader_strips_jsonl_suffix_from_file() -> None:
    report = {
        "scrubbed": [
            {"scope": "drop", "field": "X", "file": "Form_A.jsonl", "count": 1},
            # File without .jsonl suffix passes through unchanged
            {"scope": "drop", "field": "Y", "file": "Form_B", "count": 1},
        ]
    }
    result = load_phi_dropped_columns(report)
    assert result == {
        "Form_A": frozenset({"X"}),
        "Form_B": frozenset({"Y"}),
    }


# ---------------------------------------------------------------------------
# load_cleanup_dropped_columns
# ---------------------------------------------------------------------------


def test_cleanup_reader_extracts_dataset_column_events_only() -> None:
    """Only ``scope == "dataset-column"`` events contribute column drops.

    File-level removals (junk, duplicate-pair) drop whole files, not columns
    from a surviving file, and must not be confused with column drops.

    Note: cleanup events use ``file`` = source dataset filename
    (e.g. ``2A_ICBaseline.xlsx``), not the JSONL filename. The reader
    accepts a ``source_to_form`` mapping to resolve these to form names.
    """
    report = {
        "study": "Mini",
        "generated_utc": "2026-05-07T00:00:00Z",
        "leg": "dataset",
        "removed": [
            {
                "scope": "dataset-column",
                "name": "DUP_COL_1",
                "file": "1A_ICScreening.xlsx",
                "sheet": "Sheet1",
                "reason": "100% identical to 'DUP_COL'",
                "kept": "DUP_COL",
            },
            {
                "scope": "dataset-column",
                "name": "ANOTHER_DUP",
                "file": "1A_ICScreening.xlsx",
                "sheet": "Sheet1",
                "reason": "entirely null",
                "kept": None,
            },
            # File-level removals are NOT column drops
            {
                "scope": "dataset-junk-file",
                "name": "Paste Errors",
                "file": "Paste Errors.jsonl",
                "sheet": None,
                "reason": "known junk artifact",
                "kept": None,
            },
            {
                "scope": "dataset-duplicate-file",
                "name": "14_Case_Control",
                "file": "14_Case_Control.jsonl",
                "sheet": None,
                "reason": "subset",
                "kept": "14_CaseControl.jsonl",
            },
        ],
        "skipped": [],
        "errors": [],
    }
    source_to_form = {"1A_ICScreening.xlsx": "1A_ICScreening"}

    result = load_cleanup_dropped_columns(report, source_to_form=source_to_form)
    assert result == {
        "1A_ICScreening": frozenset({"DUP_COL_1", "ANOTHER_DUP"}),
    }
    for v in result.values():
        assert isinstance(v, frozenset)


def test_cleanup_reader_returns_empty_when_no_column_drops() -> None:
    report = {
        "removed": [
            {
                "scope": "dataset-junk-file",
                "name": "Paste Errors",
                "file": "Paste Errors.jsonl",
                "sheet": None,
                "reason": "known junk",
                "kept": None,
            },
        ]
    }
    assert load_cleanup_dropped_columns(report, source_to_form={}) == {}


def test_cleanup_reader_handles_unmapped_source_filename() -> None:
    """A drop event whose ``file`` is not in ``source_to_form`` falls back to
    the file stem so callers can detect it during reconciliation."""
    report = {
        "removed": [
            {
                "scope": "dataset-column",
                "name": "X",
                "file": "Mystery_Form.xlsx",
                "sheet": None,
                "reason": "entirely null",
                "kept": None,
            },
        ]
    }
    result = load_cleanup_dropped_columns(report, source_to_form={})
    # Falls back to stripping the extension off the source filename.
    assert result == {"Mystery_Form": frozenset({"X"})}


def test_cleanup_reader_handles_missing_keys() -> None:
    assert load_cleanup_dropped_columns({}, source_to_form={}) == {}
    assert load_cleanup_dropped_columns({"removed": []}, source_to_form={}) == {}
