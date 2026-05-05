"""Retrieval and answer composition over Source Truth catalog artifacts."""

from __future__ import annotations

from typing import Any, cast

from scripts.source_truth.retrieval import SourceTruthRetriever


def _catalog_artifact() -> dict[str, Any]:
    return {
        "artifact_type": "study_variable_catalog",
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "records": [
            {
                "variable_id": "HIV_HIV",
                "label": "hiv result",
                "display_label": "HIV result",
                "normalized_meaning": "HIV result",
                "search_terms": ["hiv", "result"],
                "dataset_column": "HIV_HIV",
                "form": "6_HIV.xlsx",
                "source_presence": {"dataset": True, "pdf": True, "dictionary": False},
                "handling_action": "keep",
                "handling_status": {
                    "action": "keep",
                    "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                },
                "analysis_queryable": True,
                "options_summary": {"count": 3, "option_set": "hiv_result_pdf"},
                "relationship_summary": {"count": 0, "types": []},
                "evidence_pack_ref": {
                    "artifact_type": "study_variable_evidence_pack",
                    "variable_id": "HIV_HIV",
                },
            },
            {
                "variable_id": "HIV_STATUS",
                "label": "hiv status",
                "display_label": "HIV status",
                "normalized_meaning": "HIV status",
                "search_terms": ["hiv", "status"],
                "dataset_column": "HIV_STATUS",
                "form": "6_HIV.xlsx",
                "handling_action": "keep",
                "handling_status": {"action": "keep"},
                "analysis_queryable": True,
                "options_summary": {"count": 2},
                "relationship_summary": {"count": 0, "types": []},
                "evidence_pack_ref": {
                    "artifact_type": "study_variable_evidence_pack",
                    "variable_id": "HIV_STATUS",
                },
            },
        ],
        "evidence_packs": [
            {
                "artifact_type": "study_variable_evidence_pack",
                "variable_id": "HIV_HIV",
                "exact_source_wording": {
                    "dataset_column": "HIV_HIV",
                    "pdf_options": ["Positive (+)", "Negative (-)", "Indeterminate"],
                },
                "source_references": {"pdf": {"annotation_pages": [1]}},
                "normalization_trace": {
                    "confidence": "high",
                    "normalization_basis": "dataset_column_code_lowercased",
                },
            },
            {
                "artifact_type": "study_variable_evidence_pack",
                "variable_id": "HIV_STATUS",
                "exact_source_wording": {"dataset_column": "HIV_STATUS"},
                "source_references": {"pdf": {"annotation_pages": [2]}},
                "normalization_trace": {"confidence": "medium"},
            },
        ],
    }


def test_metadata_answer_uses_compact_catalog_without_ledger_or_presence_noise() -> None:
    calls: list[str] = []
    retriever = SourceTruthRetriever.from_catalog_artifact(
        _catalog_artifact(), evidence_pack_loader=calls.append
    )

    answer = retriever.answer_metadata_question("What is HIV_HIV?")

    assert answer.needs_clarification is False
    assert calls == []
    assert answer.variable_ids == ["HIV_HIV"]
    assert answer.text == (
        "HIV_HIV is HIV result. It maps to dataset column HIV_HIV on 6_HIV.xlsx. "
        "Handling: keep. Options: 3 defined in hiv_result_pdf."
    )
    assert "source_presence" not in answer.text
    assert "ledger" not in answer.text.lower()
    assert "dictionary" not in answer.text.lower()


def test_exact_wording_question_lazily_loads_only_the_needed_evidence_pack() -> None:
    loaded: list[str] = []
    raw_packs = _catalog_artifact()["evidence_packs"]
    assert isinstance(raw_packs, list)
    packs = {
        str(pack["variable_id"]): cast(dict[str, Any], pack)
        for pack in raw_packs
        if isinstance(pack, dict)
    }

    def load_pack(variable_id: str) -> dict[str, Any]:
        loaded.append(variable_id)
        return packs[variable_id]

    artifact = _catalog_artifact()
    artifact.pop("evidence_packs")
    retriever = SourceTruthRetriever.from_catalog_artifact(
        artifact,
        evidence_pack_loader=load_pack,
    )

    answer = retriever.answer_metadata_question("What are the exact source options for HIV_HIV?")

    assert loaded == ["HIV_HIV"]
    assert answer.text == (
        "HIV_HIV is HIV result. It maps to dataset column HIV_HIV on 6_HIV.xlsx. "
        "Handling: keep. Options: Positive (+), Negative (-), Indeterminate. "
        "Provenance: PDF page 1."
    )


def test_ambiguous_variable_matches_ask_for_clarification_without_loading_evidence() -> None:
    calls: list[str] = []
    retriever = SourceTruthRetriever.from_catalog_artifact(
        _catalog_artifact(), evidence_pack_loader=calls.append
    )

    answer = retriever.answer_metadata_question("What is the HIV variable?")

    assert calls == []
    assert answer.needs_clarification is True
    assert answer.variable_ids == ["HIV_HIV", "HIV_STATUS"]
    assert answer.text == (
        "Which HIV variable do you mean? Candidates: HIV_HIV (HIV result), HIV_STATUS (HIV status)."
    )


# ---------------------------------------------------------------------------
# Study-design (criteria/schedule/specimen-test/form/cohort) card retrieval
# ---------------------------------------------------------------------------


def _study_design_catalog() -> dict[str, Any]:
    return {
        "artifact_type": "study_design_catalog",
        "study": "Indo-VAP",
        "records": [
            {
                "card_id": "INCL_AGE_18_65",
                "catalog_tier": "criteria",
                "criteria_type": "inclusion",
                "label": "Age 18-65 at consent",
                "display_label": "Age 18-65 years at consent",
                "cohort": "Indo-VAP main",
                "population": "adults",
                "search_terms": ["age", "inclusion", "criteria", "adult"],
                "confidence": "high",
                "review_state": "auto_normalized",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/protocol.pdf", "annotation_pages": [3]}
                },
            },
            {
                "card_id": "EXCL_PRIOR_TB",
                "catalog_tier": "criteria",
                "criteria_type": "exclusion",
                "label": "Prior TB treatment",
                "display_label": "Prior TB treatment exclusion",
                "cohort": "Indo-VAP main",
                "population": "adults",
                "search_terms": ["prior", "tb", "treatment", "exclusion", "criteria"],
                "confidence": "medium",
                "review_state": "review_required",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/protocol.pdf", "annotation_pages": [4]}
                },
            },
            {
                "card_id": "VISIT_BASELINE",
                "catalog_tier": "schedule",
                "visit_name": "Baseline",
                "label": "Baseline visit",
                "display_label": "Baseline visit (Day 0)",
                "timing": "Day 0",
                "forms_completed": ["6_HIV", "98B_FOB"],
                "specimens_collected": ["whole_blood"],
                "tests_performed": ["HIV_RAPID"],
                "search_terms": ["baseline", "visit", "schedule", "day"],
                "review_state": "auto_normalized",
                "source_references": {"pdf": {"file": "Indo-VAP/sov.pdf", "annotation_pages": [1]}},
            },
            {
                "card_id": "SPEC_WHOLE_BLOOD",
                "catalog_tier": "specimen_test",
                "specimen_type": "whole_blood",
                "label": "Whole blood specimen",
                "display_label": "Whole blood specimen",
                "tests": ["HIV_RAPID", "CBC"],
                "timeline": ["Baseline", "Month 2"],
                "related_variables": ["HIV_HIV"],
                "search_terms": [
                    "whole",
                    "blood",
                    "specimen",
                    "hiv",
                    "rapid",
                    "test",
                    "cbc",
                ],
                "review_state": "auto_normalized",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/lab_manual.pdf", "annotation_pages": [7]}
                },
            },
            {
                "card_id": "FORM_6_HIV",
                "catalog_tier": "form",
                "form_id": "6_HIV",
                "label": "Form 6: HIV",
                "display_label": "Form 6 HIV",
                "search_terms": ["form", "hiv", "6"],
                "review_state": "auto_normalized",
                "source_references": {"pdf": {"file": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf"}},
            },
            {
                "card_id": "COHORT_INDOVAP_MAIN",
                "catalog_tier": "cohort",
                "cohort_id": "Indo-VAP main",
                "label": "Indo-VAP main cohort",
                "display_label": "Indo-VAP main cohort",
                "search_terms": ["cohort", "indo", "vap", "main"],
                "review_state": "auto_normalized",
                "source_references": {
                    "pdf": {"file": "Indo-VAP/protocol.pdf", "annotation_pages": [1]}
                },
            },
        ],
    }


def test_retrieval_returns_inclusion_criteria_card_for_criteria_question() -> None:
    retriever = SourceTruthRetriever.from_catalog_artifact(_study_design_catalog())

    cards = retriever.retrieve_cards("What are the age inclusion criteria?", limit=3)

    assert cards, "expected at least one matching card"
    assert cards[0]["card_id"] == "INCL_AGE_18_65"
    assert cards[0]["catalog_tier"] == "criteria"


def test_retrieval_returns_schedule_card_for_visit_question() -> None:
    retriever = SourceTruthRetriever.from_catalog_artifact(_study_design_catalog())

    cards = retriever.retrieve_cards("What forms are completed at the baseline visit?")

    assert cards
    assert cards[0]["card_id"] == "VISIT_BASELINE"
    assert cards[0]["catalog_tier"] == "schedule"


def test_retrieval_returns_specimen_test_card_for_specimen_question() -> None:
    retriever = SourceTruthRetriever.from_catalog_artifact(_study_design_catalog())

    cards = retriever.retrieve_cards("Which tests run on whole blood specimens?")

    assert cards
    assert cards[0]["card_id"] == "SPEC_WHOLE_BLOOD"
    assert cards[0]["catalog_tier"] == "specimen_test"


def test_retrieval_returns_form_card_for_form_overview_question() -> None:
    retriever = SourceTruthRetriever.from_catalog_artifact(_study_design_catalog())

    cards = retriever.retrieve_cards("Tell me about Form 6")

    assert cards
    assert cards[0]["card_id"] == "FORM_6_HIV"
    assert cards[0]["catalog_tier"] == "form"


def test_retrieval_returns_cohort_card_for_cohort_question() -> None:
    retriever = SourceTruthRetriever.from_catalog_artifact(_study_design_catalog())

    cards = retriever.retrieve_cards("What is the Indo-VAP main cohort?")

    assert cards
    assert cards[0]["card_id"] == "COHORT_INDOVAP_MAIN"
    assert cards[0]["catalog_tier"] == "cohort"


def test_retrieval_filters_criteria_by_cohort_when_specified() -> None:
    retriever = SourceTruthRetriever.from_catalog_artifact(_study_design_catalog())

    cards = retriever.retrieve_cards("Indo-VAP main cohort exclusion criteria", limit=5)

    # The exclusion criteria card for the main cohort should rank first
    assert cards
    assert cards[0]["card_id"] == "EXCL_PRIOR_TB"


def test_retrieval_card_ids_used_in_answer_when_no_variable_id() -> None:
    retriever = SourceTruthRetriever.from_catalog_artifact(_study_design_catalog())

    answer = retriever.answer_metadata_question("What forms are completed at the baseline visit?")

    assert answer.variable_ids == ["VISIT_BASELINE"]
    assert "Baseline" in answer.text or "baseline" in answer.text.lower()
