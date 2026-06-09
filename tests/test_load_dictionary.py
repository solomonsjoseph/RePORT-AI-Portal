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
    _strip_doc_urls,
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
# _strip_doc_urls — publish-time URL scrub (leak-gate alignment)
# ═══════════════════════════════════════════════════════════════════════════


class TestStripDocUrls:
    """URLs in dictionary text are masked before publication.

    The residual leak gate (``scan_tree_for_phi``) blocks any URL in the
    published tree; the publisher must therefore never emit one.
    """

    def test_url_in_notes_masked(self):
        df = pd.DataFrame(
            {
                "Codes": ["BRA"],
                "Notes": ["https://en.wikipedia.org/wiki/ISO_3166-1_alpha-3"],
            }
        )
        out = _strip_doc_urls(df)
        assert out["Notes"].iloc[0] == "<URL_REMOVED>"
        assert out["Codes"].iloc[0] == "BRA"

    def test_url_embedded_in_text_masked_in_place(self):
        df = pd.DataFrame({"Notes": ["see http://example.org/std for details"]})
        out = _strip_doc_urls(df)
        assert "http" not in out["Notes"].iloc[0]
        assert out["Notes"].iloc[0].startswith("see ")
        assert out["Notes"].iloc[0].endswith(" for details")

    def test_non_string_cells_untouched(self):
        df = pd.DataFrame({"Mixed": [3, None, float("nan"), "plain text"]})
        out = _strip_doc_urls(df)
        assert out["Mixed"].iloc[0] == 3
        assert out["Mixed"].iloc[3] == "plain text"

    def test_masked_output_passes_leak_gate_pattern(self):
        """The mask token itself must never re-trip the gate's URL pattern."""
        from scripts.security.phi_patterns import BLOCKING_PATTERNS

        url_pat = next(pat for name, pat in BLOCKING_PATTERNS if name == "URL")
        df = pd.DataFrame({"Notes": ["ref: https://who.int/x and http://cdc.gov/y"]})
        out = _strip_doc_urls(df)
        assert url_pat.search(out["Notes"].iloc[0]) is None


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
