"""Dataset Schema sidecar tests derived from Source Truth artifacts."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.dataset_schema import (
    DatasetSchemaError,
    build_dataset_schema,
    get_dataset_schema_status,
    resolve_analysis_binding,
)


def _source_truth_artifact() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "sheets": [
            {
                "sheet": "_6_HIV",
                "columns": ["HIV_HIV"],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": ["HIV_HIV"],
        "annotation_pages": [{"page": 1, "annotations": ["HIV_HIV"]}],
        "option_sets": {
            "hiv_result_pdf": {
                "source": "PDF option text",
                "values": ["Positive (+)", "Negative (-)", "Indeterminate"],
            }
        },
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "fields": {
            "HIV_HIV": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
                "option_set": "hiv_result_pdf",
            }
        },
    }
    return build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)


def test_retained_dataset_variable_resolves_analysis_binding_from_schema() -> None:
    schema = build_dataset_schema(_source_truth_artifact())

    binding = resolve_analysis_binding(schema, "HIV_HIV")

    assert binding["dataset_column"] == "HIV_HIV"
    assert binding["analysis_queryable"] is True
    assert binding["source_truth_ref"] == {
        "artifact_type": "study_variable_source_truth",
        "variable_id": "HIV_HIV",
    }
    assert binding["catalog_ref"] == {
        "status": "pending_catalog_generation",
        "variable_id": "HIV_HIV",
    }


def test_schema_distinguishes_retained_transformed_source_only_dropped_and_review_required() -> (
    None
):
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "sheets": [
            {
                "sheet": "_6_HIV",
                "columns": [
                    "SUBJID",
                    "HIV_HIVDAT",
                    "HIV_HIV",
                    "HIV_SIGN",
                    "HIV_HIVNDOTH",
                ],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "SUBJID",
            "HIV_HIVDAT",
            "HIV_HIV",
            "HIV_SIGN",
            "HIV_HIVNDOTH",
            "HIV_FORM_INSTRUCTION",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "HIV_HIVDAT",
                    "HIV_HIV",
                    "HIV_SIGN",
                    "HIV_HIVNDOTH",
                    "HIV_FORM_INSTRUCTION",
                ],
            }
        ],
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "pdf_annotation_status": "direct",
            },
            "HIV_HIVDAT": {
                "action": "jitter_date",
                "reason": "date_field",
                "confidence": "high",
                "pdf_annotation_status": "direct",
            },
            "HIV_HIV": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "pdf_annotation_status": "direct",
            },
            "HIV_SIGN": {
                "action": "drop",
                "reason": "signature_field",
                "confidence": "high",
                "pdf_annotation_status": "direct",
            },
            "HIV_HIVNDOTH": {
                "action": "review_required",
                "reason": "possible_free_text_reason_child_of_hiv_testing_status",
                "confidence": "low",
                "pdf_annotation_status": "direct",
            },
            "HIV_FORM_INSTRUCTION": {
                "action": "keep",
                "reason": "pdf_only_instruction",
                "confidence": "high",
                "source_kind": "source_only",
                "dataset_present": False,
                "pdf_annotation_status": "direct",
            },
        },
    }

    schema = build_dataset_schema(
        build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)
    )

    assert {entry["variable_id"] for entry in schema["entries"]} == {
        "SUBJID",
        "HIV_HIVDAT",
        "HIV_HIV",
    }
    assert get_dataset_schema_status(schema, "HIV_HIVDAT") == {
        "variable_id": "HIV_HIVDAT",
        "source_truth_dataset_present": True,
        "clean_output_present": True,
        "analysis_queryable": True,
        "handling_action": "jitter_date",
        "review_state": "auto_normalized",
        "source_kind": "matched",
    }
    assert (
        get_dataset_schema_status(schema, "HIV_FORM_INSTRUCTION")["source_truth_dataset_present"]
        is False
    )
    assert get_dataset_schema_status(schema, "HIV_FORM_INSTRUCTION")["analysis_queryable"] is False
    assert get_dataset_schema_status(schema, "HIV_SIGN")["clean_output_present"] is False
    assert get_dataset_schema_status(schema, "HIV_SIGN")["handling_action"] == "drop"
    assert get_dataset_schema_status(schema, "HIV_HIVNDOTH")["review_state"] == "review_required"


@pytest.mark.parametrize("forbidden_key", ["rows", "raw_values", "observed_value_counts"])
def test_schema_generation_rejects_raw_dataset_value_keys(forbidden_key: str) -> None:
    artifact = _source_truth_artifact()
    artifact[forbidden_key] = [{"HIV_HIV": "raw row value"}]

    with pytest.raises(DatasetSchemaError, match="source_truth_artifact"):
        build_dataset_schema(artifact)


@pytest.mark.parametrize(
    "variable_id",
    ["HIV_FORM_INSTRUCTION", "HIV_SIGN", "HIV_HIVNDOTH"],
)
def test_analysis_binding_refuses_non_queryable_source_truth_records(variable_id: str) -> None:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "sheets": [{"sheet": "_6_HIV", "columns": ["HIV_SIGN", "HIV_HIVNDOTH"]}],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "HIV_SIGN",
            "HIV_HIVNDOTH",
            "HIV_FORM_INSTRUCTION",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "HIV_SIGN",
                    "HIV_HIVNDOTH",
                    "HIV_FORM_INSTRUCTION",
                ],
            }
        ],
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "fields": {
            "HIV_SIGN": {
                "action": "drop",
                "reason": "signature_field",
                "confidence": "high",
                "pdf_annotation_status": "direct",
            },
            "HIV_HIVNDOTH": {
                "action": "review_required",
                "reason": "possible_free_text_reason_child_of_hiv_testing_status",
                "confidence": "low",
                "pdf_annotation_status": "direct",
            },
            "HIV_FORM_INSTRUCTION": {
                "action": "keep",
                "reason": "pdf_only_instruction",
                "confidence": "high",
                "source_kind": "source_only",
                "dataset_present": False,
                "pdf_annotation_status": "direct",
            },
        },
    }
    schema = build_dataset_schema(
        build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)
    )

    with pytest.raises(DatasetSchemaError):
        resolve_analysis_binding(schema, variable_id)
