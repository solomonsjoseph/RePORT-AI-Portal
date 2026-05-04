"""Catalog and evidence-pack behavior derived from Source Truth artifacts."""

from __future__ import annotations

from typing import Any

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.dataset_schema import build_dataset_schema


def _source_truth_artifact() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "sheets": [
            {
                "sheet": "_6_HIV",
                "columns": ["HIV_HIV", "SUBJID", "HIV_SIGN"],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "HIV_HIV",
            "SUBJID",
            "HIV_SIGN",
            "HIV_FORM_INSTRUCTION",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "HIV_HIV",
                    "SUBJID",
                    "HIV_SIGN",
                    "HIV_FORM_INSTRUCTION",
                ],
            }
        ],
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
        "source_pdf": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf",
        "fields": {
            "HIV_HIV": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
                "option_set": "hiv_result_pdf",
                "relationships": [
                    {
                        "type": "result_field",
                        "parent_variable_id": "HIV_HIVND",
                        "basis": "pdf_option_group",
                    }
                ],
            },
            "SUBJID": {
                "action": "pseudonymize",
                "label": "SUBJ",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
                "sensitivity_flags": ["direct_identifier"],
            },
            "HIV_SIGN": {
                "action": "drop",
                "reason": "signature_field",
                "confidence": "high",
                "section": "completion",
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
    return build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)


def _records_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["variable_id"]: record for record in records}


def test_retained_current_dataset_variables_build_light_catalog_records() -> None:
    catalog = build_catalog_artifact(_source_truth_artifact())
    records = _records_by_id(catalog["records"])

    assert set(records) == {"HIV_HIV", "SUBJID"}
    assert (
        records["HIV_HIV"]
        | {
            "variable_id": "HIV_HIV",
            "label": "hiv_hiv",
            "display_label": "hiv_hiv",
            "normalized_meaning": "hiv_hiv",
            "dataset_column": "HIV_HIV",
            "source_kind": "matched",
            "review_state": "auto_normalized",
            "catalog_tier": "variable",
            "handling_action": "keep",
            "handling_reason": "direct_pdf_annotated_clinical_or_categorical_field",
            "handling_status": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
            },
            "analysis_queryable": True,
            "section": "hiv_fields",
            "form": "6_HIV.xlsx",
            "source_presence": {"dataset": True, "pdf": True, "dictionary": False},
            "search_terms": ["hiv", "fields"],
            "options_summary": {"count": 3, "option_set": "hiv_result_pdf"},
            "relationship_summary": {"count": 1, "types": ["result_field"]},
            "source_truth_ref": {
                "artifact_type": "study_variable_source_truth",
                "study": "Indo-VAP",
                "source_file": "6_HIV.xlsx",
                "variable_id": "HIV_HIV",
            },
            "evidence_pack_ref": {
                "artifact_type": "study_variable_evidence_pack",
                "variable_id": "HIV_HIV",
            },
        }
        == records["HIV_HIV"]
    )
    assert records["SUBJID"]["handling_action"] == "pseudonymize"
    assert records["SUBJID"]["handling_reason"] == "participant_identifier"
    assert records["SUBJID"]["sensitivity_flags"] == ["direct_identifier"]


def test_source_only_variables_get_non_queryable_evidence_but_no_catalog_record() -> None:
    catalog = build_catalog_artifact(_source_truth_artifact())
    records = _records_by_id(catalog["records"])
    packs = _records_by_id(catalog["evidence_packs"])

    assert "HIV_FORM_INSTRUCTION" not in records
    assert packs["HIV_FORM_INSTRUCTION"]["analysis_queryable"] is False
    assert packs["HIV_FORM_INSTRUCTION"]["source_kind"] == "source_only"
    assert packs["HIV_FORM_INSTRUCTION"]["catalog_ref"] == {
        "status": "not_in_compact_catalog",
        "variable_id": "HIV_FORM_INSTRUCTION",
    }


def test_dropped_variables_are_excluded_from_catalog_and_evidence_packs() -> None:
    catalog = build_catalog_artifact(_source_truth_artifact())

    assert "HIV_SIGN" not in _records_by_id(catalog["records"])
    assert "HIV_SIGN" not in _records_by_id(catalog["evidence_packs"])
    assert catalog["excluded_records"]["HIV_SIGN"] == {
        "reason": "not_catalog_target",
        "handling_action": "drop",
        "source_kind": "matched",
        "analysis_queryable": False,
    }


def test_compact_catalog_omits_heavy_exact_wording_kept_in_evidence_pack() -> None:
    catalog = build_catalog_artifact(_source_truth_artifact())
    compact = _records_by_id(catalog["records"])["HIV_HIV"]
    pack = _records_by_id(catalog["evidence_packs"])["HIV_HIV"]

    assert "exact_source_wording" not in compact
    assert "source_references" not in compact
    assert "source_defined_options" not in compact
    assert pack["exact_source_wording"]["pdf_options"] == [
        "Positive (+)",
        "Negative (-)",
        "Indeterminate",
    ]
    assert pack["source_references"]["pdf"]["annotation_pages"] == [1]
    assert pack["relationships"] == [
        {
            "type": "result_field",
            "parent_variable_id": "HIV_HIVND",
            "basis": "pdf_option_group",
        }
    ]
    assert pack["normalization_trace"] == {
        "label": "hiv_hiv",
        "confidence": "high",
        "normalization_basis": "dataset_column_code_lowercased",
        "handling_action": "keep",
        "handling_reason": "direct_pdf_annotated_clinical_or_categorical_field",
        "section": "hiv_fields",
        "option_set": "hiv_result_pdf",
    }


def test_dataset_schema_entries_link_to_catalog_records_and_evidence_packs() -> None:
    source_truth = _source_truth_artifact()
    schema = build_dataset_schema(source_truth)
    catalog = build_catalog_artifact(source_truth, dataset_schema=schema)
    records = _records_by_id(catalog["records"])
    pack = _records_by_id(catalog["evidence_packs"])["HIV_HIV"]

    assert records["HIV_HIV"]["dataset_schema_ref"] == {
        "artifact_type": "study_dataset_schema",
        "variable_id": "HIV_HIV",
    }
    assert pack["catalog_ref"] == {
        "artifact_type": "study_variable_catalog",
        "variable_id": "HIV_HIV",
    }
    assert pack["dataset_schema_ref"] == {
        "artifact_type": "study_dataset_schema",
        "variable_id": "HIV_HIV",
    }
    assert catalog["dataset_schema_links"]["HIV_HIV"] == {
        "dataset_schema_ref": {
            "artifact_type": "study_dataset_schema",
            "variable_id": "HIV_HIV",
        },
        "catalog_ref": {
            "artifact_type": "study_variable_catalog",
            "variable_id": "HIV_HIV",
        },
        "evidence_pack_ref": {
            "artifact_type": "study_variable_evidence_pack",
            "variable_id": "HIV_HIV",
        },
    }
