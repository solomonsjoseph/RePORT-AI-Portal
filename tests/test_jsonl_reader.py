"""Tests for scripts/extraction/io/jsonl_reader.py — JSONL line parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.extraction.io.jsonl_reader import JSONLParseError, load_json_object_line


class TestLoadJsonObjectLine:
    def test_valid_json_object(self) -> None:
        line = json.dumps({"key": "value"})
        result = load_json_object_line(line, source_path=Path("test.jsonl"), line_number=1)
        assert result == {"key": "value"}

    def test_empty_line_raises(self) -> None:
        with pytest.raises(JSONLParseError):
            load_json_object_line("", source_path=Path("test.jsonl"), line_number=1)

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(JSONLParseError):
            load_json_object_line("   \t  ", source_path=Path("test.jsonl"), line_number=2)

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(JSONLParseError):
            load_json_object_line("{bad json}", source_path=Path("test.jsonl"), line_number=3)

    def test_json_array_raises(self) -> None:
        with pytest.raises(JSONLParseError):
            load_json_object_line("[1, 2, 3]", source_path=Path("test.jsonl"), line_number=4)
