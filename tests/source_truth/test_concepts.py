# tests/source_truth/test_concepts.py
import json
from pathlib import Path

import pytest

from scripts.source_truth.concepts import (
    ConceptIndexError,
    build_concept_index,
    enrich_concept_index_with_schema,
    load_study_concepts,
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


def test_load_study_concepts_returns_dict_with_required_sections():
    concepts = load_study_concepts(FIXTURE_DIR / "study_concepts.yaml")
    assert concepts["study"] == "Mini"
    for section in ("cohorts", "outcomes", "exposures", "schedules", "definitions"):
        assert section in concepts


def test_build_concept_index_emits_artifact_with_artifact_type():
    concepts = load_study_concepts(FIXTURE_DIR / "study_concepts.yaml")
    artifacts = _load_fixture_artifacts()
    index = build_concept_index(concepts, policy_artifacts=artifacts)
    assert index["artifact_type"] == "study_concept_index"
    assert index["study"] == "Mini"
    assert "cohort_a" in index["cohorts"]
    cohort_a = index["cohorts"]["cohort_a"]
    assert cohort_a["name"] == "Mini Cohort A"
    member_vids = {m["variable_id"] for m in cohort_a["member_variables"]}
    assert "SUBJID" in member_vids
    assert "IC_WEIGHT" in member_vids


def test_build_concept_index_blocks_on_unknown_member_variable():
    concepts = load_study_concepts(FIXTURE_DIR / "study_concepts.yaml")
    concepts["cohorts"]["cohort_a"]["member_variables"].append(
        {"form": "2A_ICBaseline", "variable_id": "DOES_NOT_EXIST", "role": "covariate"}
    )
    artifacts = _load_fixture_artifacts()
    with pytest.raises(ConceptIndexError, match="DOES_NOT_EXIST"):
        build_concept_index(concepts, policy_artifacts=artifacts)


def test_build_concept_index_initial_analysis_queryable_is_null():
    concepts = load_study_concepts(FIXTURE_DIR / "study_concepts.yaml")
    artifacts = _load_fixture_artifacts()
    index = build_concept_index(concepts, policy_artifacts=artifacts)
    members = index["cohorts"]["cohort_a"]["member_variables"]
    for member in members:
        assert member["analysis_queryable"] is None


def test_build_concept_index_byte_identical_repeat_run():
    concepts = load_study_concepts(FIXTURE_DIR / "study_concepts.yaml")
    artifacts = _load_fixture_artifacts()
    a = build_concept_index(concepts, policy_artifacts=artifacts)
    b = build_concept_index(concepts, policy_artifacts=artifacts)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_enrich_concept_index_with_dataset_schema_patches_analysis_queryable():
    concepts = load_study_concepts(FIXTURE_DIR / "study_concepts.yaml")
    artifacts = _load_fixture_artifacts()
    index = build_concept_index(concepts, policy_artifacts=artifacts)

    fake_schema = {
        "artifact_type": "study_dataset_schema",
        "entries": [
            {"variable_id": "SUBJID", "analysis_queryable": True},
            {"variable_id": "IC_WEIGHT", "analysis_queryable": False},
        ],
    }
    enriched = enrich_concept_index_with_schema(index, dataset_schema=fake_schema)

    members = enriched["cohorts"]["cohort_a"]["member_variables"]
    by_var = {m["variable_id"]: m for m in members}
    assert by_var["SUBJID"]["analysis_queryable"] is True
    assert by_var["IC_WEIGHT"]["analysis_queryable"] is False
