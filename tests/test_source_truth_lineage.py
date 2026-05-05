"""Lineage/version compatibility helpers for Source Truth artifact outputs."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.dataset_schema import build_dataset_schema
from scripts.source_truth.ledgers import (
    build_dataset_cleanup_ledger,
    build_phi_handling_ledger,
)
from scripts.source_truth.lineage import (
    LINEAGE_VERSION,
    SourceTruthLineageError,
    build_lineage_report,
    stamp_generated_artifact,
    stamp_source_truth,
    validate_lineage_bundle,
)


def _source_truth_artifact() -> dict[str, Any]:
    return build_source_truth_artifact(
        {
            "study": "LineageStudy",
            "source_file": "lineage.xlsx",
            "sheets": [{"sheet": "main", "columns": ["SUBJID", "VISIT_DATE", "DROP_ME"]}],
        },
        {
            "real_annotation_variables": ["SUBJID", "VISIT_DATE", "DROP_ME"],
            "annotation_pages": [{"page": 1, "annotations": ["SUBJID", "VISIT_DATE", "DROP_ME"]}],
        },
        {
            "study": "LineageStudy",
            "source_file": "lineage.xlsx",
            "fields": {
                "SUBJID": {
                    "action": "pseudonymize",
                    "reason": "participant_identifier",
                    "confidence": "high",
                    "pdf_annotation_status": "direct",
                },
                "VISIT_DATE": {
                    "action": "jitter_date",
                    "reason": "date_field",
                    "confidence": "high",
                    "pdf_annotation_status": "direct",
                },
                "DROP_ME": {
                    "action": "drop",
                    "reason": "duplicate_dataset_column",
                    "confidence": "high",
                    "pdf_annotation_status": "direct",
                },
            },
        },
    )


def _lineaged_bundle(run_id: str = "run-001") -> dict[str, dict[str, Any]]:
    source_truth = stamp_source_truth(_source_truth_artifact(), run_id=run_id)
    schema = stamp_generated_artifact(
        build_dataset_schema(source_truth),
        source_truth,
        run_id=run_id,
    )
    catalog = stamp_generated_artifact(
        build_catalog_artifact(source_truth, dataset_schema=schema),
        source_truth,
        run_id=run_id,
    )
    phi = stamp_generated_artifact(
        build_phi_handling_ledger(source_truth), source_truth, run_id=run_id
    )
    cleanup = stamp_generated_artifact(
        build_dataset_cleanup_ledger(source_truth),
        source_truth,
        run_id=run_id,
    )
    dataset_output = stamp_generated_artifact(
        {
            "artifact_type": "study_dataset_output",
            "study": source_truth["study"],
            "source_file": source_truth["source_file"],
            "dataset_schema_ref": schema["lineage"]["artifact_ref"],
        },
        source_truth,
        run_id=run_id,
        generated_from=[source_truth, schema],
    )
    return {
        "source_truth": source_truth,
        "dataset_schema": schema,
        "catalog": catalog,
        "phi_ledger": phi,
        "cleanup_ledger": cleanup,
        "dataset_output": dataset_output,
    }


def test_stamps_source_truth_and_generated_artifact_refs_without_mutating_inputs() -> None:
    source_truth_input = _source_truth_artifact()
    source_truth_before = copy.deepcopy(source_truth_input)

    source_truth = stamp_source_truth(source_truth_input, run_id="run-001")
    schema_input = build_dataset_schema(source_truth)
    schema_before = copy.deepcopy(schema_input)
    schema = stamp_generated_artifact(schema_input, source_truth, run_id="run-001")

    assert source_truth_input == source_truth_before
    assert schema_input == schema_before
    assert source_truth["lineage"]["version"] == LINEAGE_VERSION
    assert source_truth["lineage"]["run_id"] == "run-001"
    assert source_truth["lineage"]["generation_id"].startswith("stg-")
    assert schema["lineage"]["artifact_ref"] == {
        "artifact_type": "study_dataset_schema",
        "study": "LineageStudy",
        "source_file": "lineage.xlsx",
        "run_id": "run-001",
        "generation_id": schema["lineage"]["generation_id"],
    }
    assert schema["lineage"]["generated_from"] == [source_truth["lineage"]["artifact_ref"]]


def test_validates_consistent_lineage_across_all_source_truth_outputs() -> None:
    bundle = _lineaged_bundle()

    report = validate_lineage_bundle(bundle.values())

    assert report == {
        "ok": True,
        "version": LINEAGE_VERSION,
        "run_id": "run-001",
        "source_truth_generation_id": bundle["source_truth"]["lineage"]["generation_id"],
        "artifact_count": 6,
        "problems": [],
    }


def test_reports_stale_generated_artifacts_without_raising() -> None:
    bundle = _lineaged_bundle()
    refreshed_source_truth = stamp_source_truth(_source_truth_artifact(), run_id="run-002")
    bundle["source_truth"] = refreshed_source_truth

    report = build_lineage_report(bundle.values())

    assert report["ok"] is False
    assert report["problems"] == [
        {"code": "mixed_run_ids", "run_ids": ["run-001", "run-002"]},
        {
            "code": "stale_artifact",
            "artifact_type": "study_dataset_schema",
            "generation_id": bundle["dataset_schema"]["lineage"]["generation_id"],
            "expected_source_truth_generation_id": refreshed_source_truth["lineage"][
                "generation_id"
            ],
            "actual_source_truth_generation_id": bundle["dataset_schema"]["lineage"][
                "generated_from"
            ][0]["generation_id"],
        },
        {
            "code": "stale_artifact",
            "artifact_type": "study_variable_catalog",
            "generation_id": bundle["catalog"]["lineage"]["generation_id"],
            "expected_source_truth_generation_id": refreshed_source_truth["lineage"][
                "generation_id"
            ],
            "actual_source_truth_generation_id": bundle["catalog"]["lineage"]["generated_from"][0][
                "generation_id"
            ],
        },
        {
            "code": "stale_artifact",
            "artifact_type": "phi_handling_ledger",
            "generation_id": bundle["phi_ledger"]["lineage"]["generation_id"],
            "expected_source_truth_generation_id": refreshed_source_truth["lineage"][
                "generation_id"
            ],
            "actual_source_truth_generation_id": bundle["phi_ledger"]["lineage"]["generated_from"][
                0
            ]["generation_id"],
        },
        {
            "code": "stale_artifact",
            "artifact_type": "dataset_cleanup_ledger",
            "generation_id": bundle["cleanup_ledger"]["lineage"]["generation_id"],
            "expected_source_truth_generation_id": refreshed_source_truth["lineage"][
                "generation_id"
            ],
            "actual_source_truth_generation_id": bundle["cleanup_ledger"]["lineage"][
                "generated_from"
            ][0]["generation_id"],
        },
        {
            "code": "stale_artifact",
            "artifact_type": "study_dataset_output",
            "generation_id": bundle["dataset_output"]["lineage"]["generation_id"],
            "expected_source_truth_generation_id": refreshed_source_truth["lineage"][
                "generation_id"
            ],
            "actual_source_truth_generation_id": bundle["dataset_output"]["lineage"][
                "generated_from"
            ][0]["generation_id"],
        },
    ]


def test_rejects_missing_lineage_and_mixed_versions() -> None:
    bundle = _lineaged_bundle()
    missing = copy.deepcopy(bundle["catalog"])
    missing.pop("lineage")

    with pytest.raises(SourceTruthLineageError, match="missing_lineage"):
        validate_lineage_bundle([bundle["source_truth"], missing])

    mixed = copy.deepcopy(bundle["dataset_schema"])
    mixed["lineage"]["version"] = "source-truth-lineage/v0"

    with pytest.raises(SourceTruthLineageError, match="mixed_lineage_versions"):
        validate_lineage_bundle([bundle["source_truth"], mixed])


def test_rejects_artifacts_from_different_runs() -> None:
    bundle = _lineaged_bundle()
    other_run_schema = stamp_generated_artifact(
        build_dataset_schema(bundle["source_truth"]),
        bundle["source_truth"],
        run_id="run-002",
    )

    with pytest.raises(SourceTruthLineageError, match="mixed_run_ids"):
        validate_lineage_bundle([bundle["source_truth"], other_run_schema])
