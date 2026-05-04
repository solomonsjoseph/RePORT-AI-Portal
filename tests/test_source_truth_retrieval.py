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
