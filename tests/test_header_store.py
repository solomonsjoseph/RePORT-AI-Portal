"""Tests for the Note 6 shared header-extraction store reader.

All readers are fail-soft: missing run dir / store / entry returns None so callers
fall back to a direct read. Destruction is best-effort.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.extraction.header_store import (
    HEADER_STORE_FILENAME,
    destroy_header_store,
    header_store_path,
    load_header_store,
    resolve_headers,
    resolve_row_count,
    store_headers,
    store_row_count,
)


def _write_store(run_dir: Path, forms: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / HEADER_STORE_FILENAME).write_text(
        json.dumps({"study": "S", "forms": forms}), encoding="utf-8"
    )


def test_load_returns_none_without_run_dir():
    assert load_header_store(None) is None
    assert header_store_path(None) is None


def test_load_returns_none_when_missing(tmp_path: Path):
    assert load_header_store(tmp_path) is None


def test_load_returns_none_on_malformed(tmp_path: Path):
    (tmp_path / HEADER_STORE_FILENAME).write_text("{not json", encoding="utf-8")
    assert load_header_store(tmp_path) is None


def test_store_headers_and_row_count(tmp_path: Path):
    _write_store(
        tmp_path,
        {
            "1_Enrollment": {
                "source_file": "1_Enrollment.xlsx",
                "sheet_name": "Sheet1",
                "headers": ["SUBJID", "AGE"],
                "header_count": 2,
                "row_count": 30,
            }
        },
    )
    store = load_header_store(tmp_path)
    assert store_headers(store, "1_Enrollment") == ["SUBJID", "AGE"]
    assert store_row_count(store, "1_Enrollment") == 30
    # absent stem → None
    assert store_headers(store, "nope") is None
    assert store_row_count(store, "nope") is None


def test_resolve_prefers_store(tmp_path: Path):
    _write_store(
        tmp_path,
        {"f": {"headers": ["A", "B"], "row_count": 7}},
    )
    store = load_header_store(tmp_path)

    def _boom(_path):
        raise AssertionError("direct reader must not be called when store has the entry")

    assert resolve_headers(store, "f", tmp_path / "f.xlsx", reader=_boom) == ["A", "B"]
    assert resolve_row_count(store, "f", tmp_path / "f.xlsx", counter=_boom) == 7


def test_resolve_falls_back_to_direct_read_on_miss(tmp_path: Path):
    # No store at all → resolve must use the provided fallback reader/counter.
    assert resolve_headers(None, "f", tmp_path / "f.xlsx", reader=lambda _p: ["X"]) == ["X"]
    assert resolve_row_count(None, "f", tmp_path / "f.xlsx", counter=lambda _p: 42) == 42


def test_destroy_header_store(tmp_path: Path):
    _write_store(tmp_path, {"f": {"headers": ["A"], "row_count": 1}})
    assert (tmp_path / HEADER_STORE_FILENAME).exists()
    assert destroy_header_store(tmp_path) is True
    assert not (tmp_path / HEADER_STORE_FILENAME).exists()
    # second destroy is a no-op
    assert destroy_header_store(tmp_path) is False
    assert destroy_header_store(None) is False
