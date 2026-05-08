from pathlib import Path

import pytest

from scripts.source_truth.sot_gap_dispatcher import dispatch_forms


FIXTURE = Path("tests/fixtures/sot_gap")


def test_dispatch_forms_runs_extractor_and_reviewer_in_order(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_run_extractor(*, form, **_kwargs):
        calls.append(("extractor", form))
        return {
            "form": form,
            "yaml_path": str(tmp_path / f"{form}.yaml.draft"),
            "evidence_pack_path": str(tmp_path / f"{form}.json"),
        }

    def fake_run_reviewer(*, form, **_kwargs):
        calls.append(("reviewer", form))
        return {
            "form": form,
            "verdict": "agree",
            "review_md": str(tmp_path / f"{form}_review.md"),
        }

    monkeypatch.setattr(
        "scripts.source_truth.sot_gap_dispatcher.run_extractor", fake_run_extractor
    )
    monkeypatch.setattr(
        "scripts.source_truth.sot_gap_dispatcher.run_reviewer", fake_run_reviewer
    )

    forms = ["8_CXR", "95_SAE"]
    results = dispatch_forms(
        forms=forms,
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        drafts_dir=tmp_path,
        evidence_pack_drafts_dir=tmp_path,
        reviews_dir=tmp_path,
        concurrency=2,
    )
    assert len(results) == 2
    assert {r["form"] for r in results} == {"8_CXR", "95_SAE"}
    for form in forms:
        idx_e = calls.index(("extractor", form))
        idx_r = calls.index(("reviewer", form))
        assert idx_e < idx_r
