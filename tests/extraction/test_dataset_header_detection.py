"""Tests for P2.2: Header-row detection in the dataset extraction leg.

Covers three Excel fixture cases:
  (a) Banner row above header — single-cell row before the real column names.
  (b) Unit row between header and data — the unit row appears as first data row.
  (c) Footer totals at the bottom — TOTAL row is NOT included as a data row.

Also tests the shared helpers directly:
  - split_sheet_into_tables  (segmentation only)
  - promote_header           (header detection + banner-skip + footer trim)

And validates end-to-end via _read_tabular_file in dataset_pipeline.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from scripts.extraction.io.sheet_split import promote_header, split_sheet_into_tables


# ---------------------------------------------------------------------------
# Helpers — build in-memory xlsx bytes
# ---------------------------------------------------------------------------


def _make_xlsx(rows: list[list]) -> bytes:
    """Return xlsx bytes for the given list-of-rows (openpyxl)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixture (a): banner row above header
#
# Sheet layout (row numbers are 1-based Excel rows):
#   Row 1: "Study: HIV-TB Cohort 2020"   ← banner (single populated cell)
#   Row 2: "subject_id", "age", "result"  ← real header
#   Row 3: "S001", 34, "positive"         ← data
#   Row 4: "S002", 28, "negative"         ← data
# ---------------------------------------------------------------------------


BANNER_ROWS: list[list] = [
    ["Study: HIV-TB Cohort 2020", None, None],
    ["subject_id", "age", "result"],
    ["S001", 34, "positive"],
    ["S002", 28, "negative"],
]


@pytest.fixture()
def banner_xlsx(tmp_path: Path) -> Path:
    path = tmp_path / "banner_fixture.xlsx"
    path.write_bytes(_make_xlsx(BANNER_ROWS))
    return path


class TestBannerRowFixture:
    """Fixture (a): banner row above the real header."""

    def _raw_df(self) -> pd.DataFrame:
        """Return the sheet parsed with header=None (as the pipeline does)."""
        buf = io.BytesIO(_make_xlsx(BANNER_ROWS))
        return pd.read_excel(buf, header=None, keep_default_na=False, na_values=[""])

    def test_split_gives_one_segment(self):
        """All rows are non-empty → one table segment."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        assert len(tables) == 1

    def test_promote_header_detects_real_header(self):
        """promote_header must skip the banner and use row 2 as columns."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0])

        # Column names must match the real header row, not the banner.
        assert list(result.columns) == ["subject_id", "age", "result"]

    def test_data_rows_start_immediately_after_header(self):
        """First data row is S001, not the banner text."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0])

        assert len(result) == 2
        assert result.iloc[0]["subject_id"] == "S001"
        assert result.iloc[1]["subject_id"] == "S002"

    def test_banner_text_not_in_data(self):
        """The banner string 'Study: HIV-TB Cohort 2020' must not appear as data."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0])

        for col in result.columns:
            assert "Study:" not in str(col)
        all_values = result.values.flatten().tolist()
        assert not any("Study:" in str(v) for v in all_values)

    def test_end_to_end_via_read_tabular_file(self, banner_xlsx: Path):
        """_read_tabular_file must return the correct columns and row count."""
        from scripts.extraction.dataset_pipeline import _read_tabular_file

        sheets = _read_tabular_file(banner_xlsx)
        assert len(sheets) == 1
        name, df = sheets[0]
        assert list(df.columns) == ["subject_id", "age", "result"]
        assert len(df) == 2


# ---------------------------------------------------------------------------
# Fixture (b): unit row between header and data
#
# Sheet layout:
#   Row 1: "subject_id", "weight_kg", "height_cm"  ← header
#   Row 2: "(ID)",        "kg",         "cm"         ← units (v1: kept as data row)
#   Row 3: "S001",        72.5,         178.0
#   Row 4: "S002",        65.0,         165.0
#
# v1 behaviour: the unit row IS included as the first data row.
# This is documented in the promote_header docstring.
# ---------------------------------------------------------------------------


UNIT_ROWS: list[list] = [
    ["subject_id", "weight_kg", "height_cm"],
    ["(ID)", "kg", "cm"],
    ["S001", 72.5, 178.0],
    ["S002", 65.0, 165.0],
]


@pytest.fixture()
def unit_xlsx(tmp_path: Path) -> Path:
    path = tmp_path / "unit_fixture.xlsx"
    path.write_bytes(_make_xlsx(UNIT_ROWS))
    return path


class TestUnitRowFixture:
    """Fixture (b): unit row between header and data — v1 keeps it as data."""

    def _raw_df(self) -> pd.DataFrame:
        buf = io.BytesIO(_make_xlsx(UNIT_ROWS))
        return pd.read_excel(buf, header=None, keep_default_na=False, na_values=[""])

    def test_columns_are_real_header(self):
        """Columns should be the header row, not the unit row."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0])
        assert list(result.columns) == ["subject_id", "weight_kg", "height_cm"]

    def test_unit_row_is_first_data_row(self):
        """v1 behaviour: units appear as the first data row (not filtered)."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0])
        # 3 rows total: unit row + 2 data rows
        assert len(result) == 3
        assert result.iloc[0]["subject_id"] == "(ID)"

    def test_real_data_rows_present(self):
        """S001 and S002 must be present after the unit row."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0])
        assert result.iloc[1]["subject_id"] == "S001"
        assert result.iloc[2]["subject_id"] == "S002"

    def test_end_to_end_via_read_tabular_file(self, unit_xlsx: Path):
        """_read_tabular_file must preserve the unit row as a data row."""
        from scripts.extraction.dataset_pipeline import _read_tabular_file

        sheets = _read_tabular_file(unit_xlsx)
        assert len(sheets) == 1
        _, df = sheets[0]
        assert list(df.columns) == ["subject_id", "weight_kg", "height_cm"]
        # 3 rows: unit row + 2 real data rows
        assert len(df) == 3


# ---------------------------------------------------------------------------
# Fixture (c): footer totals at the bottom
#
# Sheet layout:
#   Row 1: "subject_id", "score_a", "score_b"  ← header
#   Row 2: "S001",        85,         90
#   Row 3: "S002",        70,         75
#   Row 4: "TOTAL",       155,        165        ← footer to exclude
# ---------------------------------------------------------------------------


FOOTER_ROWS: list[list] = [
    ["subject_id", "score_a", "score_b"],
    ["S001", 85, 90],
    ["S002", 70, 75],
    ["TOTAL", 155, 165],
]


@pytest.fixture()
def footer_xlsx(tmp_path: Path) -> Path:
    path = tmp_path / "footer_fixture.xlsx"
    path.write_bytes(_make_xlsx(FOOTER_ROWS))
    return path


class TestFooterTotalsFixture:
    """Fixture (c): TOTAL footer row must NOT appear in extracted data."""

    def _raw_df(self) -> pd.DataFrame:
        buf = io.BytesIO(_make_xlsx(FOOTER_ROWS))
        return pd.read_excel(buf, header=None, keep_default_na=False, na_values=[""])

    def test_total_row_excluded_by_promote_header(self):
        """promote_header with footer_marker='total' drops the TOTAL row."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0], footer_marker="total")
        assert len(result) == 2
        subject_ids = result["subject_id"].tolist()
        assert "TOTAL" not in subject_ids
        assert "S001" in subject_ids
        assert "S002" in subject_ids

    def test_total_row_excluded_end_to_end(self, footer_xlsx: Path):
        """_read_tabular_file passes footer_marker='total' — TOTAL row dropped."""
        from scripts.extraction.dataset_pipeline import _read_tabular_file

        sheets = _read_tabular_file(footer_xlsx)
        assert len(sheets) == 1
        _, df = sheets[0]
        assert list(df.columns) == ["subject_id", "score_a", "score_b"]
        assert len(df) == 2
        assert "TOTAL" not in df["subject_id"].tolist()

    def test_data_values_correct(self):
        """S001 and S002 rows have correct values after footer removal."""
        raw = self._raw_df()
        tables = split_sheet_into_tables(raw)
        assert tables is not None
        result = promote_header(tables[0], footer_marker="total")
        row_s001 = result[result["subject_id"] == "S001"].iloc[0]
        assert int(row_s001["score_a"]) == 85
        assert int(row_s001["score_b"]) == 90


# ---------------------------------------------------------------------------
# split_sheet_into_tables — direct unit tests (no header logic)
# ---------------------------------------------------------------------------


class TestSplitSheetIntoTablesShared:
    """Direct tests of the shared segmentation helper."""

    def test_single_table(self):
        df = pd.DataFrame({0: [1, 2, 3], 1: [4, 5, 6]})
        tables = split_sheet_into_tables(df)
        assert tables is not None
        assert len(tables) == 1

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        tables = split_sheet_into_tables(df)
        assert tables == []

    def test_two_tables_separated_by_empty_row(self):
        df = pd.DataFrame({0: [1, 2, None, 4, 5], 1: [10, 20, None, 40, 50]})
        tables = split_sheet_into_tables(df)
        assert tables is not None
        assert len(tables) == 2

    def test_returns_none_on_structural_error(self, monkeypatch):
        """Simulate a structural error — helper must return None, not raise."""
        import scripts.extraction.io.sheet_split as ss

        original = pd.DataFrame.isnull

        def _explode(self):
            raise KeyError("simulated error")

        monkeypatch.setattr(pd.DataFrame, "isnull", _explode)
        df = pd.DataFrame({0: [1, 2], 1: [3, 4]})
        result = ss.split_sheet_into_tables(df)
        assert result is None


# ---------------------------------------------------------------------------
# promote_header — edge cases
# ---------------------------------------------------------------------------


class TestPromoteHeader:
    def test_no_banner_plain_table(self):
        """When row 0 is the header (no banner), it is promoted correctly."""
        df = pd.DataFrame(
            {0: ["col_a", "col_b", "col_c"], 1: [1, 2, 3], 2: [4, 5, 6]}
        ).T.reset_index(drop=True)
        # Build as raw DataFrame matching what split_sheet_into_tables returns:
        # columns are integer indices, header is in row 0.
        raw = pd.DataFrame(
            {
                0: ["col_a", 1, 2],
                1: ["col_b", 10, 20],
                2: ["col_c", 100, 200],
            }
        ).T.reset_index(drop=True)
        # Transpose to get rows as rows
        raw2 = pd.DataFrame(
            [[None, None, None],  # this row has 0 non-nulls
             ["col_a", "col_b", "col_c"],
             [1, 10, 100],
             [2, 20, 200]],
        )
        # The first row is all-None → skip; second row is the real header
        result = promote_header(raw2)
        assert list(result.columns) == ["col_a", "col_b", "col_c"]
        assert len(result) == 2

    def test_fallback_when_all_single_value_rows(self):
        """If no row has >1 non-null values, row 0 is used as the header."""
        df = pd.DataFrame({0: ["only_one", None], 1: [None, None]})
        result = promote_header(df)
        # row 0 has only 1 non-null — still used as fallback header
        assert "only_one" in result.columns or "Unnamed" in result.columns

    def test_nan_col_name_becomes_unnamed(self):
        """NaN values in the header row become 'Unnamed'."""
        df = pd.DataFrame({0: ["col_a", 1], 1: [None, 2], 2: ["col_c", 3]})
        result = promote_header(df)
        assert "col_a" in result.columns
        assert "Unnamed" in result.columns
        assert "col_c" in result.columns
