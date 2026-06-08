from __future__ import annotations

from pathlib import Path

from scripts.source_truth import study_intake
from scripts.source_truth.study_intake import _find_dataset


def test_missing_pdf_is_routed_to_sot_review(tmp_path: Path, capsys) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    (study_dir / "datasets").mkdir(parents=True)
    (study_dir / "datasets" / "1_Form.xlsx").write_bytes(b"placeholder")

    rc = study_intake.main(["--study", "Study", "--form", "1_Form", "--repo-root", str(tmp_path)])

    report = tmp_path / "output" / "Study" / "audit" / "Sot_review" / "1_Form" / "review_report.md"
    assert rc == 0
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "# Sot_review: Source Truth Human Review" in text
    assert "missing_pdf" in text
    assert (
        "no Source Truth policy, dataset schema, joined view, or source pack was generated" in text
    )
    assert "sot_review_report=" in capsys.readouterr().out


def test_missing_dataset_is_routed_to_sot_review(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    (study_dir / "annotated_pdfs").mkdir(parents=True)
    (study_dir / "annotated_pdfs" / "1 Form v1.0.pdf").write_bytes(b"%PDF placeholder")

    rc = study_intake.main(["--study", "Study", "--form", "1_Form", "--repo-root", str(tmp_path)])

    report = tmp_path / "output" / "Study" / "audit" / "Sot_review" / "1_Form" / "review_report.md"
    assert rc == 0
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "missing_dataset" in text
    assert "data/raw/Study/datasets" in text


def test_ambiguous_dataset_is_routed_to_sot_review(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Study"
    (study_dir / "annotated_pdfs").mkdir(parents=True)
    (study_dir / "datasets").mkdir(parents=True)
    (study_dir / "annotated_pdfs" / "1 Form v1.0.pdf").write_bytes(b"%PDF placeholder")
    (study_dir / "datasets" / "1_A.xlsx").write_bytes(b"placeholder")
    (study_dir / "datasets" / "1_B.xlsx").write_bytes(b"placeholder")

    rc = study_intake.main(["--study", "Study", "--form", "1_Form", "--repo-root", str(tmp_path)])

    report = tmp_path / "output" / "Study" / "audit" / "Sot_review" / "1_Form" / "review_report.md"
    assert rc == 0
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert "ambiguous_dataset" in text
    assert "1_A.xlsx" in text
    assert "1_B.xlsx" in text


# ---------------------------------------------------------------------------
# Regression tests: _find_dataset must honour _forms_manifest.yaml reject list
# ---------------------------------------------------------------------------

_MANIFEST_WITH_REJECT = """\
required:
  - 14_Case_Control.xlsx
optional: []
reject:
  - 14_CaseControl.xlsx
"""


def test_find_dataset_skips_rejected_exact_match(tmp_path: Path) -> None:
    """Exact-name match returns the kept file, not the manifest-rejected duplicate stub."""
    study_dir = tmp_path / "data" / "raw" / "TestStudy"
    ds_dir = study_dir / "datasets"
    ds_dir.mkdir(parents=True)

    # Both the kept file and the rejected stub exist on disk.
    (ds_dir / "14_Case_Control.xlsx").write_bytes(b"")
    (ds_dir / "14_CaseControl.xlsx").write_bytes(b"")

    # Manifest rejects the stub and requires the real file.
    (study_dir / "_forms_manifest.yaml").write_text(_MANIFEST_WITH_REJECT, encoding="utf-8")

    result = _find_dataset(study_dir, "14_CaseControl")

    assert result is not None
    assert result.name == "14_Case_Control.xlsx", (
        f"Expected the kept dataset 14_Case_Control.xlsx but got {result.name!r}; "
        "rejected stub 14_CaseControl.xlsx must not be returned"
    )


def test_find_dataset_degrades_gracefully_without_manifest(tmp_path: Path) -> None:
    """When no _forms_manifest.yaml is present, _find_dataset falls back to current behaviour."""
    study_dir = tmp_path / "data" / "raw" / "TestStudy"
    ds_dir = study_dir / "datasets"
    ds_dir.mkdir(parents=True)

    # Only one file; no manifest.  Exact-match should still resolve it.
    (ds_dir / "5_Demographics.xlsx").write_bytes(b"")

    result = _find_dataset(study_dir, "5_Demographics")

    assert result is not None
    assert result.name == "5_Demographics.xlsx"
