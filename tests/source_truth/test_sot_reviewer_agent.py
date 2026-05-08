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


def test_run_reviewer_rejects_unknown_verdict(tmp_path, monkeypatch):
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    yaml_path = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_path.write_text("form_id: 8_CXR\nvariables: []\n")
    pack_path = drafts_dir / "8_CXR.json"
    pack_path.write_text('{"form": "8_CXR", "variables": []}')

    monkeypatch.setattr(
        "scripts.source_truth.sot_reviewer_agent.invoke_reviewer_subagent",
        lambda prompt: {"verdict": "looks_good", "notes": ""},
    )

    with pytest.raises(ValueError, match="Unexpected verdict"):
        run_reviewer(
            form="8_CXR",
            sot_dir=Path("tests/fixtures/sot_gap/data/Mini/SoT"),
            raw_pdf_dir=Path("tests/fixtures/sot_gap/data/raw/Mini"),
            dataset_dir=Path("tests/fixtures/sot_gap/output/Mini/trio_bundle/datasets"),
            pilot_dir=Path("tests/fixtures/sot_gap/tmp/results"),
            draft_yaml_path=yaml_path,
            draft_pack_path=pack_path,
            reviews_dir=drafts_dir,
        )


import json as _json

from scripts.source_truth.sot_reviewer_agent import run_reviewer


def test_run_reviewer_wraps_json_decode_error_with_form_context(tmp_path, monkeypatch):
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    yaml_path = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_path.write_text("form_id: 8_CXR\nvariables: []\n")
    pack_path = drafts_dir / "8_CXR.json"
    pack_path.write_text('{"form": "8_CXR", "variables": []}')

    def fake_invoke(prompt: str):
        raise _json.JSONDecodeError("Expecting value", "doc", 0)

    monkeypatch.setattr(
        "scripts.source_truth.sot_reviewer_agent.invoke_reviewer_subagent",
        fake_invoke,
    )

    with pytest.raises(ValueError, match="non-JSON for form"):
        run_reviewer(
            form="8_CXR",
            sot_dir=Path("tests/fixtures/sot_gap/data/Mini/SoT"),
            raw_pdf_dir=Path("tests/fixtures/sot_gap/data/raw/Mini"),
            dataset_dir=Path("tests/fixtures/sot_gap/output/Mini/trio_bundle/datasets"),
            pilot_dir=Path("tests/fixtures/sot_gap/tmp/results"),
            draft_yaml_path=yaml_path,
            draft_pack_path=pack_path,
            reviews_dir=drafts_dir,
        )
