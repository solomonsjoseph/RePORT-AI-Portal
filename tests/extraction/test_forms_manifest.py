"""Tests for the per-study forms manifest gate in dataset_pipeline.

Each test materialises a minimal fake study layout under tmp_path so the
real Indo-VAP raw data is never touched.  Covers every gate branch:

  1. Manifest absent    → warning logged, extraction proceeds without raising.
  2. All files required → no raise.
  3. Required missing   → ManifestMismatchError.
  4. Unknown file       → ManifestMismatchError.
  5. Reject exact match → auto-skipped, info log, no raise, in rejected_files.
  6. Reject glob match  → auto-skipped, info log, no raise, in rejected_files.
  7. Optional missing   → info-level log only, no raise.
  8. Required ∩ reject  → ManifestMismatchError (manifest authoring conflict).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts.extraction.dataset_pipeline import (
    ManifestMismatchError,
    check_forms_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(datasets_dir: Path, content: str) -> None:
    """Write a _forms_manifest.yaml one level above datasets_dir."""
    study_dir = datasets_dir.parent
    (study_dir / "_forms_manifest.yaml").write_text(content)


def _touch(datasets_dir: Path, *names: str) -> None:
    """Create empty placeholder files in datasets_dir."""
    for name in names:
        (datasets_dir / name).touch()


# ---------------------------------------------------------------------------
# Branch 1: manifest absent → warning + no raise
# ---------------------------------------------------------------------------


class TestManifestAbsent:
    def test_no_raise_when_manifest_missing(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "1A_ICScreening.xlsx")

        with caplog.at_level(logging.WARNING, logger="scripts.extraction.dataset_pipeline"):
            # Must not raise
            check_forms_manifest(datasets_dir)

        assert any("no forms manifest" in r.message.lower() for r in caplog.records), (
            "Expected a warning about missing manifest"
        )


# ---------------------------------------------------------------------------
# Branch 2: manifest present, all files match required → no raise
# ---------------------------------------------------------------------------


class TestAllRequiredPresent:
    def test_clean_pass(self, tmp_path: Path) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "1A_ICScreening.xlsx", "6_HIV.xlsx")

        _write_manifest(
            datasets_dir,
            "required:\n  - 1A_ICScreening.xlsx\n  - 6_HIV.xlsx\noptional: []\nreject: []\n",
        )

        # Must not raise
        check_forms_manifest(datasets_dir)


# ---------------------------------------------------------------------------
# Branch 3: required form missing → ManifestMismatchError
# ---------------------------------------------------------------------------


class TestRequiredMissing:
    def test_raises_with_missing_form_name(self, tmp_path: Path) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx")  # 1A_ICScreening.xlsx is absent

        _write_manifest(
            datasets_dir,
            "required:\n  - 1A_ICScreening.xlsx\n  - 6_HIV.xlsx\noptional: []\nreject: []\n",
        )

        with pytest.raises(ManifestMismatchError) as exc_info:
            check_forms_manifest(datasets_dir)

        assert "1A_ICScreening.xlsx" in str(exc_info.value)
        assert "required form missing" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Branch 4: unknown file present → ManifestMismatchError
# ---------------------------------------------------------------------------


class TestUnknownFile:
    def test_raises_for_unknown_file(self, tmp_path: Path) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx", "random_file.xlsx")

        _write_manifest(
            datasets_dir,
            "required:\n  - 6_HIV.xlsx\noptional: []\nreject: []\n",
        )

        with pytest.raises(ManifestMismatchError) as exc_info:
            check_forms_manifest(datasets_dir)

        assert "random_file.xlsx" in str(exc_info.value)
        assert "not in manifest" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Branch 5: reject exact-name match → auto-skip, info log, no raise
# ---------------------------------------------------------------------------


class TestRejectExact:
    def test_auto_skips_rejected_exact_name(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx", "Paste Errors.xlsx")

        _write_manifest(
            datasets_dir,
            "required:\n  - 6_HIV.xlsx\noptional: []\nreject:\n  - Paste Errors.xlsx\n",
        )

        with caplog.at_level(logging.INFO, logger="scripts.extraction.dataset_pipeline"):
            result = check_forms_manifest(datasets_dir)

        assert "Paste Errors.xlsx" in result.rejected_files
        assert "6_HIV.xlsx" not in result.rejected_files
        assert any(
            "Paste Errors.xlsx" in r.message and "auto-skipped" in r.message
            for r in caplog.records
        ), "Expected an info-level auto-skip log for the reject-listed file"


# ---------------------------------------------------------------------------
# Branch 6: reject glob/fnmatch pattern → auto-skip, info log, no raise
# ---------------------------------------------------------------------------


class TestRejectGlob:
    def test_auto_skips_glob_matched_reject(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx", "2A_ICBaseline_1.xlsx")

        _write_manifest(
            datasets_dir,
            "required:\n  - 6_HIV.xlsx\noptional: []\nreject:\n  - '*_1.xlsx'\n",
        )

        with caplog.at_level(logging.INFO, logger="scripts.extraction.dataset_pipeline"):
            result = check_forms_manifest(datasets_dir)

        assert "2A_ICBaseline_1.xlsx" in result.rejected_files
        assert any(
            "2A_ICBaseline_1.xlsx" in r.message and "*_1.xlsx" in r.message
            for r in caplog.records
        ), "Expected an info-level auto-skip log naming the glob pattern"


# ---------------------------------------------------------------------------
# Branch 7: optional form missing → info log, no raise
# ---------------------------------------------------------------------------


class TestOptionalMissing:
    def test_no_raise_info_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx")
        # 30_Air_Quality.xlsx is optional but absent

        _write_manifest(
            datasets_dir,
            "required:\n  - 6_HIV.xlsx\noptional:\n  - 30_Air_Quality.xlsx\nreject: []\n",
        )

        with caplog.at_level(logging.INFO, logger="scripts.extraction.dataset_pipeline"):
            check_forms_manifest(datasets_dir)  # must not raise

        assert any("30_Air_Quality.xlsx" in r.message for r in caplog.records), (
            "Expected an info-level log mentioning the missing optional form"
        )


# ---------------------------------------------------------------------------
# Branch 8: required ∩ reject → ManifestMismatchError (authoring conflict)
# ---------------------------------------------------------------------------


class TestRequiredRejectConflict:
    def test_raises_when_required_form_also_listed_as_reject(self, tmp_path: Path) -> None:
        """A form cannot be both required and reject-listed.

        Surfacing this as a hard error prevents silent data loss when an
        operator accidentally adds a real form to the reject list.
        """
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx")

        _write_manifest(
            datasets_dir,
            "required:\n  - 6_HIV.xlsx\noptional: []\nreject:\n  - 6_HIV.xlsx\n",
        )

        with pytest.raises(ManifestMismatchError) as exc_info:
            check_forms_manifest(datasets_dir)

        assert "6_HIV.xlsx" in str(exc_info.value)
        assert "conflict" in str(exc_info.value).lower()
