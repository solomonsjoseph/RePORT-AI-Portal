from pathlib import Path

import pytest

from scripts.source_truth.sot_reviewer_agent import run_reviewer


FIXTURE = Path("tests/fixtures/sot_gap")


def test_run_reviewer_writes_review_md(tmp_path, monkeypatch):
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    yaml_path = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_path.write_text("form_id: 8_CXR\nvariables:\n  - variable_id: CXR_NEW\n")
    pack_path = drafts_dir / "8_CXR.json"
    pack_path.write_text('{"form": "8_CXR", "variables": [{"variable_id": "CXR_NEW"}]}')

    monkeypatch.setattr(
        "scripts.source_truth.sot_reviewer_agent.invoke_reviewer_subagent",
        lambda prompt: {"verdict": "agree", "notes": "Looks good."},
    )

    result = run_reviewer(
        form="8_CXR",
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        draft_yaml_path=yaml_path,
        draft_pack_path=pack_path,
        reviews_dir=drafts_dir,
    )
    review_md = drafts_dir / "8_CXR_review.md"
    assert review_md.is_file()
    text = review_md.read_text()
    assert "verdict: agree" in text
    assert result["verdict"] == "agree"
