"""Assertion 17: no un-capped age survives in a cap-ruled published column.

Output invariant completing assertion 12. ``cap_numeric`` clamps every numeric
value strictly above the age threshold (HIPAA Safe Harbor §164.514(b)(2)(i)(C),
age > 89 → "90+") to the label; this assertion re-runs that exact predicate over
the PUBLISHED JSONL and fails if any value that would still be clamped is present
— i.e. capping did not run on a cap-ruled column. No other gate checks this (the
residual PHI scanner cannot flag a bare age without false-positiving on glucose /
height / lab values).

Tests use the default scrub config (``cap_fields`` includes ``IS_AGE``,
threshold=89, label="90+"). Count-only assertion — detail carries form:column +
count, never a value.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.skills.extract_to_llm_source import (
    _verify_assertion_17_cap_application_complete,
)


def _write_rows(dataset_dir: Path, form_name: str, rows: list[dict]) -> None:
    """Write a published JSONL with the given row dicts."""
    dataset_dir.mkdir(parents=True, exist_ok=True)
    path = dataset_dir / f"{form_name}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class TestCapApplicationComplete:
    def test_uncapped_age_over_threshold_fails(self, tmp_path: Path) -> None:
        """A bare numeric age > 89 left in a cap-ruled column is a leak -> fail."""
        ds = tmp_path / "files"
        _write_rows(ds, "1_Form", [{"IS_AGE": "45"}, {"IS_AGE": "92"}])
        result, detail = _verify_assertion_17_cap_application_complete(ds, "Test-Study")
        assert result == "fail"
        assert "IS_AGE" in detail
        # count-only: the offending value (92) must NEVER appear in the detail
        assert "92" not in detail

    def test_all_ages_at_or_below_threshold_passes(self, tmp_path: Path) -> None:
        """Ages <= threshold need no capping -> pass (capping affects the tail only)."""
        ds = tmp_path / "files"
        _write_rows(ds, "1_Form", [{"IS_AGE": "45"}, {"IS_AGE": "89"}, {"IS_AGE": "0"}])
        result, _ = _verify_assertion_17_cap_application_complete(ds, "Test-Study")
        assert result == "pass"

    def test_capped_label_passes(self, tmp_path: Path) -> None:
        """A value already collapsed to the '90+' label is non-numeric -> pass."""
        ds = tmp_path / "files"
        _write_rows(ds, "1_Form", [{"IS_AGE": "90+"}, {"IS_AGE": "70"}])
        result, _ = _verify_assertion_17_cap_application_complete(ds, "Test-Study")
        assert result == "pass"

    def test_categorical_text_in_age_named_field_passes(self, tmp_path: Path) -> None:
        """The NC_AGE case: categorical text in an age-named field never false-positives.

        ``cap_numeric`` returns was_capped=False on non-numeric content, so a coded
        age field carrying free text (even with an embedded 2-digit number) is not
        flagged — capping is a no-op on it by design, and it carries no bare age.
        """
        ds = tmp_path / "files"
        _write_rows(
            ds,
            "1_Form",
            [{"IS_AGE": "Not applicable 12 reason"}, {"IS_AGE": "declined"}],
        )
        result, _ = _verify_assertion_17_cap_application_complete(ds, "Test-Study")
        assert result == "pass"

    def test_non_cap_column_with_high_number_passes(self, tmp_path: Path) -> None:
        """A high number in a NON-cap column (e.g. a lab value) is not flagged."""
        ds = tmp_path / "files"
        _write_rows(ds, "1_Form", [{"CBC_MCV": "200"}, {"HC_HEIGHT": "130"}])
        result, _ = _verify_assertion_17_cap_application_complete(ds, "Test-Study")
        assert result == "pass"

    def test_empty_dir_passes(self, tmp_path: Path) -> None:
        """No published files -> nothing to verify -> pass."""
        ds = tmp_path / "files"
        ds.mkdir(parents=True, exist_ok=True)
        result, _ = _verify_assertion_17_cap_application_complete(ds, "Test-Study")
        assert result == "pass"

    def test_count_aggregates_across_rows(self, tmp_path: Path) -> None:
        """Multiple over-threshold values report an aggregate count, no values."""
        ds = tmp_path / "files"
        _write_rows(ds, "1_Form", [{"IS_AGE": "91"}, {"IS_AGE": "95"}, {"IS_AGE": "40"}])
        result, detail = _verify_assertion_17_cap_application_complete(ds, "Test-Study")
        assert result == "fail"
        assert "IS_AGE=2" in detail
