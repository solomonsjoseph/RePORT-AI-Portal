"""Lean-catalog files must remain under their hard size thresholds."""

from __future__ import annotations

from pathlib import Path

import pytest

import config


@pytest.mark.parametrize(
    "path,threshold_attr",
    [
        (config.LLM_SOURCE_DICTIONARY_CATALOG_PATH, "LEAN_CATALOG_DICTIONARY_MAX_BYTES"),
        (config.LLM_SOURCE_DATASET_SCHEMA_CATALOG_PATH, "LEAN_CATALOG_DATASET_SCHEMA_MAX_BYTES"),
        (config.STUDY_LLM_SOURCE_DIR / "study_metadata_catalog.json", "LEAN_CATALOG_STUDY_METADATA_MAX_BYTES"),
    ],
)
def test_catalog_under_size_threshold(path: Path, threshold_attr: str) -> None:
    if not path.is_file():
        pytest.skip(f"catalog not built yet: {path}")
    threshold = getattr(config, threshold_attr)
    actual = path.stat().st_size
    assert actual <= threshold, f"{path.name}: {actual} bytes > threshold {threshold}"
