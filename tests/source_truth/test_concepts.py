# tests/source_truth/test_concepts.py
"""Tests for the concept-index validator and dataset-schema enricher.

The concept index is now structurally derived from SoT (see
``test_concept_derivation``); this module covers the validation and
enrichment passes that wrap the derived index inside the build
coordinator.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.concept_derivation import derive_concept_index
from scripts.source_truth.concepts import (
    ConceptIndexError,
    build_concept_index,
    enrich_concept_index_with_schema,
)
from scripts.source_truth.policy_loader import load_policy_yaml

FIXTURE_DIR = Path("tests/fixtures/build_mini/data/Mini")
POLICIES_DIR = FIXTURE_DIR / "SoT"


def _load_fixture_artifacts():
    return [
        load_policy_yaml(POLICIES_DIR / "1A_ICScreening_policy.yaml"),
        load_policy_yaml(POLICIES_DIR / "2A_ICBaseline_policy.yaml"),
        load_policy_yaml(POLICIES_DIR / "19_Smear_policy.yaml"),
    ]


def test_build_concept_index_emits_artifact_with_artifact_type():
    artifacts = _load_fixture_artifacts()
    derived = derive_concept_index(artifacts)
    index = build_concept_index(derived, policy_artifacts=artifacts)
    assert index["artifact_type"] == "study_concept_index"
    assert index["study"] == "Mini"
    assert "cohort_a" in index["cohorts"]
    cohort_a = index["cohorts"]["cohort_a"]
    member_vids = {m["variable_id"] for m in cohort_a["member_variables"]}
    # SUBJID is the canonical identifier in 1A and 2A.
    assert "SUBJID" in member_vids


def test_build_concept_index_blocks_on_unknown_member_variable():
    artifacts = _load_fixture_artifacts()
    derived = derive_concept_index(artifacts)
    derived["cohorts"]["cohort_a"]["member_variables"].append(
        {"form": "2A_ICBaseline", "variable_id": "DOES_NOT_EXIST", "role": "covariate"}
    )
    with pytest.raises(ConceptIndexError, match="DOES_NOT_EXIST"):
        build_concept_index(derived, policy_artifacts=artifacts)


def test_build_concept_index_initial_analysis_queryable_is_null():
    artifacts = _load_fixture_artifacts()
    derived = derive_concept_index(artifacts)
    index = build_concept_index(derived, policy_artifacts=artifacts)
    members = index["cohorts"]["cohort_a"]["member_variables"]
    assert members  # ensure cohort_a has at least one member
    for member in members:
        assert member["analysis_queryable"] is None


def test_build_concept_index_byte_identical_repeat_run():
    artifacts = _load_fixture_artifacts()
    derived = derive_concept_index(artifacts)
    a = build_concept_index(derived, policy_artifacts=artifacts)
    b = build_concept_index(derived, policy_artifacts=artifacts)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_enrich_concept_index_with_dataset_schema_patches_analysis_queryable():
    artifacts = _load_fixture_artifacts()
    derived = derive_concept_index(artifacts)
    index = build_concept_index(derived, policy_artifacts=artifacts)

    fake_schema = {
        "artifact_type": "study_dataset_schema",
        "entries": [
            {"variable_id": "SUBJID", "analysis_queryable": True},
            {"variable_id": "IC_WEIGHT", "analysis_queryable": False},
        ],
    }
    enriched = enrich_concept_index_with_schema(index, dataset_schema=fake_schema)

    members = enriched["cohorts"]["cohort_a"]["member_variables"]
    by_var: dict[str, dict] = {}
    for m in members:
        # Multiple forms may carry SUBJID; first occurrence per vid is fine.
        by_var.setdefault(m["variable_id"], m)
    assert by_var["SUBJID"]["analysis_queryable"] is True
