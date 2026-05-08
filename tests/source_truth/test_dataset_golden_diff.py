"""Golden-diff: every byte of legacy and new dataset files matches."""

from __future__ import annotations

from pathlib import Path

import pytest

import config


def _legacy_dir() -> Path:
    return config.TRIO_DATASETS_DIR


def _new_dir() -> Path:
    return config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR


@pytest.mark.skipif(
    not _legacy_dir().is_dir() or not _new_dir().is_dir(),
    reason="One or both dataset dirs missing; run `make verify-and-promote` first",
)
def test_every_legacy_dataset_has_byte_identical_new_copy() -> None:
    legacy_files = sorted(_legacy_dir().glob("*.jsonl"))
    assert legacy_files, "no legacy datasets found in TRIO_DATASETS_DIR"
    mismatches: list[str] = []
    for legacy_file in legacy_files:
        new_file = _new_dir() / legacy_file.name
        if not new_file.is_file():
            mismatches.append(f"missing in new: {legacy_file.name}")
            continue
        if legacy_file.read_bytes() != new_file.read_bytes():
            mismatches.append(f"byte-mismatch: {legacy_file.name}")
    assert not mismatches, "\n".join(mismatches)
