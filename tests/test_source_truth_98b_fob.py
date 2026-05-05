"""98B_FOB cleanup-heavy tracer tests for Study Variable Source Truth."""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

import pytest

from scripts.source_truth.builder import (
    DERIVATION_CATALOG,
    DERIVATION_CLEANUP_LEDGER,
    DERIVATION_PHI_LEDGER,
    SourceTruthBuildError,
    build_source_truth_artifact,
)
from scripts.source_truth.record import validate_record

_FOB_COLUMNS = [
    "SUBJID",
    "FOB_VISDAT",
    "FOB_COHBOUT",
    "FOB_CONTACT_PHONE",
    "FOB_REVIEW_NOTE",
    "SYSTEM_ID",
    "CAPTURE_DEVICE_ID",
    "ROUTING_STATE",
    "IMAGE_FILE_NAME",
    "BATCH_NAME",
    "REMOTE_UPLOAD_ID",
    "ORIGINAL_FILE_NAME",
    "VERIFICATION_STATUS",
    "WORKSTATION_ID",
    "Time_Stamp",
]


def _column_inventory() -> dict[str, Any]:
    return {
        "study": "Indo-VAP",
        "source_file": "98B_FOB.xlsx",
        "source_path": "Indo-VAP/datasets/98B_FOB.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [{"sheet": "_98B_FOB", "columns": list(_FOB_COLUMNS)}],
        "column_count": len(_FOB_COLUMNS),
    }


def _pdf_extraction() -> dict[str, Any]:
    return {
        "page_count": 2,
        "annotation_count": 6,
        "real_annotation_variables": [
            "SUBJID",
            "FOB_VISDAT",
            "FOB_COHBOUT",
            "FOB_CONTACT_PHONE",
            "FOB_REVIEW_NOTE",
            "FOB_HOUSEHOLD_CONTACT_DEFINITION",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "FOB_VISDAT",
                    "FOB_COHBOUT",
                    "FOB_CONTACT_PHONE",
                    "FOB_REVIEW_NOTE",
                    "FOB_HOUSEHOLD_CONTACT_DEFINITION",
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


def _field_policy() -> dict[str, Any]:
    return {
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
                "field_class": "participant_identifier",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "FOB_VISDAT": {
                "action": "jitter_date",
                "reason": "date_field",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "final_outcome_visit",
                "pdf_annotation_status": "direct",
            },
            "FOB_COHBOUT": {
                "action": "keep",
                "reason": "clinical_outcome",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
                "option_set": "fob_cohort_outcome",
            },
            "FOB_CONTACT_PHONE": {
                "action": "drop",
                "reason": "contact_field",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "contact_follow_up",
                "pdf_annotation_status": "direct",
            },
            "FOB_REVIEW_NOTE": {
                "action": "review_required",
                "reason": "ambiguous_free_text_follow_up_note",
                "confidence": "low",
                "field_class": "study_variable",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
            },
            "FOB_HOUSEHOLD_CONTACT_DEFINITION": {
                "action": "keep",
                "reason": "pdf_only_study_context",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "household_contacts",
                "source_kind": "source_only",
                "pdf_annotation_status": "direct",
            },
            "SYSTEM_ID": {
                "action": "drop",
                "reason": "system_metadata",
                "confidence": "high",
                "field_class": "system_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "CAPTURE_DEVICE_ID": {
                "action": "drop",
                "reason": "capture_metadata",
                "confidence": "high",
                "field_class": "capture_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "ROUTING_STATE": {
                "action": "drop",
                "reason": "routing_metadata",
                "confidence": "high",
                "field_class": "routing_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "IMAGE_FILE_NAME": {
                "action": "drop",
                "reason": "image_metadata",
                "confidence": "high",
                "field_class": "image_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "BATCH_NAME": {
                "action": "drop",
                "reason": "batch_metadata",
                "confidence": "high",
                "field_class": "batch_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "REMOTE_UPLOAD_ID": {
                "action": "drop",
                "reason": "remote_metadata",
                "confidence": "high",
                "field_class": "remote_metadata",
                "pdf_annotation_status": "not_annotated",
                "sensitivity_flags": ["remote_identifier"],
            },
            "ORIGINAL_FILE_NAME": {
                "action": "drop",
                "reason": "original_file_metadata",
                "confidence": "high",
                "field_class": "original_file_metadata",
                "pdf_annotation_status": "not_annotated",
                "sensitivity_flags": ["possible_free_text_filename"],
            },
            "VERIFICATION_STATUS": {
                "action": "drop",
                "reason": "verification_metadata",
                "confidence": "high",
                "field_class": "verification_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "WORKSTATION_ID": {
                "action": "drop",
                "reason": "workstation_metadata",
                "confidence": "high",
                "field_class": "workstation_metadata",
                "pdf_annotation_status": "not_annotated",
                "sensitivity_flags": ["workstation_identifier"],
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "timestamp_metadata",
                "confidence": "high",
                "field_class": "timestamp_metadata",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }


def _artifact() -> dict[str, Any]:
    return build_source_truth_artifact(
        _column_inventory(),
        _pdf_extraction(),
        _field_policy(),
    )


def test_98b_fob_records_cover_dataset_columns_and_metadata_classes() -> None:
    records = _artifact()["records"]
    dataset_records = [
        record for record in records if record["presence"]["dataset"]["present"] is True
    ]
    covered = [record["presence"]["dataset"]["column"] for record in dataset_records]

    assert len(dataset_records) == len(_FOB_COLUMNS)
    assert Counter(covered) == Counter(_FOB_COLUMNS)
    for record in records:
        validate_record(record)

    classes = {record["variable_id"]: record["normalized"]["field_class"] for record in records}
    assert classes["FOB_COHBOUT"] == "study_variable"
    assert classes["SYSTEM_ID"] == "system_metadata"
    assert classes["CAPTURE_DEVICE_ID"] == "capture_metadata"
    assert classes["ROUTING_STATE"] == "routing_metadata"
    assert classes["IMAGE_FILE_NAME"] == "image_metadata"
    assert classes["BATCH_NAME"] == "batch_metadata"
    assert classes["REMOTE_UPLOAD_ID"] == "remote_metadata"
    assert classes["ORIGINAL_FILE_NAME"] == "original_file_metadata"
    assert classes["VERIFICATION_STATUS"] == "verification_metadata"
    assert classes["WORKSTATION_ID"] == "workstation_metadata"
    assert classes["Time_Stamp"] == "timestamp_metadata"


def test_98b_fob_separates_cleanup_metadata_from_phi_decisions() -> None:
    records = {record["variable_id"]: record for record in _artifact()["records"]}

    cleanup_columns = [
        "SYSTEM_ID",
        "CAPTURE_DEVICE_ID",
        "ROUTING_STATE",
        "IMAGE_FILE_NAME",
        "BATCH_NAME",
        "REMOTE_UPLOAD_ID",
        "ORIGINAL_FILE_NAME",
        "VERIFICATION_STATUS",
        "WORKSTATION_ID",
        "Time_Stamp",
    ]

    for column in cleanup_columns:
        assert records[column]["derivation_targets"] == [DERIVATION_CLEANUP_LEDGER]

    assert records["FOB_CONTACT_PHONE"]["derivation_targets"] == [DERIVATION_PHI_LEDGER]
    assert records["SUBJID"]["derivation_targets"][-1] == DERIVATION_PHI_LEDGER
    assert records["REMOTE_UPLOAD_ID"]["normalized"]["sensitivity_flags"] == ["remote_identifier"]
    assert records["WORKSTATION_ID"]["normalized"]["sensitivity_flags"] == [
        "workstation_identifier"
    ]


def test_98b_fob_source_only_pdf_fields_are_metadata_not_analysis_bindings() -> None:
    artifact = _artifact()
    records = {record["variable_id"]: record for record in artifact["records"]}

    source_only = records["FOB_HOUSEHOLD_CONTACT_DEFINITION"]

    assert source_only["source_kind"] == "source_only"
    assert source_only["presence"]["dataset"] == {"present": False}
    assert source_only["presence"]["pdf"]["present"] is True
    assert source_only["normalized"]["analysis_queryable"] is False
    assert source_only["derivation_targets"] == [DERIVATION_CATALOG]
    assert source_only["exact_source_wording"]["dataset_column"] is None
    assert "FOB_HOUSEHOLD_CONTACT_DEFINITION" in artifact["completeness"]["pdf_fields_covered"]
    assert artifact["completeness"]["unmatched_pdf_fields"] == []


def test_98b_fob_review_required_fields_and_raw_value_boundary() -> None:
    artifact = _artifact()
    records = {record["variable_id"]: record for record in artifact["records"]}

    review_required = records["FOB_REVIEW_NOTE"]
    assert review_required["source_kind"] == "review_required"
    assert review_required["review_state"] == "review_required"
    assert review_required["derivation_targets"] == []
    assert "FOB_REVIEW_NOTE" in artifact["completeness"]["review_required_fields"]

    column_inventory = copy.deepcopy(_column_inventory())
    column_inventory["sheets"][0]["sample_values"] = ["raw row value must not enter SoT"]

    with pytest.raises(SourceTruthBuildError, match="column_inventory"):
        build_source_truth_artifact(
            column_inventory,
            _pdf_extraction(),
            _field_policy(),
        )
