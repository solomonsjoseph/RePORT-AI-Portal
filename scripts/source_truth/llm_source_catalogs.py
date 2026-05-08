"""Lean-ToC writers for llm_source dictionary_mapping.

Three eventual catalogs (Tasks 5, 6 add the rest):
- ``dictionary_mapping/catalog.json`` — pointers to per-form JSONL files
  under ``dictionary_mapping/jsonl/``
- ``dataset_schema/catalog.json`` — Task 6
- ``study_metadata_catalog.json`` — Task 5

Every catalog stores pointers only — never inline payloads.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import config
from scripts.extraction.io.file_io import atomic_write_json
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


_SCHEMA_VERSION = 1


def relocate_dictionary(
    *,
    legacy_dir: Path | None = None,
    new_jsonl_dir: Path | None = None,
) -> int:
    """Copy the per-form dictionary tree from ``trio_bundle/dictionary/`` to
    ``llm_source/dictionary_mapping/jsonl/``, preserving the subdir+jsonl
    shape. Atomic per file. Idempotent. Legacy is preserved (Phase 5
    deletes it). Returns the number of files copied.
    """

    legacy_dir = legacy_dir if legacy_dir is not None else config.DICTIONARY_JSON_OUTPUT_DIR
    new_jsonl_dir = (
        new_jsonl_dir if new_jsonl_dir is not None else config.LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR
    )
    if not legacy_dir.is_dir():
        logger.info("relocate_dictionary.skipped legacy_dir_missing=%s", str(legacy_dir))
        return 0
    new_jsonl_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in sorted(legacy_dir.rglob("*.jsonl")):
        rel = src.relative_to(legacy_dir)
        target = new_jsonl_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
        try:
            with open(fd, "wb") as out_fh, open(src, "rb") as in_fh:
                shutil.copyfileobj(in_fh, out_fh)
            Path(tmp).replace(target)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        count += 1
    logger.info(
        "relocate_dictionary.done legacy=%s new=%s files=%d",
        str(legacy_dir),
        str(new_jsonl_dir),
        count,
    )
    return count


def build_dictionary_catalog(
    *,
    jsonl_dir: Path | None = None,
    output_path: Path | None = None,
) -> None:
    """Walk ``jsonl_dir`` for per-form subdirs (each containing a `*_table.jsonl`)
    and write a lean ToC at ``output_path``. Catalog entries point at
    ``jsonl/<form>/<form>_table.jsonl`` relative to the catalog's parent.
    """

    jsonl_dir = (
        jsonl_dir if jsonl_dir is not None else config.LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR
    )
    output_path = (
        output_path if output_path is not None else config.LLM_SOURCE_DICTIONARY_CATALOG_PATH
    )
    forms: dict[str, dict[str, Any]] = {}
    if jsonl_dir.is_dir():
        for sub in sorted(p for p in jsonl_dir.iterdir() if p.is_dir()):
            jsonl_files = sorted(sub.glob("*.jsonl"))
            if not jsonl_files:
                continue
            # Pick the canonical *_table.jsonl if present, else first jsonl
            canonical = next(
                (f for f in jsonl_files if f.name.endswith("_table.jsonl")), jsonl_files[0]
            )
            forms[sub.name] = {
                "file": f"jsonl/{sub.name}/{canonical.name}",
            }
    payload = {"schema_version": _SCHEMA_VERSION, "forms": forms}
    atomic_write_json(output_path, payload)
    logger.info("dictionary_catalog.written forms=%d output=%s", len(forms), str(output_path))


if __name__ == "__main__":
    relocate_dictionary()
    build_dictionary_catalog()
