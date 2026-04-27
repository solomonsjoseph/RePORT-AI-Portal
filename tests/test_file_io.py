"""Tests for scripts/extraction/io/file_io.py — atomic write helpers."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.extraction.io.file_io import (
    JSONL_EXT,
    atomic_write_json,
    atomic_write_jsonl,
)


class TestAtomicWriteJsonl:
    def test_writes_valid_jsonl(self, tmp_path: Path) -> None:
        records = [{"a": 1}, {"b": 2}]
        out = tmp_path / "out.jsonl"
        atomic_write_jsonl(out, records)
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "dir" / "out.jsonl"
        atomic_write_jsonl(out, [{"x": 1}])
        assert out.exists()

    def test_empty_records_produces_empty_file(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.jsonl"
        atomic_write_jsonl(out, [])
        assert out.exists()
        assert out.read_text() == ""

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        out = tmp_path / "overwrite.jsonl"
        atomic_write_jsonl(out, [{"old": True}])
        atomic_write_jsonl(out, [{"new": True}])
        data = json.loads(out.read_text().strip())
        assert data == {"new": True}

    def test_unicode_content(self, tmp_path: Path) -> None:
        out = tmp_path / "unicode.jsonl"
        atomic_write_jsonl(out, [{"name": "日本語テスト"}])
        data = json.loads(out.read_text().strip())
        assert data["name"] == "日本語テスト"

    def test_string_path_accepted(self, tmp_path: Path) -> None:
        out = str(tmp_path / "str_path.jsonl")
        atomic_write_jsonl(out, [{"ok": True}])
        assert Path(out).exists()

    def test_no_temp_file_left_on_success(self, tmp_path: Path) -> None:
        out = tmp_path / "clean.jsonl"
        atomic_write_jsonl(out, [{"a": 1}])
        remaining = list(tmp_path.glob("*.tmp"))
        assert len(remaining) == 0


class TestAtomicWriteJson:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        out = tmp_path / "out.json"
        atomic_write_json(out, {"key": "value"})
        data = json.loads(out.read_text())
        assert data == {"key": "value"}

    def test_pretty_printed_by_default(self, tmp_path: Path) -> None:
        out = tmp_path / "pretty.json"
        atomic_write_json(out, {"a": 1, "b": 2})
        text = out.read_text()
        assert "\n" in text  # pretty-printed has newlines

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        out = tmp_path / "deep" / "nested" / "out.json"
        atomic_write_json(out, {"x": 1})
        assert out.exists()


class TestConstants:
    def test_jsonl_ext(self) -> None:
        assert JSONL_EXT == ".jsonl"
