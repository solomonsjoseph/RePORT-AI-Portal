"""Behavior tests for Source Truth PHI and cleanup ledgers."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.ledgers import (
    SourceTruthLedgerError,
    build_dataset_cleanup_ledger,
    build_phi_handling_ledger,
)


def _source_truth_artifact() -> dict[str, Any]:
    column_inventory = {
        "study": "LedgerStudy",
        "source_file": "ledger.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [
            {
                "sheet": "main",
                "columns": [
                    "SUBJID",
                    "VISIT_DATE",
                    "LAB_RESULT",
                    "SENSITIVE_KEEP",
                    "PHI_REVIEW",
                    "Time_Stamp",
                    "DUP_A",
                    "DUP_A2",
                    "DUP_REVIEW",
                ],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "SUBJID",
            "VISIT_DATE",
            "LAB_RESULT",
            "SENSITIVE_KEEP",
            "PHI_REVIEW",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "VISIT_DATE",
                    "LAB_RESULT",
                    "SENSITIVE_KEEP",
                    "PHI_REVIEW",
                ],
            }
        ],
        "option_sets": {},
    }
    field_policy = {
        "study": "LedgerStudy",
        "source_file": "ledger.xlsx",
        "coverage": {"boundary": "metadata only"},
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "label": "Subject pseudonym",
                "reason": "participant_identifier",
                "confidence": "high",
                "pdf_annotation_status": "direct",
                "section": "header",
            },
            "VISIT_DATE": {
                "action": "jitter_date",
                "reason": "date_field",
                "confidence": "high",
                "pdf_annotation_status": "direct",
                "section": "visit",
            },
            "LAB_RESULT": {
                "action": "keep",
                "reason": "clinical_measure",
                "confidence": "high",
                "pdf_annotation_status": "direct",
                "section": "labs",
            },
            "SENSITIVE_KEEP": {
                "action": "keep",
                "reason": "sensitive_clinical_field_preserved_with_justification",
                "confidence": "high",
                "pdf_annotation_status": "direct",
                "section": "labs",
                "sensitivity_flags": ["sensitive_clinical"],
            },
            "PHI_REVIEW": {
                "action": "review_required",
                "reason": "possible_phi_free_text",
                "confidence": "low",
                "pdf_annotation_status": "direct",
                "section": "review",
                "sensitivity_flags": ["possible_phi"],
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "non_pdf_system_timestamp_metadata",
                "field_class": "timestamp_metadata",
                "confidence": "high",
                "pdf_annotation_status": "not_annotated",
                "section": "system_metadata",
            },
            "DUP_A": {
                "action": "keep",
                "reason": "canonical_duplicate_column",
                "confidence": "high",
                "pdf_annotation_status": "not_annotated",
            },
            "DUP_A2": {
                "action": "drop",
                "reason": "duplicate_dataset_column",
                "confidence": "high",
                "pdf_annotation_status": "not_annotated",
            },
            "DUP_REVIEW": {
                "action": "keep",
                "reason": "suspected_duplicate_preserved_for_review",
                "confidence": "medium",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }
    return build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)


def test_phi_and_cleanup_ledgers_separate_decision_classes() -> None:
    artifact = _source_truth_artifact()

    phi = build_phi_handling_ledger(artifact)
    cleanup = build_dataset_cleanup_ledger(artifact)

    phi_ids = {entry["source_truth_ref"]["variable_id"] for entry in phi["entries"]}
    cleanup_ids = {entry["source_truth_ref"]["variable_id"] for entry in cleanup["policy_drops"]}

    assert phi_ids == {"SUBJID", "VISIT_DATE", "SENSITIVE_KEEP", "PHI_REVIEW"}
    assert cleanup_ids == {"Time_Stamp", "DUP_A2"}
    assert "LAB_RESULT" not in phi_ids | cleanup_ids

    phi_by_id = {entry["source_truth_ref"]["variable_id"]: entry for entry in phi["entries"]}
    assert phi_by_id["SENSITIVE_KEEP"]["action"] == "keep"
    assert phi_by_id["SENSITIVE_KEEP"]["sensitivity_flags"] == ["sensitive_clinical"]
    assert phi_by_id["PHI_REVIEW"]["review_state"] == "review_required"


def test_cleanup_ledger_reports_exact_duplicate_drops_and_preserved_candidates() -> None:
    runtime_metadata = {
        "removed": [
            {
                "scope": "dataset-system-metadata",
                "name": "Time_Stamp",
                "reason": "known system timestamp metadata",
            },
            {
                "scope": "dataset-duplicate-column",
                "name": "DUP_A2",
                "kept": "DUP_A",
                "reason": "exact duplicate column",
            },
        ],
        "duplicate_candidates_preserved": [
            {
                "scope": "dataset-duplicate-candidate",
                "candidate_a": "DUP_A",
                "candidate_b": "DUP_REVIEW",
                "reason": "values differ; preserved for review",
            }
        ],
    }

    ledger = build_dataset_cleanup_ledger(_source_truth_artifact(), runtime_metadata)

    assert ledger["runtime_cleanup_drops"] == [
        {
            "scope": "dataset-system-metadata",
            "source_truth_ref": {
                "artifact_type": "study_variable_source_truth",
                "variable_id": "Time_Stamp",
            },
            "reason": "known system timestamp metadata",
            "outcome": "runtime_cleanup_drop",
        }
    ]
    assert ledger["duplicate_drops"] == [
        {
            "scope": "dataset-duplicate-column",
            "dropped_ref": {
                "artifact_type": "study_variable_source_truth",
                "variable_id": "DUP_A2",
            },
            "canonical_ref": {
                "artifact_type": "study_variable_source_truth",
                "variable_id": "DUP_A",
            },
            "reason": "exact duplicate column",
            "outcome": "dropped_exact_duplicate",
        }
    ]
    assert ledger["duplicate_candidates_preserved"] == [
        {
            "scope": "dataset-duplicate-candidate",
            "candidate_refs": [
                {"artifact_type": "study_variable_source_truth", "variable_id": "DUP_A"},
                {
                    "artifact_type": "study_variable_source_truth",
                    "variable_id": "DUP_REVIEW",
                },
            ],
            "reason": "values differ; preserved for review",
            "outcome": "preserved_candidate",
        }
    ]


def test_cleanup_ledger_counts_duplicates_by_canonical_source_truth_record() -> None:
    runtime_metadata = {
        "removed": [
            {
                "scope": "dataset-duplicate-column",
                "name": "DUP_A2",
                "kept": "DUP_A",
                "reason": "exact duplicate column",
            },
            {
                "scope": "dataset-duplicate-column",
                "name": "DUP_REVIEW",
                "kept": "DUP_A",
                "reason": "exact duplicate column",
            },
        ],
    }

    ledger = build_dataset_cleanup_ledger(_source_truth_artifact(), runtime_metadata)

    assert ledger["canonical_duplicate_counts"] == [
        {
            "canonical_ref": {
                "artifact_type": "study_variable_source_truth",
                "variable_id": "DUP_A",
            },
            "dropped_duplicate_count": 2,
        }
    ]
    assert ledger["summary"]["exact_duplicate_drop_count"] == 2


def test_cleanup_ledger_reports_policy_runtime_mismatches() -> None:
    runtime_metadata = {
        "policy_runtime_mismatches": [
            {
                "name": "Time_Stamp",
                "type": "policy_drop_missing_runtime_drop",
                "policy_action": "drop",
                "runtime_action": "kept",
                "reason": "cleanup audit did not report expected drop",
            }
        ]
    }

    ledger = build_dataset_cleanup_ledger(_source_truth_artifact(), runtime_metadata)

    assert ledger["policy_runtime_mismatches"] == [
        {
            "source_truth_ref": {
                "artifact_type": "study_variable_source_truth",
                "variable_id": "Time_Stamp",
            },
            "type": "policy_drop_missing_runtime_drop",
            "policy_action": "drop",
            "runtime_action": "kept",
            "reason": "cleanup audit did not report expected drop",
        }
    ]
    assert ledger["summary"]["policy_runtime_mismatch_count"] == 1


def test_ledgers_reject_raw_value_boundaries() -> None:
    artifact = _source_truth_artifact()
    unsafe_artifact = copy.deepcopy(artifact)
    unsafe_artifact["records"][0]["normalized"]["sample_values"] = ["raw value"]

    with pytest.raises(SourceTruthLedgerError, match="sample_values"):
        build_phi_handling_ledger(unsafe_artifact)

    with pytest.raises(SourceTruthLedgerError, match="before_value"):
        build_dataset_cleanup_ledger(
            artifact,
            {
                "removed": [
                    {
                        "scope": "dataset-duplicate-column",
                        "name": "DUP_A2",
                        "kept": "DUP_A",
                        "before_value": "raw row content",
                    }
                ]
            },
        )
