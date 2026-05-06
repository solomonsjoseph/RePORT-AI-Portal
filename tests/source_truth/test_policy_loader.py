# tests/source_truth/test_policy_loader.py
from pathlib import Path

import pytest

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
    review_states = {r["variable_id"]: r.get("review", {}).get("state") for r in artifact["records"]}
    assert any(state for state in review_states.values())  # at least one record has review state
