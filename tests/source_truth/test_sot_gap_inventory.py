"""SoT gap inventory walker: fixture-driven."""

import json
from pathlib import Path

import pytest

from scripts.source_truth.sot_gap_inventory import build_coverage

FIXTURE = Path("tests/fixtures/sot_gap")


def test_complete_form_marked_complete():
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    assert coverage["forms"]["19_Smear"]["sot_present"] is True
    assert coverage["forms"]["19_Smear"]["sot_complete"] is True
    assert coverage["forms"]["19_Smear"]["missing_variables"] == []


def test_partial_form_lists_missing_variables():
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    cxr = coverage["forms"]["8_CXR"]
    assert cxr["sot_present"] is True
    assert cxr["sot_complete"] is False
    # 8_CXR.jsonl includes CXR_RESULT and CXR_SIDE which are NOT in 8_CXR_policy.yaml
    assert sorted(cxr["missing_variables"]) == ["CXR_RESULT", "CXR_SIDE"]


def test_missing_form_listed():
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    sae = coverage["forms"]["95_SAE"]
    assert sae["sot_present"] is False
    assert sae["sot_complete"] is False


def test_inventory_never_persists_row_values_or_columns(tmp_path, monkeypatch):
    """Privacy: per-form info must not carry the full schema or row payload."""
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    forbidden_keys = {"row_sample", "values", "dataset_columns", "rows", "samples"}
    for form, info in coverage["forms"].items():
        leaked = forbidden_keys & set(info.keys())
        assert not leaked, f"{form} leaked keys: {leaked}"


def test_production_format_yaml_parses_correctly():
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    info = coverage["forms"]["4_Smear"]
    assert info["sot_present"] is True
    assert info["sot_complete"] is True
    assert info["missing_variables"] == []
