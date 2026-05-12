from pathlib import Path

import pytest

from scripts.source_truth.sot_gap_dispatcher import dispatch_forms
from scripts.source_truth.sot_gap_merge import merge_approved_draft

FIXTURE = Path("tests/fixtures/sot_gap")


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


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

    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.run_extractor", fake_run_extractor)
    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.run_reviewer", fake_run_reviewer)

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


def test_dispatch_forms_records_form_when_reviewer_fails_after_extractor(tmp_path, monkeypatch):
    """When extractor succeeds and reviewer raises, the form is in errors,
    not in results — the dispatcher must not silently lose the failure."""

    def fake_run_extractor(*, form, **_kwargs):
        return {
            "form": form,
            "yaml_path": str(tmp_path / f"{form}.yaml.draft"),
            "evidence_pack_path": str(tmp_path / f"{form}.json"),
        }

    def fake_run_reviewer(*, form, **_kwargs):
        raise RuntimeError(f"reviewer-down for {form}")

    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.run_extractor", fake_run_extractor)
    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.run_reviewer", fake_run_reviewer)

    results, errors = dispatch_forms(
        forms=["8_CXR"],
        sot_dir=FIXTURE / "data/Mini/SoT",
        raw_pdf_dir=FIXTURE / "data/raw/Mini",
        dataset_dir=FIXTURE / "output/Mini/trio_bundle/datasets",
        pilot_dir=FIXTURE / "tmp/results",
        drafts_dir=tmp_path,
        evidence_pack_drafts_dir=tmp_path,
        reviews_dir=tmp_path,
        concurrency=1,
    )

    assert results == []
    assert len(errors) == 1
    form, exc = errors[0]
    assert form == "8_CXR"
    assert isinstance(exc, RuntimeError)


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

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, *args, **kwargs):
            class _F:
                def result(self_inner):
                    return {"form": "X", "verdict": "agree"}

            return _F()

    def _fake_as_completed(d):
        return list(d.keys())

    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.ThreadPoolExecutor", _FakePool)
    monkeypatch.setattr("scripts.source_truth.sot_gap_dispatcher.as_completed", _fake_as_completed)

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


# ---------------------------------------------------------------------------
# merge_approved_draft
# ---------------------------------------------------------------------------


@pytest.fixture
def merge_dirs(tmp_path):
    """Create SoT/drafts/evidence_packs scaffolding shared by merge tests."""
    sot_dir = tmp_path / "SoT"
    sot_dir.mkdir()
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    pack_drafts_dir = drafts_dir / "evidence_packs"
    pack_drafts_dir.mkdir()
    return sot_dir, drafts_dir, pack_drafts_dir


def test_merge_overwrites_sot_yaml_and_keeps_evidence_pack(merge_dirs):
    sot_dir, drafts_dir, pack_drafts_dir = merge_dirs

    yaml_draft = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_draft.write_text("form_id: 8_CXR\nvariables: []\n")
    pack_draft = pack_drafts_dir / "8_CXR.json"
    pack_draft.write_text('{"form": "8_CXR"}')

    merge_approved_draft(
        form="8_CXR",
        draft_yaml_path=yaml_draft,
        draft_pack_path=pack_draft,
        sot_dir=sot_dir,
    )

    assert (sot_dir / "8_CXR_policy.yaml").read_text() == "form_id: 8_CXR\nvariables: []\n"
    # Evidence pack draft remains in the drafts dir; final move to llm_source happens in Phase 2:
    assert pack_draft.is_file()


def test_merge_rejects_malformed_yaml(merge_dirs):
    sot_dir, drafts_dir, pack_drafts_dir = merge_dirs

    yaml_draft = drafts_dir / "8_CXR_policy.yaml.draft"
    yaml_draft.write_text("form_id: 8_CXR\n  invalid:: indent\n")
    pack_draft = pack_drafts_dir / "8_CXR.json"
    pack_draft.write_text('{"form": "8_CXR"}')

    with pytest.raises(ValueError, match="malformed"):
        merge_approved_draft(
            form="8_CXR",
            draft_yaml_path=yaml_draft,
            draft_pack_path=pack_draft,
            sot_dir=sot_dir,
        )

    assert not (sot_dir / "8_CXR_policy.yaml").exists()
    assert not (sot_dir / "8_CXR_policy.yaml.tmp").exists()


def test_merge_raises_file_not_found_for_missing_draft(merge_dirs):
    sot_dir, drafts_dir, pack_drafts_dir = merge_dirs

    missing_yaml = drafts_dir / "missing_policy.yaml.draft"  # not created
    pack_draft = pack_drafts_dir / "missing.json"
    pack_draft.write_text('{"form": "missing"}')

    with pytest.raises(FileNotFoundError):
        merge_approved_draft(
            form="missing",
            draft_yaml_path=missing_yaml,
            draft_pack_path=pack_draft,
            sot_dir=sot_dir,
        )

    assert not (sot_dir / "missing_policy.yaml").exists()
    assert not (sot_dir / "missing_policy.yaml.tmp").exists()
