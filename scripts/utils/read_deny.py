"""OS-level read-deny helper for the Phase 3 cross-verify fix agent.

The fix agent must NOT be able to read row JSONLs. Production target on
Linux uses ``unshare(CLONE_NEWNS)`` or container bind mounts; Phase 3
ships the macOS-dev variant: chmod 000 on the path tree, with restore
on exit.

Tests assume non-root execution (chmod 000 has no effect for root).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from scripts.utils.logging_system import get_logger

logger = get_logger(__name__)


# Process-local map of path → original mode. Restore reads from here.
_ORIGINAL_MODES: dict[Path, int] = {}


def _record_mode(path: Path) -> None:
    if path in _ORIGINAL_MODES:
        return
    try:
        _ORIGINAL_MODES[path] = path.stat().st_mode
    except FileNotFoundError:
        pass


def _walk_tree(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_dir():
        out: list[Path] = [path]
        # Walk children before parent for deny so contents become unreadable
        # via dir denial; collect in stable order so restore reverses cleanly.
        for child in sorted(path.rglob("*")):
            out.append(child)
        return out
    return [path]


def enforce_read_deny(paths: list[Path]) -> None:
    """Set mode 0 on each path (and recursively on directory contents).

    Records the original mode so :func:`restore_read_access` can revert.
    Idempotent: a second call does not overwrite the originally recorded mode.
    """

    enforced = 0
    for p in paths:
        nodes = _walk_tree(p)
        # Record modes first (while paths are still readable), then chmod
        # deepest-first so we never block our own ability to stat children.
        for node in nodes:
            _record_mode(node)
        for node in sorted(nodes, key=lambda n: len(n.parts), reverse=True):
            try:
                os.chmod(node, 0)
                enforced += 1
            except (PermissionError, FileNotFoundError) as exc:
                logger.warning("read_deny.chmod_failed path=%s err=%s", str(node), exc)
    logger.info("read_deny.enforced paths=%d nodes=%d", len(paths), enforced)


def restore_read_access(paths: list[Path]) -> None:
    """Restore the recorded mode for each path (and contents)."""

    restored = 0
    # Restore in reverse order so directories regain readability before
    # their contents are restored (otherwise we cannot stat children).
    for p in paths:
        # Walking a mode-0 directory: rglob returns [] silently, so we miss
        # children. Always union the walk result with anything we recorded
        # under this path so files orphaned by an unreadable parent dir are
        # still restored. Sort shortest-path-first so dirs come back to
        # readable before their children are restored.
        walk_nodes = _walk_tree(p)
        recorded_under_p = [
            k for k in _ORIGINAL_MODES if k == p or p in k.parents
        ]
        union: dict[Path, None] = {}
        for n in walk_nodes:
            union[n] = None
        for n in recorded_under_p:
            union[n] = None
        nodes = sorted(union.keys(), key=lambda n: len(n.parts))
        for node in nodes:
            mode = _ORIGINAL_MODES.get(node)
            if mode is None:
                # Default mode if no record (rare).
                if node.exists() and node.is_dir():
                    mode = stat.S_IRWXU
                else:
                    mode = stat.S_IRUSR | stat.S_IWUSR
            try:
                os.chmod(node, mode)
                restored += 1
            except (PermissionError, FileNotFoundError) as exc:
                logger.warning("read_deny.restore_failed path=%s err=%s", str(node), exc)
    logger.info("read_deny.restored paths=%d nodes=%d", len(paths), restored)
