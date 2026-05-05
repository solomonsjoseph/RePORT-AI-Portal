"""Behavior tests for pre-extracted PDF evidence capture."""

from __future__ import annotations

from typing import Any, cast

from scripts.source_truth.pdf_evidence import (
    build_pdf_evidence_completeness_report,
    check_pdf_evidence_completeness,
    extract_pdf_evidence,
)


def test_extract_pdf_evidence_captures_visible_form_semantics() -> None:
    pages: list[dict[str, Any]] = [
        {
            "page": 1,
            "lines": [
                {"text": "SECTION B: HIV testing", "role": "section"},
                {
                    "text": "B1. Has participant ever had an HIV test?",
                    "role": "question",
                    "field": "HIV_TEST_DONE",
                },
                {"text": "Yes", "role": "option", "parent": "HIV_TEST_DONE"},
                {"text": "No", "role": "option", "parent": "HIV_TEST_DONE"},
                {
                    "text": "If No, skip to Section C.",
                    "role": "skip",
                    "parent": "HIV_TEST_DONE",
                },
                {
                    "text": "Date of most recent test (DD/MM/YYYY)",
                    "role": "date_hint",
                    "parent": "HIV_TEST_DONE",
                },
                {
                    "text": "B2. CD4 count",
                    "role": "test",
                    "field": "CD4_COUNT",
                },
                {"text": "cells/mm3", "role": "unit", "parent": "CD4_COUNT"},
                {
                    "text": "Specimen collected before ART start",
                    "role": "context",
                    "parent": "CD4_COUNT",
                },
            ],
        }
    ]

    artifact = extract_pdf_evidence(pages)

    assert artifact["evidence_count"] == 9
    evidence = cast(list[dict[str, Any]], artifact["evidence"])
    by_text = {item["text"]: item for item in evidence}
    assert by_text["SECTION B: HIV testing"]["kind"] == "section_label"
    assert by_text["B1. Has participant ever had an HIV test?"]["field_id"] == "HIV_TEST_DONE"
    assert by_text["Yes"]["kind"] == "option_text"
    assert by_text["Yes"]["parent_id"] == "HIV_TEST_DONE"
    assert by_text["If No, skip to Section C."]["kind"] == "skip_instruction"
    assert by_text["Date of most recent test (DD/MM/YYYY)"]["kind"] == "date_unit_hint"
    assert by_text["B2. CD4 count"]["kind"] == "specimen_test_wording"
    assert by_text["cells/mm3"]["kind"] == "date_unit_hint"
    assert by_text["Specimen collected before ART start"]["kind"] == "useful_context"
    assert check_pdf_evidence_completeness(artifact)["state"] == "complete"


def test_extract_pdf_evidence_excludes_noise_and_raw_dataset_values() -> None:
    pages: list[dict[str, Any]] = [
        {
            "page": 1,
            "lines": [
                "Form 12 footer",
                "PDF created 2026-01-02 10:33:14",
                "Printed on 2026-01-02",
                "Artifact version: source-truth-v4",
                {"text": "Version date: 2024-12-31", "role": "footer"},
                {
                    "text": "C1. Sputum specimen result",
                    "role": "question",
                    "field": "SPUTUM_RESULT",
                },
            ],
            "dataset_rows": [{"SPUTUM_RESULT": "raw-positive-patient-value"}],
            "raw_values": ["raw-negative-patient-value"],
        }
    ]

    artifact = extract_pdf_evidence(pages)
    evidence = cast(list[dict[str, Any]], artifact["evidence"])
    excluded = cast(list[dict[str, Any]], artifact["excluded"])
    rendered = repr(artifact)

    assert [item["text"] for item in evidence] == ["C1. Sputum specimen result"]
    assert {item["text"] for item in excluded} == {
        "Form 12 footer",
        "PDF created 2026-01-02 10:33:14",
        "Printed on 2026-01-02",
        "Artifact version: source-truth-v4",
        "Version date: 2024-12-31",
    }
    assert "raw-positive-patient-value" not in rendered
    assert "raw-negative-patient-value" not in rendered
    assert "dataset_rows" not in rendered
    assert "raw_values" not in rendered


def test_pdf_evidence_completeness_distinguishes_gate_states() -> None:
    not_extracted = extract_pdf_evidence([{"page": 1, "annotations": ["FIELD_ONLY"]}])
    no_useful_text = extract_pdf_evidence(
        [{"page": 1, "lines": ["Form 9 footer", "Exported 2026-01-01"]}]
    )
    needs_review = extract_pdf_evidence(
        [{"page": 1, "lines": ["Unlabeled clinical note beside the field"]}]
    )

    assert check_pdf_evidence_completeness(not_extracted)["state"] == "not_extracted_yet"
    assert check_pdf_evidence_completeness(no_useful_text)["state"] == "no_useful_text_left"
    review_gate = check_pdf_evidence_completeness(needs_review)
    assert review_gate["state"] == "needs_human_review"
    assert review_gate["unclassified_text"] == ["Unlabeled clinical note beside the field"]


def test_all_form_report_surfaces_unmatched_annotations_source_only_evidence_and_gaps() -> None:
    report = build_pdf_evidence_completeness_report(
        {
            "6_HIV": {
                "dataset_columns": ["HIV_TEST_DONE"],
                "pages": [
                    {
                        "page": 1,
                        "annotations": [
                            "HIV_TEST_DONE",
                            "HIV_TEST_DATE",
                            "HIV_FORM_INSTRUCTION",
                        ],
                        "lines": [
                            {
                                "text": "B1. Has participant ever had an HIV test?",
                                "role": "question",
                                "field": "HIV_TEST_DONE",
                            },
                            {
                                "text": "Date of most recent test (DD/MM/YYYY)",
                                "role": "date_hint",
                                "field": "HIV_TEST_DATE",
                            },
                            {
                                "text": "If No, skip to Section C.",
                                "role": "skip",
                                "field": "HIV_FORM_INSTRUCTION",
                            },
                        ],
                    }
                ],
                "dataset_rows": [{"HIV_TEST_DONE": "raw value never copied"}],
            },
            "98B_FOB": {
                "dataset_columns": ["FOB_COHBOUT"],
                "pages": [
                    {
                        "page": 1,
                        "annotations": ["FOB_COHBOUT", "FOB_REVIEW_NOTE"],
                        "lines": [
                            {
                                "text": "B2. Final cohort outcome result",
                                "role": "question",
                                "field": "FOB_COHBOUT",
                            },
                            "Unlabeled clinical note beside the field",
                        ],
                    }
                ],
            },
            "empty_form": {"pages": [{"page": 1, "annotations": ["FIELD_ONLY"]}]},
        }
    )

    assert report["state_counts"] == {
        "complete": 1,
        "needs_human_review": 1,
        "not_extracted_yet": 1,
    }
    forms = cast(dict[str, dict[str, Any]], report["forms"])
    assert forms["6_HIV"]["captured_field_ids"] == [
        "HIV_FORM_INSTRUCTION",
        "HIV_TEST_DATE",
        "HIV_TEST_DONE",
    ]
    assert forms["6_HIV"]["source_only_pdf_evidence"] == [
        "HIV_FORM_INSTRUCTION",
        "HIV_TEST_DATE",
    ]
    assert forms["98B_FOB"]["unmatched_annotation_fields"] == ["FOB_REVIEW_NOTE"]
    assert forms["98B_FOB"]["evidence_gaps"] == ["Unlabeled clinical note beside the field"]
    assert forms["empty_form"]["state"] == "not_extracted_yet"
    assert "raw value never copied" not in repr(report)
