# scripts/source_truth/dictionary_consolidator.py
"""Consolidate the multi-file dictionary extraction output into a single
`data_dictionary.json` artifact under `llm_source/`.

The existing dictionary producer writes multiple JSON files into
`output/{STUDY}/trio_bundle/dictionary/`. This consolidator reads those
files, merges them under a single artifact wrapper, and writes the
result to `output/{STUDY}/llm_source/data_dictionary.json`.

The legacy multi-file output is left in place — Plan D retires it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DictionaryConsolidatorError", "consolidate_dictionary"]


class DictionaryConsolidatorError(RuntimeError):
    """Raised when the consolidator cannot proceed."""


def consolidate_dictionary(
    *,
    study: str,
    source_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Read every *.json under source_dir and write a consolidated artifact.

    Args:
        study: Study identifier.
        source_dir: Directory containing the multi-file dictionary output
            (typically `output/{study}/trio_bundle/dictionary/`).
        output_path: Where to write the consolidated artifact (typically
            `output/{study}/llm_source/data_dictionary.json`).

    Returns:
        A summary dict with keys: study, output_path, files_consolidated.
    """
    if not source_dir.is_dir():
        raise DictionaryConsolidatorError(
            f"source_dir does not exist or is not a directory: {source_dir}"
        )

    tables: dict[str, Any] = {}
    files = sorted(source_dir.glob("*.json"))
    for path in files:
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DictionaryConsolidatorError(
                f"failed to parse {path}: {exc}"
            ) from exc
        tables[path.stem] = content

    artifact = {
        "artifact_type": "study_data_dictionary",
        "source": "dictionary_workbook",
        "study": study,
        "tables": tables,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(artifact, sort_keys=True, indent=2, ensure_ascii=False)
    output_path.write_text(encoded + "\n", encoding="utf-8")

    return {
        "study": study,
        "output_path": str(output_path),
        "files_consolidated": len(files),
    }
