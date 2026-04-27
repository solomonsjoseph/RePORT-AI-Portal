"""Tests for scripts/extraction/io/file_discovery.py — file discovery helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.extraction.io.file_discovery import (
    DEFAULT_JUNK_FILENAMES,
    discover_files,
)


class TestDiscoverFiles:
    def test_finds_xlsx_files(self, tmp_path: Path) -> None:
        (tmp_path / "data.xlsx").write_bytes(b"fake")
        (tmp_path / "info.csv").write_bytes(b"fake")
        result = discover_files(tmp_path, extensions=(".xlsx",))
        assert len(result) == 1
        assert result[0].name == "data.xlsx"

    def test_skips_hidden_files(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden.xlsx").write_bytes(b"fake")
        (tmp_path / "visible.xlsx").write_bytes(b"fake")
        result = discover_files(tmp_path, extensions=(".xlsx",))
        assert len(result) == 1
        assert result[0].name == "visible.xlsx"

    def test_skips_junk_files(self, tmp_path: Path) -> None:
        for junk in list(DEFAULT_JUNK_FILENAMES)[:2]:
            (tmp_path / junk).write_bytes(b"fake")
        (tmp_path / "real.xlsx").write_bytes(b"fake")
        result = discover_files(tmp_path, extensions=(".xlsx",))
        assert len(result) == 1

    def test_returns_sorted_list(self, tmp_path: Path) -> None:
        (tmp_path / "c.csv").write_bytes(b"")
        (tmp_path / "a.csv").write_bytes(b"")
        (tmp_path / "b.csv").write_bytes(b"")
        result = discover_files(tmp_path, extensions=(".csv",))
        names = [p.name for p in result]
        assert names == sorted(names)

    def test_empty_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="No supported"):
            discover_files(tmp_path)

    def test_no_extension_filter(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_bytes(b"")
        (tmp_path / "b.csv").write_bytes(b"")
        result = discover_files(tmp_path)
        assert len(result) == 2
