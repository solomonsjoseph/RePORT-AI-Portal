# tests/source_truth/test_evidence_pack_splitter.py
import json
from pathlib import Path

import pytest

from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.evidence_pack_splitter import split_catalog_artifact
from scripts.source_truth.policy_loader import load_policy_yaml

FIXTURE_DIR = Path("tests/fixtures/build_mini/data/Mini/SoT")


def _build_for(form: str):
    art = load_policy_yaml(FIXTURE_DIR / f"{form}_policy.yaml")
    return build_catalog_artifact(art)


def test_split_catalog_artifact_returns_compact_and_packs():
    catalog = _build_for("19_Smear")
    compact, packs = split_catalog_artifact(catalog)
    assert compact["artifact_type"] == "study_metadata_catalog"
    assert "compact_records" in compact
    assert "evidence_packs" not in compact
    assert isinstance(packs, dict)
    assert all(isinstance(p, dict) for p in packs.values())


def test_split_catalog_packs_keyed_by_variable_id():
    catalog = _build_for("19_Smear")
    _, packs = split_catalog_artifact(catalog)
    sample_vid = next(iter(packs))
    assert packs[sample_vid].get("variable_id") == sample_vid


def test_split_catalog_artifact_empty_packs_when_none():
    fake = {
        "artifact_type": "study_variable_catalog",
        "compact_records": [],
        "evidence_packs": [],
    }
    compact, packs = split_catalog_artifact(fake)
    assert packs == {}
    assert compact["compact_records"] == []


def test_split_catalog_artifact_idempotent_on_already_renamed_artifact_type():
    """When artifact_type is already `study_metadata_catalog`, no re-rename;
    output mirrors input minus the evidence_packs key."""
    fake = {
        "artifact_type": "study_metadata_catalog",
        "compact_records": [{"variable_id": "X"}],
        "evidence_packs": [{"variable_id": "X", "extra": "field"}],
    }
    compact, packs = split_catalog_artifact(fake)
    assert compact["artifact_type"] == "study_metadata_catalog"
    assert "evidence_packs" not in compact
    assert compact["compact_records"] == [{"variable_id": "X"}]
    assert packs == {"X": {"variable_id": "X", "extra": "field"}}


def test_split_catalog_packs_for_multi_form_fixture():
    """Run the splitter on a second fixture form to verify cross-form behavior.

    Uses 2A_ICBaseline which has different variable shapes from 19_Smear.
    """
    catalog = _build_for("2A_ICBaseline")
    compact, packs = split_catalog_artifact(catalog)

    assert compact["artifact_type"] == "study_metadata_catalog"
    assert isinstance(compact.get("compact_records"), list)
    assert len(compact["compact_records"]) > 0

    # Every pack must have a variable_id matching its key
    for vid, pack in packs.items():
        assert pack["variable_id"] == vid

    # The form's variable_id set should appear among the packs
    pack_vids = set(packs.keys())
    record_vids = {r["variable_id"] for r in compact["compact_records"]}
    assert pack_vids & record_vids, (
        "expected at least one shared variable_id between compact_records and packs"
    )
