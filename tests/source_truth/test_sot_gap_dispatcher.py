from pathlib import Path

import pytest

from scripts.source_truth.sot_gap_dispatcher import dispatch_forms


FIXTURE = Path("tests/fixtures/sot_gap")


def test_dispatch_forms_runs_extractor_before_reviewer_within_each_form(tmp_path, monkeypatch):
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
    results, errors = dispatch_forms(
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
        assert idx_e < idx_r, f"extractor must run before reviewer for {form}"


def test_dispatch_forms_collects_errors_without_aborting(tmp_path, monkeypatch):
    """When one worker raises, other workers complete and the error is recorded."""
    def fake_run_extractor(*, form, **_kwargs):
        if form == "8_CXR":
            raise RuntimeError("boom")
        return {
            "form": form,
            "yaml_path": str(tmp_path / f"{form}.yaml.draft"),
            "evidence_pack_path": str(tmp_path / f"{form}.json"),
        }

    def fake_run_reviewer(*, form, **_kwargs):
        return {
            "form": form,
            "verdict": "agree",
            "review_md": str(tmp_path / f"{form}_review.md"),
        }

    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.run_extractor", fake_run_extractor)
    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.run_reviewer", fake_run_reviewer)

    results, errors = dispatch_forms(
        forms=["8_CXR", "95_SAE"],
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        drafts_dir=tmp_path,
        evidence_pack_drafts_dir=tmp_path,
        reviews_dir=tmp_path,
        concurrency=2,
    )

    assert {r["form"] for r in results} == {"95_SAE"}
    assert [(form, type(exc).__name__) for form, exc in errors] == [("8_CXR", "RuntimeError")]


@pytest.mark.parametrize(
    "concurrency_arg,expected_workers",
    [(0, 1), (1, 1), (4, 4), (8, 8), (99, 8)],
)
def test_concurrency_clamping(concurrency_arg, expected_workers, monkeypatch):
    """max_workers is clamped to [1, 8] regardless of input."""
    captured = {}

    class _FakePool:
        def __init__(self, max_workers):
            captured["max_workers"] = max_workers
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def submit(self, *args, **kwargs):
            class _F:
                def result(self_inner): return {"form": "X", "verdict": "agree"}
            return _F()

    def _fake_as_completed(d):
        return list(d.keys())

    monkeypatch.setattr(
        "scripts.source_truth.sot_gap_dispatcher.ThreadPoolExecutor", _FakePool
    )
    monkeypatch.setattr(
        "scripts.source_truth.sot_gap_dispatcher.as_completed", _fake_as_completed
    )

    dispatch_forms(
        forms=["X"],
        sot_dir=Path("."),
        raw_pdf_dir=Path("."),
        dataset_dir=Path("."),
        pilot_dir=Path("."),
        drafts_dir=Path("."),
        evidence_pack_drafts_dir=Path("."),
        reviews_dir=Path("."),
        concurrency=concurrency_arg,
    )
    assert captured["max_workers"] == expected_workers
