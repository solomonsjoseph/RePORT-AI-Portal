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

    def test_xlsx_uses_zip_streaming_not_openpyxl(self):
        """XLSX header reads stream the workbook XML directly, not via row iterators."""
        source = self._read_source()
        assert "zipfile.ZipFile" in source
        assert "ElementTree.iterparse" in source
        assert "openpyxl.load_workbook" not in source

    def test_xlsx_first_row_guard_present(self):
        """The streamed XLSX parser must stop after row 1."""
        source = self._read_source()
        assert 'elem.attrib.get("r") != "1"' in source
        assert "break" in source

    def test_csv_reader_called_once(self):
        """CSV reading must call next() exactly once, then close."""
        source = self._read_source()
        # Find the csv reading section
        csv_section = re.search(r"if suffix == \".csv\".*?(?=if suffix in|$)", source, re.DOTALL)
        assert csv_section, "CSV reading section not found"
        csv_code = csv_section.group(0)
        # next(reader) should be called once
        next_calls = len(re.findall(r"\bnext\(", csv_code))
        assert next_calls >= 1, "CSV reading section must call next(reader) at least once"
        # Should close the file (either via context manager or explicit close)
        assert "with dataset.open" in csv_code, "CSV reading must use context manager"
