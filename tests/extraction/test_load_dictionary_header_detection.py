"""Tests for scripts.extraction.load_dictionary._process_and_save_tables.

Covers the row-0 header-promotion guard added alongside
scripts.extraction.io.sheet_split.promote_header's fail-closed fix:
  (a) a normal table (row 0 has >1 non-null value) is processed and saved
      unchanged — no regression from the added guard.
  (b) a table where row 0 has at most one non-null value is skipped (not
      silently promoted to columns) and no JSONL file is written for it.
  (c) other tables in the same batch are still processed when one is skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.extraction.load_dictionary import _process_and_save_tables


def _table(rows: list[list]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _read_jsonl_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestProcessAndSaveTablesHappyPath:
    def test_normal_header_row_is_promoted(self, tmp_path: Path) -> None:
        table = _table(
            [
                ["Question", "Type", "Notes"],
                ["Age at enrollment", "numeric", ""],
                ["Sex", "categorical", "M/F"],
            ]
        )

        ok = _process_and_save_tables([table], "MySheet", tmp_path)

        assert ok is True
        out_files = list(tmp_path.rglob("*.jsonl"))
        assert len(out_files) == 1
        records = _read_jsonl_records(out_files[0])
        assert [r["Question"] for r in records] == ["Age at enrollment", "Sex"]


class TestProcessAndSaveTablesNoHeaderRow:
    def test_row_with_at_most_one_non_null_value_is_skipped(self, tmp_path: Path) -> None:
        # Row 0 has a single non-null value -> cannot be a real header row.
        table = _table(
            [
                ["only_one_value", None, None],
                ["a", "b", "c"],
            ]
        )

        ok = _process_and_save_tables([table], "MySheet", tmp_path)

        assert ok is False
        assert list(tmp_path.rglob("*.jsonl")) == []

    def test_other_tables_in_batch_still_processed(self, tmp_path: Path) -> None:
        bad_table = _table([["only_one_value", None], ["x", "y"]])
        good_table = _table(
            [
                ["Question", "Type"],
                ["Weight", "numeric"],
            ]
        )

        ok = _process_and_save_tables([bad_table, good_table], "MySheet", tmp_path)

        assert ok is False  # at least one table in the batch failed
        out_files = list(tmp_path.rglob("*.jsonl"))
        assert len(out_files) == 1
        records = _read_jsonl_records(out_files[0])
        assert records[0]["Question"] == "Weight"

    def test_duplicate_column_names_still_deduplicated(self, tmp_path: Path) -> None:
        """Regression: the added guard must not disturb existing dedup behaviour."""
        table = _table(
            [
                ["Question", "Question", "Notes"],
                ["Age", "Age unit", "years"],
            ]
        )

        ok = _process_and_save_tables([table], "MySheet", tmp_path)

        assert ok is True
        out_files = list(tmp_path.rglob("*.jsonl"))
        records = _read_jsonl_records(out_files[0])
        assert set(records[0].keys()) >= {"Question", "Question_1", "Notes"}
