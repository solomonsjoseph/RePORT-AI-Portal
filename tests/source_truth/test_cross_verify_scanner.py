"""Cross-verify scanner — SAFE counts/booleans, no values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.cross_verify_scanner import scan


_FIXTURE = Path(__file__).parent.parent / "fixtures" / "cross_verify"


def test_emits_safe_report(tmp_path: Path) -> None:
    out = tmp_path / "safe.json"
    scan(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        output_path=out,
    )
    body = json.loads(out.read_text())
    assert body["schema_version"] == 1
    assert body["summary"]["forms"] == 1
    assert body["summary"]["variables_scanned"] == 3


def test_findings_have_only_safe_fields(tmp_path: Path) -> None:
    out = tmp_path / "safe.json"
    scan(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        output_path=out,
    )
    body = json.loads(out.read_text())
    allowed = {"form", "variable_id", "column_present", "scrubbed_count", "sot_action"}
    for f in body["findings"]:
        assert set(f.keys()) <= allowed, f"finding has forbidden keys: {set(f.keys()) - allowed}"


def test_drop_action_with_column_present_is_discrepancy(tmp_path: Path) -> None:
    out = tmp_path / "safe.json"
    scan(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        output_path=out,
    )
    body = json.loads(out.read_text())
    drop_findings = [f for f in body["findings"] if f["sot_action"] == "drop"]
    assert any(f["column_present"] for f in drop_findings)
    assert body["summary"]["discrepancies"] >= 1


def test_no_row_values_in_safe_report(tmp_path: Path) -> None:
    """SAFE report must contain none of the row values from the fixture JSONL."""
    out = tmp_path / "safe.json"
    scan(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        output_path=out,
    )
    raw = out.read_text()
    for forbidden in ("k1", "k2", "d1", "d2", "p1", "p2"):
        assert forbidden not in raw, f"row value {forbidden} leaked into SAFE report"


def test_column_present_correctly_classified(tmp_path: Path) -> None:
    out = tmp_path / "safe.json"
    scan(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        output_path=out,
    )
    body = json.loads(out.read_text())
    by_vid = {f["variable_id"]: f for f in body["findings"]}
    assert by_vid["KEEPER"]["column_present"] is True
    assert by_vid["DROPPED_VAR"]["column_present"] is True  # discrepancy: declared drop but still present
    assert by_vid["PSEUDO_VAR"]["column_present"] is True


def test_scrubbed_count_reflects_marker_rows(tmp_path: Path) -> None:
    out = tmp_path / "safe.json"
    scan(
        sot_dir=_FIXTURE / "data" / "SoT" / "Mini",
        dataset_files_dir=_FIXTURE / "dataset_schema" / "files",
        output_path=out,
    )
    body = json.loads(out.read_text())
    # Both fixture rows have _phi_scrubbed marker, so scrubbed_count = 2.
    keeper = next(f for f in body["findings"] if f["variable_id"] == "KEEPER")
    assert keeper["scrubbed_count"] == 2
