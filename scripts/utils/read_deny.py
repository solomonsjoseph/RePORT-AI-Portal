"""OS-level read-deny helper for the Phase 3 cross-verify fix agent.

The fix agent must NOT be able to read row JSONLs. Production target on
Linux uses ``unshare(CLONE_NEWNS)`` or container bind mounts; Phase 3
ships the macOS-dev variant: chmod 000 on the path tree, with restore
on exit.

Tests assume non-root execution (chmod 000 has no effect for root).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


def _walk_tree(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_dir():
        return [path, *sorted(path.rglob("*"))]
    return [path]


@contextmanager
def read_deny(paths: list[Path]) -> Iterator[None]:
    """Chmod 000 on each *paths* entry for the duration of the with-block.

    Records original modes before denial; restores them on exit even when
    the block raises. Recursive on directories. Idempotent against
    overlapping paths.
    """
    original_modes: dict[Path, int] = {}

    # Record all current modes BEFORE any chmod runs — denying a parent
    # dir would otherwise block the stat() on its children.
    for p in paths:
        for node in _walk_tree(p):
            if node in original_modes:
                continue
            try:
                original_modes[node] = node.stat().st_mode
            except FileNotFoundError:
                pass

    enforced = 0
    try:
        # Deny deepest-first so dir-level denial cannot block stat'ing a
        # child we still need to chmod.
        for node in sorted(original_modes, key=lambda n: len(n.parts), reverse=True):
            try:
                os.chmod(node, 0)
                enforced += 1
            except (PermissionError, FileNotFoundError) as exc:
                logger.warning("read_deny.chmod_failed path=%s err=%s", str(node), exc)
        logger.info("read_deny.enforced paths=%d nodes=%d", len(paths), enforced)
        yield
    finally:
        restored = 0
        # Restore shortest-path-first so dirs regain readability before
        # their contents are chmod'd back.
        for node in sorted(original_modes, key=lambda n: len(n.parts)):
            try:
                os.chmod(node, original_modes[node])
                restored += 1
            except (PermissionError, FileNotFoundError) as exc:
                logger.warning("read_deny.restore_failed path=%s err=%s", str(node), exc)
        logger.info("read_deny.restored paths=%d nodes=%d", len(paths), restored)
