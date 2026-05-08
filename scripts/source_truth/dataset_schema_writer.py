"""Dual-write coordinator: copy ``<source>.jsonl`` to both legacy and new paths.

Phase 2 introduces ``output/<study>/llm_source/dataset_schema/files/<form>.jsonl``
alongside the legacy ``output/<study>/trio_bundle/datasets/<form>.jsonl``. Both
targets receive byte-identical copies until Phase 5 deletes the legacy path.

Atomic: each target writes through tempfile + replace. If either write fails,
the partial tempfile is unlinked and the exception propagates.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


def _atomic_copy_bytes(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with open(fd, "wb") as fh, open(source, "rb") as src:
            shutil.copyfileobj(src, fh)
        Path(tmp).replace(target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def dual_write_form(*, source_path: Path, legacy_path: Path, new_path: Path) -> None:
    """Atomically copy ``source_path`` to both ``legacy_path`` and ``new_path``.

    If ``source_path == legacy_path`` (legacy already lives at the canonical
    location), skip the redundant legacy copy and only write ``new_path``.
    """

    if source_path != legacy_path:
        _atomic_copy_bytes(source_path, legacy_path)
    _atomic_copy_bytes(source_path, new_path)
    logger.info(
        "dataset_dual_write.complete source=%s legacy=%s new=%s",
        str(source_path),
        str(legacy_path),
        str(new_path),
    )
