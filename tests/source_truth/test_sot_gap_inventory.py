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
    assert sorted(cxr["missing_variables"]) != []


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


def test_inventory_never_reads_row_values(tmp_path, monkeypatch):
    """Structural guarantee: no field in the coverage dict carries a row payload."""
    coverage = build_coverage(
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    for form, info in coverage["forms"].items():
        assert "row_sample" not in info, f"{form} leaked a row sample"
        assert "values" not in info, f"{form} leaked values"
