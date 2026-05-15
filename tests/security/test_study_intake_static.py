"""Static analysis tests: data-isolation invariant enforcement in study_intake.py."""

import re
from pathlib import Path


class TestDataIsolationStaticGuards:
    """Verify that study_intake.py source code enforces the row-2+ isolation invariant."""

    @staticmethod
    def _read_source() -> str:
        """Read scripts/source_truth/study_intake.py as text."""
        # __file__ is tests/security/test_study_intake_static.py
        # parents[0] = tests/security, [1] = tests/, [2] = repo_root (RePORT-AI-Portal)
        module_path = Path(__file__).resolve().parents[2] / "scripts" / "source_truth" / "study_intake.py"
        return module_path.read_text(encoding="utf-8")

    def test_no_pandas_import(self):
        """Pandas allows iterating past row 1; must not be imported."""
        source = self._read_source()
        # Check for pandas import at module level
        assert not re.search(r"^\s*(?:import|from)\s+pandas\b", source, re.MULTILINE), (
            "study_intake.py must not import pandas (allows iteration past row 1)"
        )

    def test_no_comment_attribute(self):
        """openpyxl cell.comment exists but is never accessed."""
        source = self._read_source()
        # Reject patterns like .comment or \.comment
        assert not re.search(r"\.comment\b", source), (
            "study_intake.py must not access .comment attribute (off-sheet data vector)"
        )

    def test_no_sniffer(self):
        """csv.Sniffer reads entire file to detect dialect; forbidden."""
        source = self._read_source()
        assert not re.search(r"\bSniffer\b", source), (
            "study_intake.py must not use csv.Sniffer (reads entire file)"
        )

    def test_only_max_row_1(self):
        """Every max_row= parameter must be max_row=1."""
        source = self._read_source()
        # Find all max_row assignments
        matches = re.findall(r"max_row\s*=\s*(\d+)", source)
        assert matches, "No max_row assignments found (expected at least one)"
        for match in matches:
            assert match == "1", (
                f"Found max_row={match}; all max_row assignments must be max_row=1"
            )

    def test_iter_rows_includes_max_row(self):
        """Every iter_rows( call must include max_row=1."""
        source = self._read_source()
        # Find all iter_rows calls with their arguments
        # Pattern: iter_rows\( followed by anything until closing paren
        iter_rows_calls = re.findall(r"iter_rows\s*\([^)]*\)", source)
        assert iter_rows_calls, "No iter_rows calls found (expected at least one)"
        for call in iter_rows_calls:
            assert "max_row" in call, (
                f"iter_rows call missing max_row parameter: {call}"
            )
            assert "max_row=1" in call, (
                f"iter_rows call does not have max_row=1: {call}"
            )

    def test_csv_reader_called_once(self):
        """CSV reading must call next() exactly once, then close."""
        source = self._read_source()
        # Find the csv reading section
        csv_section = re.search(r"elif suffix == \".csv\".*?(?=else:|$)", source, re.DOTALL)
        assert csv_section, "CSV reading section not found"
        csv_code = csv_section.group(0)
        # next(reader) should be called once
        next_calls = len(re.findall(r"\bnext\(", csv_code))
        assert next_calls >= 1, "CSV reading section must call next(reader) at least once"
        # Should close the file (either via context manager or explicit close)
        assert "with open" in csv_code, "CSV reading must use context manager for file safety"

    def test_openpyxl_read_only_mode(self):
        """openpyxl must be opened in read_only=True mode."""
        source = self._read_source()
        # Find openpyxl.load_workbook calls
        wb_calls = re.findall(r"openpyxl\.load_workbook\([^)]*\)", source)
        assert wb_calls, "No openpyxl.load_workbook calls found"
        for call in wb_calls:
            assert "read_only=True" in call, (
                f"openpyxl.load_workbook must use read_only=True: {call}"
            )

    def test_no_data_only_false(self):
        """data_only=False is needed to prevent formula resolution (row 2+ data access)."""
        source = self._read_source()
        wb_calls = re.findall(r"openpyxl\.load_workbook\([^)]*\)", source)
        assert wb_calls, "No openpyxl.load_workbook calls found"
        for call in wb_calls:
            assert "data_only=False" in call, (
                f"openpyxl.load_workbook must use data_only=False: {call}"
            )
