"""Tests for canonical human-review path helpers."""

from __future__ import annotations

from pathlib import Path

from scripts.audit.review_paths import (
    dataset_jsonl_union_review_path,
    excel_duplicate_review_path,
    is_sot_review_report_path,
    legacy_sot_review_report_path,
    publish_sot_joined_gate_md_path,
    resolve_sot_review_report_path,
    sot_review_report_path,
)


def test_sot_review_report_path_under_human_review_sot(tmp_path: Path) -> None:
    audit_dir = tmp_path / "output" / "Indo-VAP" / "audit"
    path = sot_review_report_path(audit_dir, "15_Feces")
    assert path == audit_dir / "human_review" / "sot" / "15_Feces" / "review_report.md"


def test_dataset_jsonl_union_review_path(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    path = dataset_jsonl_union_review_path(audit_dir, "14_CaseControl")
    assert path == audit_dir / "human_review" / "datasets" / "14_CaseControl" / "jsonl_union_review.md"


def test_excel_duplicate_review_path(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    path = excel_duplicate_review_path(audit_dir, "18_TargConcom")
    assert path == audit_dir / "human_review" / "excel" / "18_TargConcom" / "duplicate_review_report.md"


def test_publish_sot_joined_gate_md_path(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    path = publish_sot_joined_gate_md_path(audit_dir, "run_abc123")
    assert path == audit_dir / "human_review" / "publish" / "run_abc123" / "sot_joined_gate.md"


def test_is_sot_review_report_path_canonical_and_legacy() -> None:
    canonical = Path("output/X/audit/human_review/sot/1/review_report.md")
    legacy = Path("output/X/audit/Sot_review/1/review_report.md")
    other = Path("output/X/audit/human_review/datasets/1/jsonl_union_review.md")
    assert is_sot_review_report_path(canonical)
    assert is_sot_review_report_path(legacy)
    assert not is_sot_review_report_path(other)


def test_resolve_sot_review_report_path_prefers_canonical(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    canonical = sot_review_report_path(audit_dir, "15_Feces")
    legacy = legacy_sot_review_report_path(audit_dir, "15_Feces")
    canonical.parent.mkdir(parents=True)
    canonical.write_text("canonical", encoding="utf-8")
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
    assert resolve_sot_review_report_path(audit_dir, "15_Feces") == canonical


def test_resolve_sot_review_report_path_falls_back_to_legacy(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    legacy = legacy_sot_review_report_path(audit_dir, "15_Feces")
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
    assert resolve_sot_review_report_path(audit_dir, "15_Feces") == legacy
