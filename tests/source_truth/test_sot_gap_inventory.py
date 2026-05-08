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


def test_inventory_skips_pipeline_metadata_columns(tmp_path):
    """Pipeline-injected columns must not count as missing variables."""
    sot_dir = tmp_path / "SoT"
    sot_dir.mkdir()
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    raw_pdf_dir = tmp_path / "raw_pdfs"
    raw_pdf_dir.mkdir()
    pilot_dir = tmp_path / "pilots"
    pilot_dir.mkdir()

    # SoT declares only the real form variables
    (sot_dir / "TEST_policy.yaml").write_text(
        "form_id: TEST\nvariables:\n  - variable_id: SUBJID\n  - variable_id: AGE\n"
    )
    # Dataset has the real vars PLUS pipeline metadata
    (dataset_dir / "TEST.jsonl").write_text(
        '{"SUBJID": "X", "AGE": 30, "source_file": "f.csv", "_provenance": "pipe", "_phi_scrubbed": true}\n'
    )

    coverage = build_coverage(sot_dir=sot_dir, raw_pdf_dir=raw_pdf_dir, dataset_dir=dataset_dir, pilot_dir=pilot_dir)
    info = coverage["forms"]["TEST"]
    assert info["sot_present"] is True
    assert info["sot_complete"] is True
    assert info["missing_variables"] == []


def test_inventory_finds_pdf_with_real_indo_vap_naming(tmp_path):
    """PDFs named '<id> <human readable> vX.Y.pdf' under nested subdirs are matched to form ids."""
    sot_dir = tmp_path / "SoT"
    sot_dir.mkdir()
    dataset_dir = tmp_path / "datasets"
    dataset_dir.mkdir()
    raw_pdf_dir = tmp_path / "raw_pdfs"
    nested = raw_pdf_dir / "annotated_pdfs"
    nested.mkdir(parents=True)
    pilot_dir = tmp_path / "pilots"
    pilot_dir.mkdir()

    (sot_dir / "10_TST_policy.yaml").write_text(
        "form_id: 10_TST\nvariables:\n  - variable_id: SUBJID\n"
    )
    (dataset_dir / "10_TST.jsonl").write_text('{"SUBJID": "X"}\n')
    (nested / "10 TST screening v1.0.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    coverage = build_coverage(sot_dir=sot_dir, raw_pdf_dir=raw_pdf_dir, dataset_dir=dataset_dir, pilot_dir=pilot_dir)
    info = coverage["forms"]["10_TST"]
    assert "pdf" in info["observed_in"]
    assert info["pdf_path"].endswith("10 TST screening v1.0.pdf")
