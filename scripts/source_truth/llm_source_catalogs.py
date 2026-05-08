"""Lean-ToC writers for llm_source catalogs.

Three catalogs:
- ``dictionary/catalog.json`` — pointers to per-form dictionary JSONs
- ``dataset_schema/catalog.json`` — per-form dataset file pointers + handling summary (Task 6)
- ``study_metadata_catalog.json`` — pointers to per-form evidence packs (Task 5)

Every catalog stores pointers only — never inline payloads. Hard size
thresholds enforced by ``test_lean_catalog_size_thresholds.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config
from scripts.extraction.io.file_io import atomic_write_json
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_SCHEMA_VERSION = 1


def build_dictionary_catalog(
    *,
    dictionary_dir: Path | None = None,
    output_path: Path | None = None,
) -> None:
    """Walk ``dictionary_dir`` for per-form JSONs and write a lean ToC."""

    dictionary_dir = (
        dictionary_dir if dictionary_dir is not None else config.STUDY_LLM_SOURCE_DIR / "dictionary"
    )
    output_path = (
        output_path if output_path is not None else config.LLM_SOURCE_DICTIONARY_CATALOG_PATH
    )
    forms: dict[str, dict[str, Any]] = {}
    if dictionary_dir.exists():
        for f in sorted(dictionary_dir.glob("*.json")):
            if f.name == "catalog.json":
                continue
            forms[f.stem] = {"file": f.name}
    payload = {"schema_version": _SCHEMA_VERSION, "forms": forms}
    atomic_write_json(output_path, payload)
    logger.info("dictionary_catalog.written forms=%d output=%s", len(forms), str(output_path))


if __name__ == "__main__":
    build_dictionary_catalog()
