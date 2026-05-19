"""Tests for the per-study forms manifest gate in dataset_pipeline.

Each test materialises a minimal fake study layout under tmp_path so the
real Indo-VAP raw data is never touched.  Covers every gate branch:

  1. Manifest absent    → warning logged, extraction proceeds without raising.
  2. All files required → no raise.
  3. Required missing   → ManifestMismatchError.
  4. Unknown file       → ManifestMismatchError.
  5. Reject exact match → ManifestMismatchError.
  6. Reject glob match  → ManifestMismatchError.
  7. Optional missing   → info-level log only, no raise.
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
# Branch 5: reject exact-name match → ManifestMismatchError
# ---------------------------------------------------------------------------


class TestRejectExact:
    def test_raises_for_rejected_exact_name(self, tmp_path: Path) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx", "Paste Errors.xlsx")

        _write_manifest(
            datasets_dir,
            "required:\n  - 6_HIV.xlsx\noptional: []\nreject:\n  - Paste Errors.xlsx\n",
        )

        with pytest.raises(ManifestMismatchError) as exc_info:
            check_forms_manifest(datasets_dir)

        assert "Paste Errors.xlsx" in str(exc_info.value)
        assert "rejected form" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Branch 6: reject glob/fnmatch pattern → ManifestMismatchError
# ---------------------------------------------------------------------------


class TestRejectGlob:
    def test_raises_for_glob_matched_reject(self, tmp_path: Path) -> None:
        datasets_dir = tmp_path / "datasets"
        datasets_dir.mkdir()
        _touch(datasets_dir, "6_HIV.xlsx", "2A_ICBaseline_1.xlsx")

        _write_manifest(
            datasets_dir,
            "required:\n  - 6_HIV.xlsx\noptional: []\nreject:\n  - '*_1.xlsx'\n",
        )

        with pytest.raises(ManifestMismatchError) as exc_info:
            check_forms_manifest(datasets_dir)

        assert "2A_ICBaseline_1.xlsx" in str(exc_info.value)
        assert "rejected form" in str(exc_info.value).lower()


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
