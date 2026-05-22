"""Tests for the repo-level lean SoT generation wrapper."""

# ruff: noqa: S108

from __future__ import annotations

import json
from pathlib import Path

from scripts.source_truth import generate_lean_outputs
from scripts.source_truth.generate_lean_outputs import (
    discover_pdf_backed_forms_with_reviews,
    generate_form,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_discovers_pdf_backed_forms_with_indo_vap_duplicate_overrides(tmp_path: Path) -> None:
    study_dir = tmp_path / "Indo-VAP"
    _touch(study_dir / "annotated_pdfs" / "14 Case Control v1.0.pdf")
    _touch(study_dir / "datasets" / "14_CaseControl.xlsx")
    _touch(study_dir / "datasets" / "14_Case_Control.xlsx")

    forms, reviews = discover_pdf_backed_forms_with_reviews(tmp_path, study_dir, "Indo-VAP")
    assert forms == ["14_CaseControl"]
    assert reviews == []


def test_ambiguous_pdf_code_without_override_is_reported(tmp_path: Path) -> None:
    study_dir = tmp_path / "Other"
    _touch(study_dir / "annotated_pdfs" / "1 Screening v1.0.pdf")
    _touch(study_dir / "datasets" / "1_A.xlsx")
    _touch(study_dir / "datasets" / "1_B.xlsx")

    forms, reviews = discover_pdf_backed_forms_with_reviews(tmp_path, study_dir, "Other")
    assert forms == []
    assert len(reviews) == 1
    assert reviews[0].exists()
    assert "ambiguous_dataset" in reviews[0].read_text(encoding="utf-8")


def test_generate_form_rejects_novel_anchored_candidate_and_promotes_gold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Anchored forms must reject novel candidates and preserve verified gold."""
    repo_root = tmp_path
    study = "Test-Study"
    form = "6_HIV"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "6 HIV v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")
    _touch(repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml")

    run_calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        run_calls.append(cmd)
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text("study: Test\n", encoding="utf-8")

    diff_calls: list[list[str]] = []

    def fake_run_result(
        cmd: list[str], *, cwd: Path
    ) -> generate_lean_outputs.subprocess.CompletedProcess[str]:
        diff_calls.append(cmd)
        return generate_lean_outputs.subprocess.CompletedProcess(cmd, 1, "novel=1", "")

    published: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> Path:
        published.append(kwargs)
        return Path(kwargs["out_root"]) / str(kwargs["form"]) / "pdf" / f"{kwargs['form']}_policy.yaml"

    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_run_result", fake_run_result)
    monkeypatch.setattr(generate_lean_outputs, "_publish_verified_sot_outputs", fake_publish)

    generate_form(repo_root, study, form, out_dir)

    assert any("diff_against_gold.py" in " ".join(cmd) for cmd in diff_calls)
    assert any(
        str(repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml") in cmd
        for cmd in run_calls
    )
    assert published[0]["verified_policy"] == repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml"
    assert published[0]["out_root"] == out_dir


def test_generate_form_skips_gold_diff_when_no_gold_exists(monkeypatch, tmp_path: Path) -> None:
    """Unanchored forms keep verifier-only promotion until they have gold."""
    repo_root = tmp_path
    study = "Test-Study"
    form = "7_Culture"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "7 Culture v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        calls.append(cmd)
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text("study: Test\n", encoding="utf-8")

    published: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> Path:
        published.append(kwargs)
        return Path(kwargs["out_root"]) / str(kwargs["form"]) / "pdf" / f"{kwargs['form']}_policy.yaml"

    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_publish_verified_sot_outputs", fake_publish)

    generate_form(repo_root, study, form, out_dir)

    assert not any("diff_against_gold.py" in " ".join(cmd) for cmd in calls)
    assert published[0]["verified_policy"] == Path(f"/tmp/{form}_lean.yaml")
    assert published[0]["out_root"] == out_dir


def test_publish_verified_sot_outputs_writes_policy_schema_and_joined_view(tmp_path: Path) -> None:
    repo_root = tmp_path
    study = "Test-Study"
    form = "6_HIV"
    dataset = repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx"
    policy = tmp_path / f"{form}_policy.yaml"
    source_pack = tmp_path / f"sot_source_pack_{form}.json"
    out_root = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(dataset)
    policy.write_text(
        """
study: Test-Study
form:
  number: "6"
  title: HIV
variables:
  HIV_CD4DAT:
    section: main
    pdf_question: CD4 Test Date
    type: date
    phi: jitter_date
""".lstrip(),
        encoding="utf-8",
    )
    source_pack.write_text(json.dumps({"headers": ["HIV_CD4DAT"]}), encoding="utf-8")

    result = generate_lean_outputs._publish_verified_sot_outputs(
        repo_root=repo_root,
        study=study,
        form=form,
        dataset=dataset,
        source_pack=source_pack,
        verified_policy=policy,
        out_root=out_root,
    )

    pair_dir = out_root / form
    assert result == pair_dir / "pdf" / f"{form}_policy.yaml"
    assert (pair_dir / "dataset" / f"{form}_schema.json").is_file()
    joined = pair_dir / "joined" / f"{form}_joined_query_view.yaml"
    assert joined.is_file()
    joined_text = joined.read_text(encoding="utf-8")
    assert "CD4 Test Date" in joined_text
    assert "source_order" not in joined_text


def test_generate_form_routes_missing_pdf_to_sot_review(tmp_path: Path) -> None:
    repo_root = tmp_path
    study = "Test-Study"
    form = "8_MissingPdf"
    out_dir = repo_root / "output" / study / "llm_source" / "SoT"
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    result = generate_form(repo_root, study, form, out_dir)

    expected = repo_root / "output" / study / "audit" / "Sot_review" / form / "review_report.md"
    assert result == expected
    assert expected.is_file()
    text = expected.read_text(encoding="utf-8")
    assert "missing_pdf" in text
    assert (
        "no Source Truth policy, dataset schema, joined view, or source pack was generated" in text
    )


def test_batch_main_routes_ambiguous_discovery_to_sot_review(tmp_path: Path) -> None:
    repo_root = tmp_path
    study = "Other"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "1 Screening v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / "1_A.xlsx")
    _touch(repo_root / "data" / "raw" / study / "datasets" / "1_B.xlsx")

    rc = generate_lean_outputs.main(["--study", study, "--repo-root", str(repo_root)])

    report = repo_root / "output" / study / "audit" / "Sot_review" / "1" / "review_report.md"
    assert rc == 0
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "ambiguous_dataset" in text
    assert "1_A.xlsx" in text
    assert "1_B.xlsx" in text
