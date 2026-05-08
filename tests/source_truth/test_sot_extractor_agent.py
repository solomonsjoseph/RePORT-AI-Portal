"""Extractor agent harness: column-keys-only contract."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.source_truth.sot_extractor_agent import gather_inputs, run_extractor


FIXTURE = Path("tests/fixtures/sot_gap")


def test_gather_inputs_collects_column_keys_only():
    inputs = gather_inputs(
        form="8_CXR",
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
    )
    assert "dataset_columns" in inputs
    assert isinstance(inputs["dataset_columns"], list)
    forbidden = {"rows", "values", "samples", "row_values", "data"}
    assert not (forbidden & set(inputs.keys())), inputs.keys()


def test_run_extractor_writes_yaml_and_evidence_pack(tmp_path, monkeypatch):
    fake_yaml = "form_id: 8_CXR\nvariables:\n  - variable_id: CXR_NEW\n"
    fake_pack = '{"form": "8_CXR", "variables": [{"variable_id": "CXR_NEW"}]}'

    def fake_invoke(prompt: str) -> dict:
        return {"yaml": fake_yaml, "evidence_pack": fake_pack}

    monkeypatch.setattr(
        "scripts.source_truth.sot_extractor_agent.invoke_subagent",
        fake_invoke,
    )

    out_dir = tmp_path / "sot_gap_drafts"
    pack_dir = out_dir / "evidence_packs"
    out_dir.mkdir()
    pack_dir.mkdir()

    result = run_extractor(
        form="8_CXR",
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        drafts_dir=out_dir,
        evidence_pack_drafts_dir=pack_dir,
    )

    assert (out_dir / "8_CXR_policy.yaml.draft").read_text() == fake_yaml
    assert (pack_dir / "8_CXR.json").read_text() == fake_pack
    assert result["form"] == "8_CXR"
    assert result["yaml_path"].endswith("8_CXR_policy.yaml.draft")
