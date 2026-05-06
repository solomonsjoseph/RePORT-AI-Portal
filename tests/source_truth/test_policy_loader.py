# tests/source_truth/test_policy_loader.py
from pathlib import Path

import pytest

from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.policy_loader import (
    PolicyLoaderError,
    load_policy_yaml,
)

FIXTURE_DIR = Path("tests/fixtures/build_mini/data/Mini")


def test_load_policy_yaml_returns_source_truth_artifact_shape():
    artifact = load_policy_yaml(FIXTURE_DIR / "19_Smear_policy.yaml")

    assert artifact["study"] == "Mini"
    assert artifact["form"] == "19_Smear"
    assert isinstance(artifact["records"], list)
    assert all(isinstance(r, dict) and "variable_id" in r for r in artifact["records"])
    assert "ledger_expectations" in artifact


def test_load_policy_yaml_rejects_missing_required_keys(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 2\n")  # no study, no form, no variables
    with pytest.raises(PolicyLoaderError):
        load_policy_yaml(bad)


def test_load_policy_yaml_preserves_review_state():
    artifact = load_policy_yaml(FIXTURE_DIR / "19_Smear_policy.yaml")
    # After translation records carry review_state at the top level (not nested under "review").
    review_states = {r["variable_id"]: r.get("review_state") for r in artifact["records"]}
    assert any(state for state in review_states.values())  # at least one record has review state


def test_load_policy_yaml_records_have_catalog_compatible_shape():
    artifact = load_policy_yaml(FIXTURE_DIR / "19_Smear_policy.yaml")
    for record in artifact["records"]:
        assert "presence" in record
        assert "exact_source_wording" in record
        assert "normalized" in record
        assert "source_kind" in record
        assert "review_state" in record
        assert "derivation_targets" in record


def test_load_policy_yaml_subjid_translated_correctly():
    artifact = load_policy_yaml(FIXTURE_DIR / "19_Smear_policy.yaml")
    by_vid = {r["variable_id"]: r for r in artifact["records"]}
    subjid = by_vid["SUBJID"]
    assert subjid["normalized"]["handling_action"] == "pseudonymize"
    assert subjid["presence"]["dataset"]["present"] is True
    assert subjid["presence"]["pdf"]["present"] is True
    assert subjid["normalized"]["sensitivity_flags"] == ["subject_identifier"]


def test_load_policy_yaml_then_build_catalog_artifact_smoke():
    """End-to-end: translated records must be acceptable input to build_catalog_artifact."""
    artifact = load_policy_yaml(FIXTURE_DIR / "19_Smear_policy.yaml")
    catalog = build_catalog_artifact(artifact)
    assert catalog["artifact_type"] == "study_variable_catalog"
    # build_catalog_artifact returns compact variable records under the key "records".
    assert isinstance(catalog["records"], list)
    assert len(catalog["records"]) > 0
