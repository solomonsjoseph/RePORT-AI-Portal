"""Dataset writer dual-write — both legacy and llm_source targets receive byte-identical files."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.source_truth.dataset_schema_writer import dual_write_form


def test_dual_write_creates_both_targets(tmp_path: Path) -> None:
    src = tmp_path / "src.jsonl"
    src.write_text('{"col1": "v1"}\n{"col1": "v2"}\n')
    legacy = tmp_path / "legacy" / "10_TST.jsonl"
    new = tmp_path / "new" / "10_TST.jsonl"
    dual_write_form(source_path=src, legacy_path=legacy, new_path=new)
    assert legacy.is_file()
    assert new.is_file()
    assert legacy.read_bytes() == new.read_bytes()
    assert legacy.read_bytes() == src.read_bytes()


def test_dual_write_atomic_no_partial_on_disk(tmp_path: Path) -> None:
    """If the second write fails, no partial tempfile remains."""
    src = tmp_path / "src.jsonl"
    src.write_text("payload\n")
    legacy = tmp_path / "legacy" / "10_TST.jsonl"
    new = tmp_path / "new" / "10_TST.jsonl"
    # Pre-create the new dir as a regular file to force a write error.
    new.parent.write_bytes(b"not-a-dir")
    with pytest.raises(OSError):
        dual_write_form(source_path=src, legacy_path=legacy, new_path=new)
    # No partial tempfiles left around in either parent.
    assert not list((tmp_path / "legacy").glob("*.tmp.*"))
    assert not (tmp_path / "new").is_dir() or not list((tmp_path / "new").glob("*.tmp.*"))


def test_dual_write_byte_identical_with_unicode(tmp_path: Path) -> None:
    src = tmp_path / "src.jsonl"
    src.write_text('{"name": "São Paulo"}\n')
    legacy = tmp_path / "legacy" / "f.jsonl"
    new = tmp_path / "new" / "f.jsonl"
    dual_write_form(source_path=src, legacy_path=legacy, new_path=new)
    assert legacy.read_bytes() == new.read_bytes() == src.read_bytes()


def test_dual_write_skips_legacy_when_source_equals_legacy(tmp_path: Path) -> None:
    """When source and legacy are the same path, skip the legacy copy and only write new."""
    legacy_and_src = tmp_path / "10_TST.jsonl"
    legacy_and_src.write_text("payload\n")
    new = tmp_path / "new" / "10_TST.jsonl"
    dual_write_form(source_path=legacy_and_src, legacy_path=legacy_and_src, new_path=new)
    assert new.read_bytes() == legacy_and_src.read_bytes()
