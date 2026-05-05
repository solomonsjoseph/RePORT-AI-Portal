"""Chat boundary tests for issue #73.

Pin behavior at the boundary between dataset-backed catalog variables,
source-only metadata, dropped variables, and audit-only PHI handling
content. The tests intentionally exercise the catalog/retrieval surface
(``scripts.source_truth.catalog`` + ``scripts.source_truth.retrieval``)
plus the analysis-binding refusal (``scripts.source_truth.analysis_binding``)
because those are the layers a tool description can quote and the LLM
can reason about — there is no hidden keyword router.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.source_truth.analysis_binding import (
    AnalysisBindingError,
    resolve_analysis_bindings,
)
from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import (
    AUDIT_ONLY_NOTE,
    build_catalog_artifact,
)
from scripts.source_truth.dataset_schema import build_dataset_schema
from scripts.source_truth.retrieval import SourceTruthRetriever

# ---------------------------------------------------------------------------
# Fixture: a small source-truth artifact covering all four boundary cases.
# ---------------------------------------------------------------------------
#
#   HIV_HIV               — keep, dataset+pdf      → analysis-queryable
#   HIV_FORM_INSTRUCTION  — keep, pdf-only         → source-only metadata
#   HIV_SIGN              — drop, signature_field  → PHI ledger / dropped
#   SUBJID                — pseudonymize           → audit-only (PHI ledger)


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


def _retriever() -> SourceTruthRetriever:
    catalog = build_catalog_artifact(_source_truth_artifact())
    return SourceTruthRetriever.from_catalog_artifact(catalog)


# ---------------------------------------------------------------------------
# AUDIT_ONLY_NOTE: pin the verbatim text the maintainer fixed in #83.
# ---------------------------------------------------------------------------


def test_audit_only_note_constant_is_pinned_verbatim() -> None:
    assert AUDIT_ONLY_NOTE == (
        "Note: PHI handling decisions are recorded in the study audit ledger "
        "and aren't exposed through normal chat. For audit questions, please "
        "reach out to the project maintainer."
    )


# ---------------------------------------------------------------------------
# Catalog: audit_only flag on retained PHI-handling-ledger entries.
# ---------------------------------------------------------------------------


def test_dataset_backed_keep_record_is_not_audit_only() -> None:
    catalog = build_catalog_artifact(_source_truth_artifact())
    record = next(r for r in catalog["records"] if r["variable_id"] == "HIV_HIV")
    assert record["audit_only"] is False


def test_pseudonymized_record_is_audit_only_and_not_analysis_queryable_for_chat() -> None:
    catalog = build_catalog_artifact(_source_truth_artifact())
    record = next(r for r in catalog["records"] if r["variable_id"] == "SUBJID")
    # audit_only signals the LLM/composer to surface AUDIT_ONLY_NOTE
    assert record["audit_only"] is True


# ---------------------------------------------------------------------------
# Retriever: dataset-backed retained variable answers metadata cleanly,
# without "PHI handling" phrasing or audit-ledger leakage.
# ---------------------------------------------------------------------------


def test_dataset_backed_metadata_question_answers_without_phi_noise() -> None:
    answer = _retriever().answer_chat_question("What is HIV_HIV?")

    assert answer.variable_ids == ["HIV_HIV"]
    assert answer.audit_only is False
    assert answer.analysis_queryable is True
    # Explicit regression: ordinary metadata answers must not say "PHI"
    # / "PHI-handled" / "PHI handling".
    lowered = answer.text.lower()
    assert "phi" not in lowered
    assert "phi-handled" not in lowered
    assert "phi handling" not in lowered
    assert "ledger" not in lowered
    assert "maintainer" not in lowered


# ---------------------------------------------------------------------------
# Retriever: source-only variable — metadata works, but the answer flags
# that it is not analysis-queryable. No audit-ledger leakage.
# ---------------------------------------------------------------------------


def test_source_only_variable_answers_metadata_with_analysis_note() -> None:
    answer = _retriever().answer_chat_question("What is HIV_FORM_INSTRUCTION?")

    assert answer.variable_ids == ["HIV_FORM_INSTRUCTION"]
    assert answer.analysis_queryable is False
    assert answer.audit_only is False
    # The answer should mention that this is metadata-only / not
    # analysis-queryable so the LLM does not surface it as an analyzable
    # variable. We pin a stable substring rather than the full string.
    assert "Note:" in answer.text
    assert "not analysis-queryable" in answer.text.lower()
    # No ledger leak for a source-only variable.
    assert "ledger" not in answer.text.lower()
    assert "maintainer" not in answer.text.lower()


# ---------------------------------------------------------------------------
# Retriever: dropped variable — polite maintainer-contact with NO
# PHI/sensitive classification or ledger details exposed.
# ---------------------------------------------------------------------------


def test_dropped_variable_chat_returns_polite_maintainer_message() -> None:
    answer = _retriever().answer_chat_question("Where is HIV_SIGN recorded?")

    # Dropped variable is absent from the catalog for the chat path.
    assert answer.variable_ids == []
    assert answer.audit_only is False
    assert answer.analysis_queryable is False

    text = answer.text.lower()
    # Polite maintainer-contact message
    assert "maintainer" in text
    # No PHI/sensitive classification or ledger details exposed
    assert "phi" not in text
    assert "sensitive" not in text
    assert "ledger" not in text
    assert "signature" not in text  # don't leak the drop reason


def test_dropped_variable_repeated_chat_remains_polite_and_consistent() -> None:
    retriever = _retriever()
    a = retriever.answer_chat_question("Where is HIV_SIGN recorded?")
    b = retriever.answer_chat_question("Why is HIV_SIGN missing?")
    assert a.text == b.text


# ---------------------------------------------------------------------------
# Retriever: audit-only flagged content — surface the EXACT
# AUDIT_ONLY_NOTE constant when the user's question pulls a PHI-handling
# ledger record into the chat path.
# ---------------------------------------------------------------------------


def test_audit_only_question_emits_exact_audit_only_note_constant() -> None:
    answer = _retriever().answer_chat_question("What is SUBJID?")

    assert answer.variable_ids == ["SUBJID"]
    assert answer.audit_only is True
    assert answer.text == AUDIT_ONLY_NOTE


# ---------------------------------------------------------------------------
# Analysis-binding refusal: the analysis path must respect the audit
# boundary. Source-only and audit-only refuse with a clear message and
# do NOT invoke deterministic keyword routing.
# ---------------------------------------------------------------------------


def test_analysis_binding_refuses_source_only_variable() -> None:
    source_truth = _source_truth_artifact()
    schema = build_dataset_schema(source_truth)
    catalog = build_catalog_artifact(source_truth)

    with pytest.raises(AnalysisBindingError) as exc:
        resolve_analysis_bindings(
            question="distribution of HIV_FORM_INSTRUCTION",
            cohort_id="cohort_a",
            catalog=catalog,
            dataset_schema=schema,
            outcome_variable_id="HIV_FORM_INSTRUCTION",
            predictor_variable_ids=[],
        )

    # The error message must surface the source-only / not-analysis-queryable
    # boundary clearly, without leaking ledger details.
    msg = str(exc.value)
    assert "HIV_FORM_INSTRUCTION" in msg


def test_dropped_variable_analysis_attempt_refuses_without_ledger_leak() -> None:
    source_truth = _source_truth_artifact()
    schema = build_dataset_schema(source_truth)
    catalog = build_catalog_artifact(source_truth)

    with pytest.raises(AnalysisBindingError) as exc:
        resolve_analysis_bindings(
            question="distribution of HIV_SIGN",
            cohort_id="cohort_a",
            catalog=catalog,
            dataset_schema=schema,
            outcome_variable_id="HIV_SIGN",
            predictor_variable_ids=[],
        )

    msg = str(exc.value).lower()
    # No PHI / signature / ledger leak in the analysis refusal
    assert "phi" not in msg
    assert "ledger" not in msg
    assert "signature" not in msg


# ---------------------------------------------------------------------------
# Edge: clarification still wins over audit_only flag.
# ---------------------------------------------------------------------------


def test_ambiguous_match_clarification_wins_over_audit_only_flag() -> None:
    # Build a fixture where two records share search tokens (one of which
    # is audit_only). The retriever should still ask for clarification
    # rather than collapsing to one and surfacing AUDIT_ONLY_NOTE.
    catalog = {
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
                "handling_action": "keep",
                "handling_status": {"action": "keep"},
                "analysis_queryable": True,
                "audit_only": False,
                "options_summary": {"count": 0},
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
                "handling_action": "pseudonymize",
                "handling_status": {"action": "pseudonymize"},
                "analysis_queryable": True,
                "audit_only": True,
                "options_summary": {"count": 0},
                "relationship_summary": {"count": 0, "types": []},
                "evidence_pack_ref": {
                    "artifact_type": "study_variable_evidence_pack",
                    "variable_id": "HIV_STATUS",
                },
            },
        ],
        "evidence_packs": [],
    }
    retriever = SourceTruthRetriever.from_catalog_artifact(catalog)

    answer = retriever.answer_chat_question("What is the HIV variable?")
    assert answer.needs_clarification is True
    # Clarification answer must NOT collapse to the AUDIT_ONLY_NOTE
    assert answer.text != AUDIT_ONLY_NOTE
    assert "Which" in answer.text or "candidates" in answer.text.lower()


# ---------------------------------------------------------------------------
# Regression: no hidden keyword router. Production code in agent_tools.py
# / agent_graph.py must not contain top-level functions that branch tool
# choice based on the user's query string. Validation INSIDE a tool's
# implementation (which the maintainer explicitly permits) is fine.
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_module(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def test_no_top_level_keyword_router_in_agent_graph() -> None:
    text = _read_module("scripts/ai_assistant/agent_graph.py")
    # No function-level routing on the user's query through forbidden patterns.
    forbidden = [
        re.compile(r'if\s+["\']audit["\']\s+in\s+\w+\.lower\(\)'),
        re.compile(r'if\s+["\']phi["\']\s+in\s+\w+\.lower\(\)'),
        re.compile(r"route_by_keywords?\s*\("),
        re.compile(r"force_tool\s*\("),
        re.compile(r"block_tool\s*\("),
    ]
    for pattern in forbidden:
        assert pattern.search(text) is None, (
            f"agent_graph.py contains forbidden routing pattern {pattern.pattern!r}; "
            "the maintainer's #1 constraint forbids deterministic keyword routing."
        )


def test_agent_tools_audit_only_guidance_quotes_constant_verbatim() -> None:
    # The new boundary tool's description must quote the AUDIT_ONLY_NOTE
    # text verbatim so the LLM has the exact phrasing to surface.
    text = _read_module("scripts/ai_assistant/agent_tools.py")
    assert AUDIT_ONLY_NOTE in text, (
        "agent_tools.py must quote AUDIT_ONLY_NOTE verbatim somewhere so "
        "the LLM sees the exact phrasing to use for audit-only deflection."
    )
