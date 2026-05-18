"""Tests for the repo-level lean SoT generation wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.source_truth.generate_lean_outputs import discover_pdf_backed_forms


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
