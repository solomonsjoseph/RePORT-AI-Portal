"""6_HIV tracer-bullet tests for the Study Variable Source of Truth builder."""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

import pytest

from scripts.source_truth.builder import (
    DERIVATION_CATALOG,
    DERIVATION_CLEANUP_LEDGER,
    DERIVATION_DATASET_SCHEMA,
    DERIVATION_PHI_LEDGER,
    SourceTruthBuildError,
    build_source_truth_artifact,
)
from scripts.source_truth.record import validate_record

_HIV_COLUMNS = [
    "SUBJID",
    "HIV_VISIT",
    "ICTC",
    "HIV_HIVND",
    "HIV_HIVDAT",
    "HIV_HIV",
    "HIV_HIVNDOTH",
    "HIV_ARTTX",
    "HIV_ARTDAT",
    "HIV_ARTND",
    "HIV_CD4DONE",
    "HIV_CD4DAT",
    "HIV_CD4",
    "HIV_CD4LY",
    "HIV_CD4ND",
    "HIV_CD4LYND",
    "HIV_SIGN",
    "HIV_INIT",
    "HIV_COMPDAT",
    "Time_Stamp",
]

_PDF_ANNOTATED_VARIABLES = [
    "HIV_ARTDAT",
    "HIV_ARTND",
    "HIV_ARTTX",
    "HIV_CD4",
    "HIV_CD4DONE",
    "HIV_CD4LY",
    "HIV_CD4LYND",
    "HIV_CD4ND",
    "HIV_COMPDAT",
    "HIV_HIV",
    "HIV_HIVDAT",
    "HIV_HIVND",
    "HIV_HIVNDOTH",
    "HIV_INIT",
    "HIV_SIGN",
    "HIV_VISIT",
    "ICTC",
    "SUBJID",
]


def _column_inventory() -> dict[str, Any]:
    return {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_path": "Indo-VAP/datasets/6_HIV.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "generated_utc": "2026-05-01T16:53:00+00:00",
        "sheets": [{"sheet": "_6_HIV", "columns": list(_HIV_COLUMNS)}],
        "column_count": len(_HIV_COLUMNS),
    }


def _pdf_extraction() -> dict[str, Any]:
    return {
        "page_count": 1,
        "acroform_field_count": 0,
        "annotation_count": 22,
        "real_annotation_variables": list(_PDF_ANNOTATED_VARIABLES),
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "ICTC",
                    "HIV_VISIT",
                    "HIV_HIVND",
                    "HIV_HIVDAT",
                    "HIV_HIV",
                    "HIV_HIVNDOTH",
                    "HIV_ARTTX",
                    "HIV_ARTDAT",
                    "HIV_ARTND",
                    "1",
                    "HIV_CD4DONE",
                    "HIV_ARTDAT",
                    "HIV_CD4",
                    "HIV_CD4ND",
                    "1",
                    "HIV_CD4LY",
                    "HIV_CD4LYND",
                    "1",
                    "HIV_SIGN",
                    "HIV_COMPDAT",
                    "HIV_INIT",
                ],
            }
        ],
        "option_sets": {
            "date_dmy_pdf": {
                "source": "PDF date boxes",
                "values": ["Day", "Month", "Year"],
            },
            "hiv_result_pdf": {
                "source": "PDF option text",
                "values": ["Positive (+)", "Negative (-)", "Indeterminate"],
            },
        },
        "metadata": {
            "form_number": "Form 6",
            "form_title": "6 HIV v1.0",
            "form_version": "v1.0",
        },
    }


def _field_policy() -> dict[str, Any]:
    fields: dict[str, dict[str, str]] = {
        "SUBJID": {
            "action": "pseudonymize",
            "label": "SUBJ",
            "reason": "participant_identifier",
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
        "ICTC": {
            "action": "pseudonymize",
            "label": "FAC",
            "reason": "facility_clinic_ictc_or_site_identifier",
            "confidence": "high",
            "section": "participant_header",
            "pdf_annotation_status": "direct",
        },
        "HIV_HIVND": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "high",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_HIVDAT": {
            "action": "jitter_date",
            "reason": "date_field",
            "confidence": "high",
            "section": "hiv_testing",
            "pdf_annotation_status": "direct",
            "option_set": "date_dmy_pdf",
        },
        "HIV_HIV": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "high",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
            "option_set": "hiv_result_pdf",
        },
        "HIV_HIVNDOTH": {
            "action": "review_required",
            "reason": "possible_free_text_reason_child_of_hiv_testing_status",
            "confidence": "low",
            "section": "hiv_testing",
            "pdf_annotation_status": "direct",
        },
        "HIV_ARTTX": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "high",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_ARTDAT": {
            "action": "jitter_date",
            "reason": "date_field",
            "confidence": "high",
            "section": "hiv_treatment_regimen",
            "pdf_annotation_status": "direct",
            "option_set": "date_dmy_pdf",
        },
        "HIV_ARTND": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "medium",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_CD4DONE": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "high",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_CD4DAT": {
            "action": "jitter_date",
            "reason": "date_field",
            "confidence": "high",
            "section": "cd4_enumeration",
            "pdf_annotation_status": "not_annotated",
            "option_set": "date_dmy_pdf",
        },
        "HIV_CD4": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "high",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_CD4LY": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "high",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_CD4ND": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "medium",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_CD4LYND": {
            "action": "keep",
            "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "confidence": "medium",
            "section": "hiv_fields",
            "pdf_annotation_status": "direct",
        },
        "HIV_SIGN": {
            "action": "drop",
            "reason": "signature_field",
            "confidence": "high",
            "section": "completion",
            "pdf_annotation_status": "direct",
        },
        "HIV_INIT": {
            "action": "drop",
            "reason": "initials_field",
            "confidence": "high",
            "section": "completion",
            "pdf_annotation_status": "direct",
        },
        "HIV_COMPDAT": {
            "action": "jitter_date",
            "reason": "date_field",
            "confidence": "high",
            "section": "completion",
            "pdf_annotation_status": "direct",
            "option_set": "date_dmy_pdf",
        },
        "Time_Stamp": {
            "action": "drop",
            "reason": "non_pdf_system_timestamp_metadata",
            "confidence": "high",
            "section": "system_metadata",
            "pdf_annotation_status": "not_annotated",
        },
    }
    return {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf",
        "coverage": {
            "boundary": (
                "Dataset column names plus PDF text, PDF annotations, and PDF "
                "visible options only; raw dataset values not inspected."
            )
        },
        "fields": fields,
    }


def _artifact() -> dict[str, Any]:
    return build_source_truth_artifact(
        _column_inventory(),
        _pdf_extraction(),
        _field_policy(),
    )


def test_6_hiv_records_cover_every_dataset_column_exactly_once() -> None:
    artifact = _artifact()
    records = artifact["records"]
    covered = [record["presence"]["dataset"]["column"] for record in records]

    assert len(records) == len(_HIV_COLUMNS)
    assert Counter(covered) == Counter(_HIV_COLUMNS)
    for record in records:
        validate_record(record)


def test_6_hiv_records_preserve_pdf_evidence_and_normalization_basis() -> None:
    records = {record["variable_id"]: record for record in _artifact()["records"]}

    hiv_result = records["HIV_HIV"]
    assert hiv_result["exact_source_wording"]["dataset_column"] == "HIV_HIV"
    assert hiv_result["normalized"]["label"] == "hiv_hiv"
    assert hiv_result["normalized"]["confidence"] == "high"
    assert hiv_result["normalized"]["normalization_basis"] == ("dataset_column_code_lowercased")
    assert hiv_result["normalized"]["source_defined_options"] == [
        "Positive (+)",
        "Negative (-)",
        "Indeterminate",
    ]
    assert hiv_result["source_references"]["pdf"]["annotation_pages"] == [1]

    subject_id = records["SUBJID"]
    assert subject_id["exact_source_wording"]["dataset_column"] == "SUBJID"
    assert subject_id["normalized"]["label"] == "subj"
    assert subject_id["normalized"]["normalization_basis"] == "field_policy_label"


def test_6_hiv_records_preserve_parent_child_relationship_evidence() -> None:
    field_policy = copy.deepcopy(_field_policy())
    field_policy["fields"]["HIV_HIVNDOTH"]["relationships"] = [
        {
            "type": "free_text_child",
            "parent_variable_id": "HIV_HIVND",
            "parent_option": "No, specify reason",
            "basis": "pdf_option_group",
        }
    ]

    artifact = build_source_truth_artifact(
        _column_inventory(),
        _pdf_extraction(),
        field_policy,
    )
    records = {record["variable_id"]: record for record in artifact["records"]}

    assert records["HIV_HIVNDOTH"]["normalized"]["relationships"] == [
        {
            "type": "free_text_child",
            "parent_variable_id": "HIV_HIVND",
            "parent_option": "No, specify reason",
            "basis": "pdf_option_group",
        }
    ]
    assert records["HIV_HIVNDOTH"]["source_references"]["pdf"]["relationships"] == [
        {
            "type": "free_text_child",
            "parent_variable_id": "HIV_HIVND",
            "parent_option": "No, specify reason",
            "basis": "pdf_option_group",
        }
    ]


def test_6_hiv_derivation_targets_separate_catalog_schema_and_ledgers() -> None:
    records = {record["variable_id"]: record for record in _artifact()["records"]}

    assert records["HIV_HIV"]["derivation_targets"] == [
        DERIVATION_CATALOG,
        DERIVATION_DATASET_SCHEMA,
    ]
    assert records["SUBJID"]["derivation_targets"] == [
        DERIVATION_CATALOG,
        DERIVATION_DATASET_SCHEMA,
        DERIVATION_PHI_LEDGER,
    ]
    assert records["HIV_SIGN"]["derivation_targets"] == [DERIVATION_PHI_LEDGER]
    assert records["Time_Stamp"]["derivation_targets"] == [DERIVATION_CLEANUP_LEDGER]
    assert records["HIV_HIVNDOTH"]["review_state"] == "review_required"
    assert records["HIV_HIVNDOTH"]["derivation_targets"] == []


def test_6_hiv_completeness_report_distinguishes_errors_from_review_warnings() -> None:
    report = _artifact()["completeness"]

    assert report["dataset_columns_total"] == len(_HIV_COLUMNS)
    assert report["dataset_columns_covered"] == sorted(_HIV_COLUMNS)
    assert report["unmatched_dataset_columns"] == []
    assert report["pdf_fields_total"] == len(_PDF_ANNOTATED_VARIABLES)
    assert report["unmatched_pdf_fields"] == []
    assert report["review_required_fields"] == ["HIV_HIVNDOTH"]
    assert report["blocking_errors"] == []
    assert report["excluded_footer_version_content"]["count"] == 0
    assert "review_required fields: HIV_HIVNDOTH" in report["warnings"]


@pytest.mark.parametrize(
    ("input_name", "payload_path", "bad_key"),
    [
        ("column_inventory", ("sheets", 0), "rows"),
        ("pdf_extraction", ("metadata",), "pdf_creation_date"),
        ("field_policy", ("coverage",), "raw_values"),
    ],
)
def test_builder_rejects_raw_values_and_artifact_metadata_at_input_boundary(
    input_name: str, payload_path: tuple[str | int, ...], bad_key: str
) -> None:
    column_inventory = _column_inventory()
    pdf_extraction = _pdf_extraction()
    field_policy = _field_policy()
    payloads = {
        "column_inventory": column_inventory,
        "pdf_extraction": pdf_extraction,
        "field_policy": field_policy,
    }

    target = payloads[input_name]
    for part in payload_path:
        target = target[part]
    target[bad_key] = ["forbidden"]

    with pytest.raises(SourceTruthBuildError, match=input_name):
        build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)


def test_missing_field_policy_entry_becomes_review_required_evidence_gap() -> None:
    field_policy = _field_policy()
    field_policy = copy.deepcopy(field_policy)
    field_policy["fields"].pop("HIV_ARTND")

    artifact = build_source_truth_artifact(
        _column_inventory(),
        _pdf_extraction(),
        field_policy,
    )
    records = {record["variable_id"]: record for record in artifact["records"]}

    assert records["HIV_ARTND"]["review_state"] == "review_required"
    assert (
        "HIV_ARTND: dataset column has no field-policy entry"
        in artifact["completeness"]["evidence_gaps"]
    )
