# tests/source_truth/test_evidence_pack_splitter.py
import json
from pathlib import Path

import pytest

from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.evidence_pack_splitter import split_catalog_artifact
from scripts.source_truth.policy_loader import load_policy_yaml

FIXTURE_DIR = Path("tests/fixtures/build_mini/data/Mini")


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
