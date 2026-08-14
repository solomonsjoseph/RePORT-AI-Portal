"""Tests for scripts.extraction.dataset_pipeline._filter_allowed_forms.

Covers the REPORTAL_ALLOWED_DATASET_FORMS env-var gate:
  (a) unset + production mode ON  -> RuntimeError (fail-closed)
  (b) unset + production mode OFF -> returns all files unchanged (dev/test)
  (c) set                         -> existing filename-based filtering
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.extraction.dataset_pipeline import _filter_allowed_forms


def _files(*names: str) -> list[Path]:
    return [Path(name) for name in names]


class TestFilterAllowedFormsUnset:
    def test_raises_in_production_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPORTAL_ALLOWED_DATASET_FORMS", raising=False)
        monkeypatch.setattr(config, "production_mode_enabled", lambda: True)

        files = _files("SC_Screening.xlsx", "ST_Sputum.xlsx")

        with pytest.raises(RuntimeError) as exc_info:
            _filter_allowed_forms(files)

        assert str(exc_info.value) == (
            "REPORTAL_ALLOWED_DATASET_FORMS is unset; run the pipeline through "
            "extract_to_llm_source so the header-only PHI review gate runs first."
        )

    def test_returns_all_files_outside_production_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REPORTAL_ALLOWED_DATASET_FORMS", raising=False)
        monkeypatch.setattr(config, "production_mode_enabled", lambda: False)

        files = _files("SC_Screening.xlsx", "ST_Sputum.xlsx")

        assert _filter_allowed_forms(files) == files


class TestFilterAllowedFormsSet:
    def test_filters_to_allowed_filenames_regardless_of_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTAL_ALLOWED_DATASET_FORMS", "SC_Screening.xlsx")
        monkeypatch.setattr(config, "production_mode_enabled", lambda: True)

        files = _files("SC_Screening.xlsx", "ST_Sputum.xlsx")

        assert _filter_allowed_forms(files) == _files("SC_Screening.xlsx")

    def test_filters_to_allowed_filenames_in_dev_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTAL_ALLOWED_DATASET_FORMS", "ST_Sputum.xlsx")
        monkeypatch.setattr(config, "production_mode_enabled", lambda: False)

        files = _files("SC_Screening.xlsx", "ST_Sputum.xlsx")

        assert _filter_allowed_forms(files) == _files("ST_Sputum.xlsx")

    def test_blank_allowlist_returns_all_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTAL_ALLOWED_DATASET_FORMS", " , , ")
        monkeypatch.setattr(config, "production_mode_enabled", lambda: True)

        files = _files("SC_Screening.xlsx", "ST_Sputum.xlsx")

        assert _filter_allowed_forms(files) == files
