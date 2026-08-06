from __future__ import annotations

from pathlib import Path

from scripts.source_truth import study_intake


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
