"""Tests for the Note 5 pre-promotion PHI guard gate enrichments.

Covers: the value-free `column` field on scan findings, the canonical
human-review path helpers, and the value-free presidio_failure.md writer.
"""

from __future__ import annotations

from pathlib import Path

from scripts.audit.review_paths import (
    presidio_failure_md_path,
    pycanon_report_md_path,
)
from scripts.security.llm_source_gate import LeakScanFinding, scan_tree_for_phi


def test_review_path_helpers_are_form_scoped(tmp_path: Path) -> None:
    p = presidio_failure_md_path(tmp_path, "1_Enrollment")
    assert p.name == "presidio_failure.md"
    assert p.parent.name == "1_Enrollment"
    assert "human_review" in p.parts and "presidio" in p.parts
    q = pycanon_report_md_path(tmp_path, "1_Enrollment")
    assert q.name == "pycanon_report.md"
    assert "pycanon" in q.parts


def test_leak_finding_captures_column_name(tmp_path: Path) -> None:
    """A residual-PHI hit in a JSONL leaf records the column NAME (value-free)."""
    jsonl = tmp_path / "1_Enrollment.jsonl"
    jsonl.write_text(
        '{"SUBJID": "RID_X", "CONTACT_EMAIL": "person@example.com"}\n', encoding="utf-8"
    )

    result = scan_tree_for_phi(tmp_path)

    assert not result.ok
    finding = result.findings[0]
    assert finding.column == "CONTACT_EMAIL"
    assert finding.pattern_name  # a blocking pattern name (e.g. EMAIL)
    # value-free: the dataclass has no field carrying the matched substring
    assert "person@example.com" not in repr(finding)


def test_presidio_failure_md_is_value_free(tmp_path: Path) -> None:
    from scripts.pipeline.host_pipeline import _write_presidio_failure_md
    from scripts.security.llm_source_gate import LeakScanResult
    from scripts.security.phi_guard_gate import PHIGuardResult
    from scripts.security.presidio_gate import PresidioScanResult

    finding = LeakScanFinding(
        relative_path="1_Enrollment.jsonl",
        line_number=1,
        pattern_name="EMAIL",
        column="CONTACT_EMAIL",
    )
    guard = PHIGuardResult(
        ok=False,
        presidio=PresidioScanResult(ok=True, findings=()),
        legacy=LeakScanResult(ok=False, findings=(finding,)),
        triggered_by=("legacy",),
    )

    _write_presidio_failure_md(tmp_path, guard)

    md = presidio_failure_md_path(tmp_path, "1_Enrollment")
    assert md.is_file()
    body = md.read_text(encoding="utf-8")
    # pattern + column names present; never a matched value
    assert "EMAIL" in body
    assert "CONTACT_EMAIL" in body
    assert "@example.com" not in body
    assert "person" not in body
