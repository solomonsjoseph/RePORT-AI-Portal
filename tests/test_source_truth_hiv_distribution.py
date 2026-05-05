"""End-to-end tracer: HIV distribution via catalog + Dataset Schema + analysis runner.

Issue #74 — first analysis tracer bullet. The user asks for the distribution
of HIV test results; the system must:

1. Retrieve HIV concept/variable cards from the compact catalog.
2. Validate via the Dataset Schema (not the old manual binding path) that
   the selected variable is present, source-referenced, and analysis-queryable
   for descriptive analysis.
3. Run a categorical distribution against observed values from the current
   dataset, returning counts and percentages.
4. Never treat source-defined option values as observed unless the analysis
   runner saw them in the data.
5. Return a concise dataset-style response with source references.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.dataset_schema import build_dataset_schema
from scripts.source_truth.distribution import (
    DistributionRequestError,
    run_categorical_distribution,
)

_HIV_PDF_OPTIONS = ["Positive (+)", "Negative (-)", "Indeterminate"]


def _hiv_source_truth_artifact() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "sheets": [
            {
                "sheet": "_6_HIV",
                "columns": ["SUBJID", "HIV_HIV", "HIV_SIGN"],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "SUBJID",
            "HIV_HIV",
            "HIV_SIGN",
            "HIV_FORM_INSTRUCTION",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "HIV_HIV",
                    "HIV_SIGN",
                    "HIV_FORM_INSTRUCTION",
                ],
            }
        ],
        "option_sets": {
            "hiv_result_pdf": {
                "source": "PDF option text",
                "values": list(_HIV_PDF_OPTIONS),
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
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
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


def _bundle() -> dict[str, Any]:
    source_truth = _hiv_source_truth_artifact()
    schema = build_dataset_schema(source_truth)
    catalog = build_catalog_artifact(source_truth, dataset_schema=schema)
    return {"source_truth": source_truth, "schema": schema, "catalog": catalog}


def test_hiv_distribution_retrieves_catalog_validates_schema_and_returns_counts() -> None:
    bundle = _bundle()
    # Observed: only two of the three source-defined options actually appear,
    # plus a missing/None record. The runner must report only the observed
    # values — it must NOT synthesize a "0" count for "Indeterminate" from
    # the catalog's option list.
    observed = ["Positive (+)", "Negative (-)", "Positive (+)", None]

    response = run_categorical_distribution(
        question="What is the distribution of HIV test results?",
        catalog=bundle["catalog"],
        dataset_schema=bundle["schema"],
        observed_values=observed,
    )

    # Catalog cards retrieved for the HIV concept.
    assert "HIV_HIV" in response["variable_ids"]

    # Schema-driven (not manual) confirmation of presence and queryability.
    validation = response["validation"]
    assert validation["variable_id"] == "HIV_HIV"
    assert validation["present_in_dataset"] is True
    assert validation["analysis_queryable"] is True
    assert validation["allowed_for_descriptive"] is True
    assert validation["binding_source"] == "dataset_schema"
    assert validation["has_source_references"] is True

    # Counts and percentages from the analysis runner.
    distribution = response["distribution"]
    assert distribution["variable_id"] == "HIV_HIV"
    assert distribution["n_total"] == 4
    assert distribution["n_valid"] == 3
    assert distribution["n_missing"] == 1

    counts = {row["value"]: row["count"] for row in distribution["categories"]}
    percents = {row["value"]: row["percent"] for row in distribution["categories"]}
    assert counts == {"Positive (+)": 2, "Negative (-)": 1}
    assert percents["Positive (+)"] == pytest.approx(66.67, abs=0.01)
    assert percents["Negative (-)"] == pytest.approx(33.33, abs=0.01)

    # Source-defined options are NOT treated as observed: "Indeterminate"
    # is in the catalog's option list but absent from the data and must not
    # leak into the runner output.
    observed_values = [row["value"] for row in distribution["categories"]]
    assert "Indeterminate" not in observed_values

    # The catalog's source-defined options are reported separately, clearly
    # marked as not-observed evidence rather than counted distribution rows.
    source_defined = response["source_defined_options"]
    assert source_defined == _HIV_PDF_OPTIONS

    # Source references travel with the response.
    refs = response["source_references"]
    assert refs["variable_id"] == "HIV_HIV"
    assert refs["dataset_column"] == "HIV_HIV"
    assert refs["form"] == "6_HIV.xlsx"
    assert refs["pdf_pages"] == [1]
    assert refs["catalog_ref"] == {
        "artifact_type": "study_variable_catalog",
        "variable_id": "HIV_HIV",
    }
    assert refs["dataset_schema_ref"] == {
        "artifact_type": "study_dataset_schema",
        "variable_id": "HIV_HIV",
    }

    # Concise, dataset-flavored summary text mentioning the form and provenance.
    summary = response["summary"]
    assert "HIV_HIV" in summary
    assert "6_HIV.xlsx" in summary
    assert "Positive (+)" in summary
    assert "Negative (-)" in summary
    assert "Indeterminate" not in summary
    assert "PDF page 1" in summary


def test_distribution_request_for_variable_absent_from_dataset_schema_fails_validation() -> None:
    """A retrieval target that is not present/queryable in the schema must fail validation."""
    bundle = _bundle()

    # HIV_FORM_INSTRUCTION is a source-only PDF concept — present in the
    # catalog as evidence pack, but NOT in the dataset schema's queryable
    # entries. Asking for its distribution must surface a validation failure
    # rather than silently running the analysis.
    with pytest.raises(DistributionRequestError) as exc_info:
        run_categorical_distribution(
            question="Distribution of HIV_FORM_INSTRUCTION",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            observed_values=["whatever"],
            variable_id="HIV_FORM_INSTRUCTION",
        )

    message = str(exc_info.value)
    assert "HIV_FORM_INSTRUCTION" in message
    assert "dataset" in message.lower()


def test_distribution_request_for_dropped_variable_fails_validation() -> None:
    """A dropped-by-policy variable (HIV_SIGN) is not analysis-queryable."""
    bundle = _bundle()

    with pytest.raises(DistributionRequestError) as exc_info:
        run_categorical_distribution(
            question="Distribution of HIV_SIGN",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            observed_values=["sig_a", "sig_b"],
            variable_id="HIV_SIGN",
        )

    message = str(exc_info.value)
    assert "HIV_SIGN" in message


def test_question_with_no_catalog_match_raises_distribution_error() -> None:
    """A question that matches no catalog tokens cannot bind a variable."""
    bundle = _bundle()

    with pytest.raises(DistributionRequestError) as exc_info:
        run_categorical_distribution(
            question="distribution of nonsense_xyz unrelated_token",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            observed_values=["a", "b"],
        )

    assert "catalog" in str(exc_info.value).lower()


def test_ambiguous_hiv_query_with_multiple_concept_cards_asks_for_clarification() -> None:
    """When the catalog returns multiple equally good HIV matches, ask for
    clarification rather than guessing which one to analyze."""
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "sheets": [{"sheet": "_6_HIV", "columns": ["HIV_HIV", "HIV_STATUS"]}],
    }
    pdf_extraction = {
        "real_annotation_variables": ["HIV_HIV", "HIV_STATUS"],
        "annotation_pages": [
            {"page": 1, "annotations": ["HIV_HIV", "HIV_STATUS"]},
        ],
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
            },
            "HIV_STATUS": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
            },
        },
    }
    source_truth = build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)
    schema = build_dataset_schema(source_truth)
    catalog = build_catalog_artifact(source_truth, dataset_schema=schema)

    response = run_categorical_distribution(
        question="What is the distribution of the HIV variable?",
        catalog=catalog,
        dataset_schema=schema,
        observed_values=["Positive (+)"],
    )

    assert response["needs_clarification"] is True
    assert set(response["variable_ids"]) == {"HIV_HIV", "HIV_STATUS"}
    assert "distribution" not in response
    assert "Which" in response["summary"]
