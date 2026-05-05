"""All-form Source of Truth bulk validation tests (issue #78).

Validates that ``scripts.source_truth.all_form_validation`` discovers
every policy-pilot output set on disk, runs the source-truth builder +
schema validator + completeness report against each, and aggregates a
report that distinguishes blocking errors from review-required warnings
without inspecting raw dataset row values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Fixture data — minimal but valid pilot inputs for two forms
# (6_HIV and 98B_FOB) plus configurable mutations for warning/error cases.
# These mirror the inline fixtures used in tests/test_source_truth_builder.py
# and tests/test_source_truth_98b_fob.py at the level of detail needed to
# exercise the bulk runner.
# ---------------------------------------------------------------------------


_HIV_COLUMNS = [
    "SUBJID",
    "ICTC",
    "HIV_VISIT",
    "HIV_HIV",
    "Time_Stamp",
]


def _hiv_inputs() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_path": "Indo-VAP/datasets/6_HIV.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [{"sheet": "_6_HIV", "columns": list(_HIV_COLUMNS)}],
        "column_count": len(_HIV_COLUMNS),
    }
    pdf_extraction = {
        "page_count": 1,
        "annotation_count": 4,
        "real_annotation_variables": ["SUBJID", "ICTC", "HIV_VISIT", "HIV_HIV"],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": ["SUBJID", "ICTC", "HIV_VISIT", "HIV_HIV"],
            }
        ],
        "option_sets": {
            "hiv_result_pdf": {
                "source": "PDF option text",
                "values": ["Positive (+)", "Negative (-)", "Indeterminate"],
            }
        },
        "metadata": {"form_number": "Form 6", "form_title": "6 HIV"},
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf",
        "coverage": {
            "boundary": (
                "Dataset column names plus PDF annotations and visible options "
                "only; raw dataset values not inspected."
            )
        },
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "ICTC": {
                "action": "pseudonymize",
                "reason": "facility_clinic_ictc_or_site_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "HIV_VISIT": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
            },
            "HIV_HIV": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
                "option_set": "hiv_result_pdf",
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "non_pdf_system_timestamp_metadata",
                "confidence": "high",
                "section": "system_metadata",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }
    return {
        "column_inventory": column_inventory,
        "pdf_extraction": pdf_extraction,
        "field_policy": field_policy,
    }


_FOB_COLUMNS = [
    "SUBJID",
    "FOB_VISDAT",
    "FOB_COHBOUT",
    "FOB_REVIEW_NOTE",
    "Time_Stamp",
]


def _fob_inputs() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "98B_FOB.xlsx",
        "source_path": "Indo-VAP/datasets/98B_FOB.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [{"sheet": "_98B_FOB", "columns": list(_FOB_COLUMNS)}],
        "column_count": len(_FOB_COLUMNS),
    }
    pdf_extraction = {
        "page_count": 2,
        "annotation_count": 4,
        "real_annotation_variables": [
            "SUBJID",
            "FOB_VISDAT",
            "FOB_COHBOUT",
            "FOB_REVIEW_NOTE",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "FOB_VISDAT",
                    "FOB_COHBOUT",
                    "FOB_REVIEW_NOTE",
                ],
            }
        ],
        "option_sets": {
            "fob_cohort_outcome": {
                "source": "PDF option text",
                "values": ["No TB", "Probable case", "Definite case"],
            }
        },
        "metadata": {"form_number": "Form 98B", "form_title": "Final Outcome"},
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "98B_FOB.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/98B_FOB.pdf",
        "coverage": {
            "boundary": (
                "Dataset column names plus PDF annotations/options only; "
                "raw dataset values not inspected."
            )
        },
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "FOB_VISDAT": {
                "action": "jitter_date",
                "reason": "date_field",
                "confidence": "high",
                "section": "final_outcome_visit",
                "pdf_annotation_status": "direct",
            },
            "FOB_COHBOUT": {
                "action": "keep",
                "reason": "clinical_outcome",
                "confidence": "high",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
                "option_set": "fob_cohort_outcome",
            },
            "FOB_REVIEW_NOTE": {
                "action": "review_required",
                "reason": "ambiguous_free_text_follow_up_note",
                "confidence": "low",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "timestamp_metadata",
                "confidence": "high",
                "section": "system_metadata",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }
    return {
        "column_inventory": column_inventory,
        "pdf_extraction": pdf_extraction,
        "field_policy": field_policy,
    }


# ---------------------------------------------------------------------------
# Helpers to materialize pilot input triples on disk in the layout the
# discovery function expects:
#
#     <root>/<form_id>/column_inventory.json
#     <root>/<form_id>/pdf_extraction.json
#     <root>/<form_id>/field_policy.draft.yaml
#
# (The discovery function should also accept .yml / .json field_policy
# alternatives; we exercise yaml here.)
# ---------------------------------------------------------------------------


def _write_form(root: Path, form_id: str, inputs: dict[str, Any]) -> Path:
    form_dir = root / form_id
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / "column_inventory.json").write_text(
        json.dumps(inputs["column_inventory"]), encoding="utf-8"
    )
    (form_dir / "pdf_extraction.json").write_text(
        json.dumps(inputs["pdf_extraction"]), encoding="utf-8"
    )
    (form_dir / "field_policy.draft.yaml").write_text(
        yaml.safe_dump(inputs["field_policy"], sort_keys=False), encoding="utf-8"
    )
    return form_dir


def _seed_two_pilot_forms(tmp_path: Path) -> Path:
    root = tmp_path / "policy_pilot"
    _write_form(root, "6_HIV", _hiv_inputs())
    _write_form(root, "98B_FOB", _fob_inputs())
    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_discover_policy_pilot_forms_finds_every_form_under_root(tmp_path: Path) -> None:
    from scripts.source_truth.all_form_validation import discover_policy_pilot_forms

    root = _seed_two_pilot_forms(tmp_path)
    _write_form(root, "extra_form", _hiv_inputs())  # arbitrary third form

    discovered = discover_policy_pilot_forms(root)

    form_ids = sorted(entry["form_id"] for entry in discovered)
    assert form_ids == ["6_HIV", "98B_FOB", "extra_form"]
    # Each entry must point at the three input files.
    for entry in discovered:
        assert Path(entry["column_inventory_path"]).is_file()
        assert Path(entry["pdf_extraction_path"]).is_file()
        assert Path(entry["field_policy_path"]).is_file()


def test_validate_all_forms_includes_named_pilot_fixtures(tmp_path: Path) -> None:
    from scripts.source_truth.all_form_validation import validate_all_forms

    root = _seed_two_pilot_forms(tmp_path)

    report = validate_all_forms(root)

    form_ids = sorted(form["form_id"] for form in report["forms"])
    assert "6_HIV" in form_ids
    assert "98B_FOB" in form_ids


def test_validate_all_forms_report_has_required_per_form_fields(tmp_path: Path) -> None:
    from scripts.source_truth.all_form_validation import validate_all_forms

    root = _seed_two_pilot_forms(tmp_path)

    report = validate_all_forms(root)

    required_keys = {
        "form_id",
        "dataset_columns_covered",
        "pdf_fields_covered",
        "unmatched_dataset_columns",
        "unmatched_pdf_fields",
        "evidence_gaps",
        "excluded_footer_version_content",
        "review_required_fields",
        "blocking_errors",
        "warnings",
        "status",
    }
    for form_report in report["forms"]:
        missing = required_keys - set(form_report.keys())
        assert not missing, f"missing keys in {form_report.get('form_id')}: {missing}"
        # excluded_footer_version_content must carry the explicit boundary note.
        assert isinstance(form_report["excluded_footer_version_content"], dict)
        assert "note" in form_report["excluded_footer_version_content"]


def test_validate_all_forms_distinguishes_blocking_errors_from_warnings(tmp_path: Path) -> None:
    from scripts.source_truth.all_form_validation import validate_all_forms

    root = _seed_two_pilot_forms(tmp_path)

    # Warning case: a third form with a review_required field. Already covered
    # by 98B_FOB (FOB_REVIEW_NOTE) but we add a clean-passing form too.
    clean_inputs = _hiv_inputs()
    _write_form(root, "clean_form", clean_inputs)

    # Failing case: a form whose field_policy omits a dataset column, which
    # forces the builder to emit a review_required record AND the
    # completeness report to report no blocking errors *unless* we also
    # delete the column from the inventory entry. To trigger a blocking
    # error, drop a real dataset column from the field_policy entirely
    # (still produces no_field_policy_entry warning) and add a forbidden
    # raw-row key to the pdf_extraction so the build raises.
    failing_inputs = _hiv_inputs()
    failing_inputs["pdf_extraction"]["sample_values"] = ["RAW_FORBIDDEN_VALUE"]
    _write_form(root, "failing_form", failing_inputs)

    report = validate_all_forms(root)

    by_id = {form["form_id"]: form for form in report["forms"]}

    # Failing form: blocking_errors must be non-empty and status reflects it.
    assert by_id["failing_form"]["blocking_errors"], "expected blocking errors for failing form"
    assert by_id["failing_form"]["status"] == "failed"

    # Warning form (98B_FOB has FOB_REVIEW_NOTE = review_required):
    assert by_id["98B_FOB"]["status"] == "warning"
    assert "FOB_REVIEW_NOTE" in by_id["98B_FOB"]["review_required_fields"]
    assert by_id["98B_FOB"]["blocking_errors"] == []
    assert by_id["98B_FOB"]["warnings"], "review_required field must surface as a warning"

    # Clean form passes.
    assert by_id["clean_form"]["status"] == "passed"
    assert by_id["clean_form"]["blocking_errors"] == []
    assert by_id["clean_form"]["warnings"] == []

    # Aggregate rollup separates the buckets explicitly.
    summary = report["summary"]
    assert summary["forms_total"] == len(by_id)
    assert "failing_form" in summary["forms_with_blocking_errors"]
    assert "98B_FOB" in summary["forms_with_warnings_only"]
    assert "clean_form" in summary["forms_passing"]
    # Buckets are mutually exclusive.
    assert (
        set(summary["forms_with_blocking_errors"]) & set(summary["forms_with_warnings_only"])
    ) == set()
    assert (set(summary["forms_with_blocking_errors"]) & set(summary["forms_passing"])) == set()


def test_validate_all_forms_does_not_read_raw_dataset_row_values(tmp_path: Path) -> None:
    """The validator must never echo or surface raw row-value content."""
    from scripts.source_truth.all_form_validation import validate_all_forms

    root = _seed_two_pilot_forms(tmp_path)

    sentinel = "ROW_VALUE_SHOULD_NEVER_APPEAR_8c4f1"
    # Deliberately plant the sentinel under a forbidden key so the builder
    # rejects the input. The aggregate report must still not contain the
    # sentinel string itself anywhere — only the boundary metadata.
    poisoned = _hiv_inputs()
    poisoned["column_inventory"]["sheets"][0]["sample_values"] = [sentinel]
    _write_form(root, "poisoned_form", poisoned)

    report = validate_all_forms(root)

    # Walk every string in the report and confirm the sentinel is absent.
    def _strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            out: list[str] = []
            for key, child in value.items():
                if isinstance(key, str):
                    out.append(key)
                out.extend(_strings(child))
            return out
        if isinstance(value, list):
            out = []
            for child in value:
                out.extend(_strings(child))
            return out
        return []

    for s in _strings(report):
        assert sentinel not in s, "raw row sentinel leaked into all-form validation report"

    # And the poisoned form must be reported as failed with a forbidden-key
    # blocking error rather than silently accepted.
    by_id = {form["form_id"]: form for form in report["forms"]}
    assert by_id["poisoned_form"]["status"] == "failed"
    assert any(
        "sample_values" in err or "forbidden" in err.lower()
        for err in by_id["poisoned_form"]["blocking_errors"]
    )


def test_validate_all_forms_excludes_footer_and_version_content(tmp_path: Path) -> None:
    """Footer/version-date keys must be excluded explicitly with a note."""
    from scripts.source_truth.all_form_validation import validate_all_forms

    root = _seed_two_pilot_forms(tmp_path)

    report = validate_all_forms(root)

    by_id = {form["form_id"]: form for form in report["forms"]}
    excluded = by_id["6_HIV"]["excluded_footer_version_content"]
    assert excluded["count"] == 0
    assert "footer" in excluded["note"].lower()
    assert "version" in excluded["note"].lower()


def test_validate_all_forms_named_fixtures_pass_or_warn(tmp_path: Path) -> None:
    """6_HIV and 98B_FOB are explicit named fixtures; build never errors on them."""
    from scripts.source_truth.all_form_validation import validate_all_forms

    root = _seed_two_pilot_forms(tmp_path)

    report = validate_all_forms(root)

    by_id = {form["form_id"]: form for form in report["forms"]}
    assert by_id["6_HIV"]["status"] in {"passed", "warning"}
    assert by_id["98B_FOB"]["status"] in {"passed", "warning"}
    assert by_id["6_HIV"]["blocking_errors"] == []
    assert by_id["98B_FOB"]["blocking_errors"] == []
    # Both fixtures must contribute their PDF coverage.
    assert "SUBJID" in by_id["6_HIV"]["dataset_columns_covered"]
    assert "SUBJID" in by_id["98B_FOB"]["dataset_columns_covered"]
