"""Catalog and evidence-pack behavior derived from Source Truth artifacts."""

from __future__ import annotations

from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import (
    SourceTruthCatalogError,
    build_catalog_artifact,
    build_study_design_catalog,
)
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


# ---------------------------------------------------------------------------
# Study-design catalog cards (criteria, schedule, specimen/test, form, cohort)
# ---------------------------------------------------------------------------


def _study_design_input() -> dict[str, Any]:
    return {
        "study": "Indo-VAP",
        "source_truth_ref": {
            "artifact_type": "study_variable_source_truth",
            "study": "Indo-VAP",
        },
        "criteria": [
            {
                "card_id": "INCL_AGE_18_65",
                "criteria_type": "inclusion",
                "label": "Age 18-65 at consent",
                "cohort": "Indo-VAP main",
                "population": "adults",
                "confidence": "high",
                "review_state": "auto_normalized",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/protocol.pdf", "annotation_pages": [3]}
                },
            },
            {
                "card_id": "EXCL_PRIOR_TB",
                "criteria_type": "exclusion",
                "label": "Prior TB treatment",
                "cohort": "Indo-VAP main",
                "population": "adults",
                "confidence": "medium",
                "review_state": "review_required",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/protocol.pdf", "annotation_pages": [4]}
                },
            },
        ],
        "schedule": [
            {
                "card_id": "VISIT_BASELINE",
                "visit_name": "Baseline",
                "label": "Baseline visit",
                "timing": "Day 0",
                "forms_completed": ["6_HIV", "98B_FOB"],
                "specimens_collected": ["whole_blood"],
                "tests_performed": ["HIV_RAPID"],
                "review_state": "auto_normalized",
                "source_references": {"pdf": {"file": "Indo-VAP/sov.pdf", "annotation_pages": [1]}},
            }
        ],
        "specimens_tests": [
            {
                "card_id": "SPEC_WHOLE_BLOOD",
                "specimen_type": "whole_blood",
                "label": "Whole blood specimen",
                "tests": ["HIV_RAPID", "CBC"],
                "timeline": ["Baseline", "Month 2"],
                "related_variables": ["HIV_HIV"],
                "review_state": "auto_normalized",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/lab_manual.pdf", "annotation_pages": [7]}
                },
            }
        ],
        "forms": [
            {
                "card_id": "FORM_6_HIV",
                "form_id": "6_HIV",
                "label": "Form 6: HIV",
                "review_state": "auto_normalized",
                "source_references": {"pdf": {"file": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf"}},
            }
        ],
        "cohorts": [
            {
                "card_id": "COHORT_INDOVAP_MAIN",
                "cohort_id": "Indo-VAP main",
                "label": "Indo-VAP main cohort",
                "review_state": "auto_normalized",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/protocol.pdf", "annotation_pages": [1]}
                },
            }
        ],
    }


def _design_records_by_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["card_id"]: record for record in catalog["records"]}


def test_study_design_catalog_emits_one_record_per_card_input() -> None:
    catalog = build_study_design_catalog(_study_design_input())

    records = _design_records_by_id(catalog)
    assert set(records) == {
        "INCL_AGE_18_65",
        "EXCL_PRIOR_TB",
        "VISIT_BASELINE",
        "SPEC_WHOLE_BLOOD",
        "FORM_6_HIV",
        "COHORT_INDOVAP_MAIN",
    }
    tiers = {record["card_id"]: record["catalog_tier"] for record in catalog["records"]}
    assert tiers == {
        "INCL_AGE_18_65": "criteria",
        "EXCL_PRIOR_TB": "criteria",
        "VISIT_BASELINE": "schedule",
        "SPEC_WHOLE_BLOOD": "specimen_test",
        "FORM_6_HIV": "form",
        "COHORT_INDOVAP_MAIN": "cohort",
    }
    assert catalog["artifact_type"] == "study_design_catalog"
    assert catalog["study"] == "Indo-VAP"


def test_study_design_criteria_card_carries_required_review_metadata() -> None:
    catalog = build_study_design_catalog(_study_design_input())
    inclusion = _design_records_by_id(catalog)["INCL_AGE_18_65"]

    assert inclusion["criteria_type"] == "inclusion"
    assert inclusion["cohort"] == "Indo-VAP main"
    assert inclusion["population"] == "adults"
    assert inclusion["confidence"] == "high"
    assert inclusion["review_state"] == "auto_normalized"
    assert inclusion["source_references"]["pdf"]["annotation_pages"] == [3]
    assert "age" in inclusion["search_terms"]
    assert "inclusion" in inclusion["search_terms"]


def test_study_design_schedule_card_lists_forms_specimens_and_tests() -> None:
    catalog = build_study_design_catalog(_study_design_input())
    visit = _design_records_by_id(catalog)["VISIT_BASELINE"]

    assert visit["catalog_tier"] == "schedule"
    assert visit["visit_name"] == "Baseline"
    assert visit["timing"] == "Day 0"
    assert visit["forms_completed"] == ["6_HIV", "98B_FOB"]
    assert visit["specimens_collected"] == ["whole_blood"]
    assert visit["tests_performed"] == ["HIV_RAPID"]
    assert visit["source_references"]["pdf"]["annotation_pages"] == [1]
    assert "baseline" in visit["search_terms"]


def test_study_design_specimen_test_card_links_to_related_variables() -> None:
    catalog = build_study_design_catalog(_study_design_input())
    specimen = _design_records_by_id(catalog)["SPEC_WHOLE_BLOOD"]

    assert specimen["catalog_tier"] == "specimen_test"
    assert specimen["specimen_type"] == "whole_blood"
    assert specimen["tests"] == ["HIV_RAPID", "CBC"]
    assert specimen["timeline"] == ["Baseline", "Month 2"]
    assert specimen["related_variables"] == ["HIV_HIV"]
    assert "blood" in specimen["search_terms"]


def test_study_design_form_and_cohort_cards_are_present() -> None:
    catalog = build_study_design_catalog(_study_design_input())
    records = _design_records_by_id(catalog)

    form = records["FORM_6_HIV"]
    assert form["catalog_tier"] == "form"
    assert form["form_id"] == "6_HIV"
    assert form["label"] == "Form 6: HIV"
    assert "hiv" in form["search_terms"]

    cohort = records["COHORT_INDOVAP_MAIN"]
    assert cohort["catalog_tier"] == "cohort"
    assert cohort["cohort_id"] == "Indo-VAP main"
    assert "cohort" in cohort["search_terms"]


def test_study_design_card_rejects_missing_required_keys() -> None:
    bad = _study_design_input()
    # criteria card missing `criteria_type`
    bad["criteria"][0].pop("criteria_type")
    with pytest.raises(SourceTruthCatalogError, match="criteria_type"):
        build_study_design_catalog(bad)


def test_study_design_card_rejects_invalid_review_state() -> None:
    bad = _study_design_input()
    bad["forms"][0]["review_state"] = "not_a_valid_state"
    with pytest.raises(SourceTruthCatalogError, match="review_state"):
        build_study_design_catalog(bad)


def test_study_design_card_rejects_forbidden_raw_value_keys() -> None:
    bad = _study_design_input()
    bad["criteria"][0]["observed_values"] = ["whatever"]
    with pytest.raises(SourceTruthCatalogError, match="observed_values"):
        build_study_design_catalog(bad)


def test_study_design_card_rejects_invalid_criteria_type() -> None:
    bad = _study_design_input()
    bad["criteria"][0]["criteria_type"] = "maybe"
    with pytest.raises(SourceTruthCatalogError, match="criteria_type"):
        build_study_design_catalog(bad)


def test_study_design_card_rejects_duplicate_card_ids() -> None:
    bad = _study_design_input()
    bad["forms"].append(dict(bad["forms"][0]))
    with pytest.raises(SourceTruthCatalogError, match="duplicate"):
        build_study_design_catalog(bad)


def test_study_design_review_required_state_propagates() -> None:
    catalog = build_study_design_catalog(_study_design_input())
    exclusion = _design_records_by_id(catalog)["EXCL_PRIOR_TB"]
    assert exclusion["review_state"] == "review_required"


def test_study_design_card_without_source_references_is_rejected() -> None:
    bad = _study_design_input()
    bad["criteria"][0].pop("source_references")
    with pytest.raises(SourceTruthCatalogError, match="source_references"):
        build_study_design_catalog(bad)
