"""llm_source catalog writers — dictionary catalog is a lean ToC."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.llm_source_catalogs import build_dictionary_catalog


def test_dictionary_catalog_is_lean_toc(tmp_path: Path) -> None:
    dict_dir = tmp_path / "dictionary"
    dict_dir.mkdir()
    (dict_dir / "10_TST.json").write_text('{"variables": ["A", "B"]}')
    (dict_dir / "11_IGRA.json").write_text('{"variables": ["X"]}')
    catalog_path = dict_dir / "catalog.json"
    build_dictionary_catalog(dictionary_dir=dict_dir, output_path=catalog_path)
    catalog = json.loads(catalog_path.read_text())
    assert catalog["schema_version"] == 1
    assert set(catalog["forms"].keys()) == {"10_TST", "11_IGRA"}
    assert catalog["forms"]["10_TST"]["file"] == "10_TST.json"
    assert catalog["forms"]["11_IGRA"]["file"] == "11_IGRA.json"


def test_catalog_under_size_threshold(tmp_path: Path) -> None:
    dict_dir = tmp_path / "dictionary"
    dict_dir.mkdir()
    for i in range(40):
        (dict_dir / f"form_{i:02d}.json").write_text("{}")
    catalog_path = dict_dir / "catalog.json"
    build_dictionary_catalog(dictionary_dir=dict_dir, output_path=catalog_path)
    import config

    assert catalog_path.stat().st_size <= config.LEAN_CATALOG_DICTIONARY_MAX_BYTES


def test_catalog_excludes_self(tmp_path: Path) -> None:
    """catalog.json must not list itself as a form."""
    dict_dir = tmp_path / "dictionary"
    dict_dir.mkdir()
    (dict_dir / "10_TST.json").write_text("{}")
    catalog_path = dict_dir / "catalog.json"
    catalog_path.write_text("{}")
    build_dictionary_catalog(dictionary_dir=dict_dir, output_path=catalog_path)
    catalog = json.loads(catalog_path.read_text())
    assert "catalog" not in catalog["forms"]
