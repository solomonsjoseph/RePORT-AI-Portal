"""Tests for the repo-level lean SoT generation wrapper."""

# ruff: noqa: S108

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.source_truth import generate_lean_outputs
from scripts.source_truth.generate_lean_outputs import discover_pdf_backed_forms, generate_form


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_discovers_pdf_backed_forms_with_indo_vap_duplicate_overrides(tmp_path: Path) -> None:
    study_dir = tmp_path / "Indo-VAP"
    _touch(study_dir / "annotated_pdfs" / "14 Case Control v1.0.pdf")
    _touch(study_dir / "datasets" / "14_CaseControl.xlsx")
    _touch(study_dir / "datasets" / "14_Case_Control.xlsx")

    assert discover_pdf_backed_forms(study_dir, "Indo-VAP") == ["14_CaseControl"]


def test_ambiguous_pdf_code_without_override_is_reported(tmp_path: Path) -> None:
    study_dir = tmp_path / "Other"
    _touch(study_dir / "annotated_pdfs" / "1 Screening v1.0.pdf")
    _touch(study_dir / "datasets" / "1_A.xlsx")
    _touch(study_dir / "datasets" / "1_B.xlsx")

    with pytest.raises(RuntimeError, match="ambiguous datasets"):
        discover_pdf_backed_forms(study_dir, "Other")


def test_generate_form_rejects_novel_anchored_candidate_and_promotes_gold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Anchored forms must reject novel candidates and preserve verified gold."""
    repo_root = tmp_path
    study = "Test-Study"
    form = "6_HIV"
    out_dir = repo_root / "output" / study / "llm_source" / "source_truth"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "6 HIV v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")
    _touch(repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml")

    run_calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        run_calls.append(cmd)
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text("study: Test\n", encoding="utf-8")

    diff_calls: list[list[str]] = []

    def fake_run_result(cmd: list[str], *, cwd: Path) -> generate_lean_outputs.subprocess.CompletedProcess[str]:
        diff_calls.append(cmd)
        return generate_lean_outputs.subprocess.CompletedProcess(cmd, 1, "novel=1", "")

    copied: list[tuple[Path, Path]] = []
    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs, "_run_result", fake_run_result)
    monkeypatch.setattr(
        generate_lean_outputs.shutil,
        "copy2",
        lambda src, dest: copied.append((Path(src), Path(dest))),
    )

    generate_form(repo_root, study, form, out_dir)

    assert any("diff_against_gold.py" in " ".join(cmd) for cmd in diff_calls)
    assert any(str(repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml") in cmd for cmd in run_calls)
    assert copied == [
        (
            repo_root / "data" / "SoT" / study / f"{form}_policy.lean.yaml",
            out_dir / f"{form}_policy.lean.yaml",
        )
    ]


def test_generate_form_skips_gold_diff_when_no_gold_exists(monkeypatch, tmp_path: Path) -> None:
    """Unanchored forms keep verifier-only promotion until they have gold."""
    repo_root = tmp_path
    study = "Test-Study"
    form = "7_Culture"
    out_dir = repo_root / "output" / study / "llm_source" / "source_truth"
    _touch(repo_root / "data" / "raw" / study / "annotated_pdfs" / "7 Culture v1.0.pdf")
    _touch(repo_root / "data" / "raw" / study / "datasets" / f"{form}.xlsx")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, cwd: Path) -> None:
        calls.append(cmd)
        if "generate_pdf_aware_candidate.py" in " ".join(cmd):
            Path(f"/tmp/{form}_lean.yaml").write_text("study: Test\n", encoding="utf-8")

    copied: list[Path] = []
    monkeypatch.setattr(generate_lean_outputs, "_run", fake_run)
    monkeypatch.setattr(generate_lean_outputs.shutil, "copy2", lambda _src, dest: copied.append(Path(dest)))

    generate_form(repo_root, study, form, out_dir)

    assert not any("diff_against_gold.py" in " ".join(cmd) for cmd in calls)
    assert copied == [out_dir / f"{form}_policy.lean.yaml"]
