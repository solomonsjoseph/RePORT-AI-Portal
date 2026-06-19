"""Tests for raw-file dataset deduplication (Note 4)."""

from __future__ import annotations

from pathlib import Path

from scripts.extraction.raw_file_dedup import (
    dedup_raw_datasets,
    normalize_dataset_stem,
)


def test_normalize_dataset_stem_collapses_variants() -> None:
    assert normalize_dataset_stem("Baseline_form") == normalize_dataset_stem("baselineform1")
    assert normalize_dataset_stem("camelCase_2") == normalize_dataset_stem("camelcase")


def test_tier1_perfect_duplicate_archives_extra(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Demo" / "datasets"
    study_dir.mkdir(parents=True)

    (study_dir / "form_a.csv").write_text("H1,H2\n1,2\n3,4\n", encoding="utf-8")
    (study_dir / "form_a1.csv").write_text("H1,H2\n1,2\n3,4\n", encoding="utf-8")

    audit_dir = tmp_path / "output" / "Demo" / "audit"
    archive_dir = tmp_path / "tmp" / "Demo" / "dedup_archive"

    report = dedup_raw_datasets(
        "Demo",
        datasets_dir=study_dir,
        audit_dir=audit_dir,
        archive_dir=archive_dir,
    )

    assert len(report.auto_resolved) == 1
    assert len(report.held_for_review) == 0
    remaining = list(study_dir.glob("*.csv"))
    assert len(remaining) == 1
    assert archive_dir.exists()


def test_tier1_row_count_mismatch_holds_for_review(tmp_path: Path) -> None:
    study_dir = tmp_path / "data" / "raw" / "Demo" / "datasets"
    study_dir.mkdir(parents=True)

    (study_dir / "dup.csv").write_text("H1\n1\n2\n", encoding="utf-8")
    (study_dir / "dup1.csv").write_text("H1\n1\n", encoding="utf-8")

    audit_dir = tmp_path / "output" / "Demo" / "audit"
    report = dedup_raw_datasets(
        "Demo",
        datasets_dir=study_dir,
        audit_dir=audit_dir,
        archive_dir=tmp_path / "tmp" / "Demo" / "dedup_archive",
    )

    assert report.held_for_review
    assert len(list(study_dir.glob("*.csv"))) == 2
    review_files = list(audit_dir.rglob("duplicate_review_report.md"))
    assert review_files


def test_auto_resolved_merge_writes_value_free_audit_record(tmp_path: Path) -> None:
    """Note 4: every AUTO-resolved decision leaves an on-disk audit record."""
    import json

    study_dir = tmp_path / "data" / "raw" / "Demo" / "datasets"
    study_dir.mkdir(parents=True)
    (study_dir / "form_a.csv").write_text("H1,H2\n1,2\n3,4\n", encoding="utf-8")
    (study_dir / "form_a1.csv").write_text("H1,H2\n1,2\n3,4\n", encoding="utf-8")

    audit_dir = tmp_path / "output" / "Demo" / "audit"
    report = dedup_raw_datasets(
        "Demo",
        datasets_dir=study_dir,
        audit_dir=audit_dir,
        archive_dir=tmp_path / "arch",
    )

    assert len(report.merge_decisions) == 1
    decision = report.merge_decisions[0]
    assert decision["tier"] == "tier1_perfect_duplicate"
    assert decision["action"] == "dataset_duplicate_file"

    report_json = audit_dir / "dataset_dedup" / "merge_report.json"
    assert report_json.is_file()
    data = json.loads(report_json.read_text(encoding="utf-8"))
    assert data["auto_resolved_merges"][0]["kept"] == decision["kept"]
    assert (audit_dir / "dataset_dedup" / "dataset_duplicate_merge_report.md").is_file()
    # value-free: cell values (3,4) never appear in the audit record
    assert "3,4" not in report_json.read_text(encoding="utf-8")
