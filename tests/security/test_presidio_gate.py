"""Tests for the Presidio PHI residual scanner (Wave 3 C3)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.security.presidio_gate import (
    PresidioFinding,
    analyze_text,
    scan_tree_with_presidio,
)

_VALID_AADHAAR = "2341 2341 2346"  # Verhoeff-valid
_INVALID_AADHAAR = "2341 2341 2340"  # 12 digits, checksum-invalid


class TestAnalyzeText:
    def test_detects_email(self) -> None:
        assert "EMAIL" in {f.pattern_name for f in analyze_text("write me at a.b@c.com")}

    def test_detects_pan(self) -> None:
        assert "PAN" in {f.pattern_name for f in analyze_text("PAN ABCDE1234F")}

    def test_detects_ssn(self) -> None:
        assert "SSN" in {f.pattern_name for f in analyze_text("ssn 123-45-6789")}

    def test_detects_valid_aadhaar(self) -> None:
        assert "AADHAAR" in {f.pattern_name for f in analyze_text(f"id {_VALID_AADHAAR}")}

    def test_rejects_invalid_aadhaar_checksum(self) -> None:
        """Shared validator drops a 12-digit non-Verhoeff number (no drift)."""
        assert "AADHAAR" not in {f.pattern_name for f in analyze_text(f"id {_INVALID_AADHAAR}")}

    def test_rejects_placeholder_phone(self) -> None:
        assert analyze_text("call 9999999999") == []

    def test_detects_real_phone(self) -> None:
        assert "INDIAN_PHONE" in {f.pattern_name for f in analyze_text("call 9876543210")}

    def test_clean_clinical_text_has_no_findings(self) -> None:
        assert analyze_text("hemoglobin 12.5 g/dL on visit day 14") == []

    def test_findings_are_value_free(self) -> None:
        """A finding carries offsets + entity type but never the matched value."""
        f = analyze_text("a.b@c.com")[0]
        assert isinstance(f, PresidioFinding)
        # No attribute holds the raw matched substring.
        assert not hasattr(f, "value")
        assert not hasattr(f, "matched_text")
        assert f.pattern_name == "EMAIL"


class TestScanTree:
    def test_clean_tree_ok(self, tmp_path: Path) -> None:
        (tmp_path / "a.jsonl").write_text(
            json.dumps({"SUBJID": "RID_SUBJ_abcdefghijkl", "hgb": 12.5}) + "\n",
            encoding="utf-8",
        )
        assert scan_tree_with_presidio(tmp_path).ok

    def test_leaked_email_blocks(self, tmp_path: Path) -> None:
        (tmp_path / "a.jsonl").write_text(
            json.dumps({"note": "reach me at x@y.com"}) + "\n", encoding="utf-8"
        )
        res = scan_tree_with_presidio(tmp_path)
        assert not res.ok
        assert res.findings[0].pattern_name == "EMAIL"
        assert "matched content omitted" in res.detail

    def test_dictionary_mapping_dates_suppressed(self, tmp_path: Path) -> None:
        """A documentation date in a dictionary_mapping subtree is not PHI."""
        dm = tmp_path / "dictionary_mapping"
        dm.mkdir()
        (dm / "codes.jsonl").write_text(
            json.dumps({"help": "Use 1900-01-01 for an Unknown date"}) + "\n",
            encoding="utf-8",
        )
        assert scan_tree_with_presidio(tmp_path).ok

    def test_missing_dir_ok(self, tmp_path: Path) -> None:
        assert scan_tree_with_presidio(tmp_path / "nope").ok
