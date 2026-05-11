"""Pre-delete checksum manifest writer and legacy-directory remover.

Called by ``make clean-legacy`` after Phase 5a ensures no live code writes
to output/<study>/staging/ or output/<study>/human_review/.

The manifest captures a SHA-256 fingerprint of every file under the legacy
output sub-trees (``trio_bundle/``, ``staging/``, ``human_review/``) before
deletion, so the deletion is auditable and reversible-by-reconstruction.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

from scripts.extraction.io import atomic_write_json
from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)

_LEGACY_DIR_NAMES: tuple[str, ...] = ("trio_bundle", "staging", "human_review")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def write_pre_delete_manifest(*, output_root: Path, manifest_path: Path) -> dict:
    """Write a SHA-256 manifest of all files in legacy dirs to ``manifest_path``.

    Args:
        output_root: ``output/<study>/`` root directory.
        manifest_path: Destination JSON file (typically
            ``output/<study>/audit/lineage_manifest_pre_delete.json``).

    Returns:
        The manifest dict that was persisted.
    """
    output_root = Path(output_root)
    study = output_root.name
    entries: list[dict] = []

    for dir_name in _LEGACY_DIR_NAMES:
        legacy_dir = output_root / dir_name
        if not legacy_dir.is_dir():
            continue
        for fpath in sorted(legacy_dir.rglob("*")):
            if fpath.is_file():
                entries.append(
                    {
                        "path": str(fpath.relative_to(output_root)),
                        "sha256": _sha256_file(fpath),
                        "size_bytes": fpath.stat().st_size,
                    }
                )

    manifest = {
        "schema_version": "1.0",
        "study": study,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deleted_dirs": list(_LEGACY_DIR_NAMES),
        "deleted_files": entries,
    }
    atomic_write_json(manifest_path, manifest)
    logger.info(
        "pre-delete manifest: %d files captured for %d legacy dirs",
        len(entries),
        len(_LEGACY_DIR_NAMES),
    )
    return manifest


def delete_legacy_dirs(*, output_root: Path) -> None:
    """Remove legacy output dirs.

    Caller must call :func:`write_pre_delete_manifest` first so the deletion
    is auditable.
    """
    output_root = Path(output_root)
    for dir_name in _LEGACY_DIR_NAMES:
        target = output_root / dir_name
        if target.is_dir():
            shutil.rmtree(target)
            logger.info("deleted legacy dir: %s", target)
        else:
            logger.debug("legacy dir not present, skipping: %s", target)
