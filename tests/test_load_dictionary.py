"""Tests for scripts.extraction.load_dictionary — data dictionary extraction.

Covers: _deduplicate_columns, _split_sheet_into_tables,
discover_dictionary_files, process_excel_file, and process_csv_file.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import config
from scripts.extraction import load_dictionary as ld
from scripts.extraction.load_dictionary import (
    UNNAMED_COLUMN_PREFIX,
    _deduplicate_columns,
    _split_sheet_into_tables,
    discover_dictionary_files,
    load_study_dictionary,
)

# ═══════════════════════════════════════════════════════════════════════════
# _deduplicate_columns
# ═══════════════════════════════════════════════════════════════════════════


class TestDeduplicateColumns:
    def test_unique_columns_unchanged(self):
        result = _deduplicate_columns(["A", "B", "C"])
        assert result == ["A", "B", "C"]

    def test_duplicate_gets_suffix(self):
        result = _deduplicate_columns(["Name", "Name", "Age"])
        assert result == ["Name", "Name_1", "Age"]

    def test_triple_duplicate(self):
        result = _deduplicate_columns(["X", "X", "X"])
        assert result == ["X", "X_1", "X_2"]

    def test_none_becomes_unnamed(self):
        result = _deduplicate_columns([None, "Age"])
        assert result[0] == UNNAMED_COLUMN_PREFIX

    def test_nan_becomes_unnamed(self):
        result = _deduplicate_columns([float("nan"), "Name"])
        assert result[0] == UNNAMED_COLUMN_PREFIX

    def test_mixed_duplicates_and_none(self):
        result = _deduplicate_columns(["Name", "Name", None, None, "Name"])
        assert result == [
            "Name",
            "Name_1",
            UNNAMED_COLUMN_PREFIX,
            f"{UNNAMED_COLUMN_PREFIX}_1",
            "Name_2",
        ]


# ═══════════════════════════════════════════════════════════════════════════
# _split_sheet_into_tables
# ═══════════════════════════════════════════════════════════════════════════


class TestSplitSheetIntoTables:
    def test_single_table(self):
        df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
        tables = _split_sheet_into_tables(df)
        assert len(tables) == 1

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        tables = _split_sheet_into_tables(df)
        assert tables == []

    def test_tables_separated_by_empty_row(self):
        data = {
            "A": [1, 2, None, 4, 5],
            "B": [10, 20, None, 40, 50],
        }
        df = pd.DataFrame(data)
        tables = _split_sheet_into_tables(df)
        assert len(tables) == 2


class TestDiscoverDictionaryFiles:
    def test_finds_xlsx_and_csv_only(self, tmp_path: Path) -> None:
        (tmp_path / "dict.xlsx").write_bytes(b"fake")
        (tmp_path / "dict.csv").write_bytes(b"fake")
        (tmp_path / "legacy.xls").write_bytes(b"fake")

        assert [Path(p).name for p in discover_dictionary_files(tmp_path)] == [
            "dict.csv",
            "dict.xlsx",
        ]

    def test_legacy_xls_only_raises(self, tmp_path: Path) -> None:
        (tmp_path / "legacy.xls").write_bytes(b"fake")
        with pytest.raises(ValueError, match=r"Supported extensions: \.csv, \.xlsx"):
            discover_dictionary_files(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# load_study_dictionary — staging default (Task 2)
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadStudyDictionaryDefaultOutput:
    """load_study_dictionary defaults output to config.STAGING_DICTIONARY_DIR."""

    def test_default_output_is_staging(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,  # side-effect: patches config paths to tmp
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Build a minimal dictionary source directory with a single CSV.
        src = tmp_path / "dict_src"
        src.mkdir()
        (src / "dict.csv").write_text("code,label\nA,Apple\nB,Banana\n", encoding="utf-8")

        captured: dict[str, str] = {}

        def _fake_process_csv(*, csv_path: str, output_dir: str, preserve_na: bool) -> bool:
            captured["csv_path"] = csv_path
            captured["output_dir"] = output_dir
            captured["preserve_na"] = str(preserve_na)
            return True

        # Replace the worker so we only exercise the dispatcher's default path.
        monkeypatch.setattr(ld, "process_csv_file", _fake_process_csv)

        # No json_output_dir → default must be STAGING_DICTIONARY_DIR
        ok = load_study_dictionary(dictionary_dir=str(src))
        assert ok is True
        assert Path(captured["output_dir"]) == config.STAGING_DICTIONARY_DIR
        # Sanity: did NOT leak to trio_bundle
        assert Path(captured["output_dir"]) != config.DICTIONARY_JSON_OUTPUT_DIR

    def test_explicit_output_overrides_default(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,  # side-effect: patches config paths to tmp
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src = tmp_path / "dict_src2"
        src.mkdir()
        (src / "dict.csv").write_text("code,label\nA,Apple\n", encoding="utf-8")
        explicit = tmp_path / "explicit_out"

        captured: dict[str, str] = {}

        def _fake_process_csv(*, csv_path: str, output_dir: str, preserve_na: bool) -> bool:
            captured["output_dir"] = output_dir
            return True

        monkeypatch.setattr(ld, "process_csv_file", _fake_process_csv)

        ok = load_study_dictionary(
            dictionary_dir=str(src),
            json_output_dir=str(explicit),
        )
        assert ok is True
        assert Path(captured["output_dir"]) == explicit
