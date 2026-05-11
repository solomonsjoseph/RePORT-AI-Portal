"""Human-reviewed snapshot baseline helpers.

The snapshot baseline is a full copy of ``output/{STUDY}/trio_bundle/``
saved under ``data/snapshots/{STUDY}/`` after human review. It is the
operator-approved fallback for broken or incomplete live bundles.

The active operations are:

* save the current live trio bundle as the reviewed snapshot baseline;
* restore the reviewed snapshot baseline over the live trio bundle;
* check whether a reviewed snapshot baseline exists.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
from pathlib import Path

import config

__all__ = [
    "SnapshotError",
    "create_snapshot",
    "latest_snapshot_name",
    "list_snapshots",
    "main",
    "resolve_snapshot_name",
    "restore_snapshot",
    "snapshot_exists",
]


class SnapshotError(RuntimeError):
    """Raised when a snapshot operation cannot be completed."""


def _safe_rmtree(path: Path, *, ignore_errors: bool = False) -> None:
    """Delete *path*, refusing to follow symlinks at the root."""

    if path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path, ignore_errors=ignore_errors)


def _snapshot_root() -> Path:
    return Path(config.STUDY_SNAPSHOTS_DIR)


def _trio_root() -> Path:
    return Path(config.TRIO_BUNDLE_DIR)


def _harden_tree_modes(root: Path) -> None:
    """Set every dir under *root* to mode 0o700 and every file to 0o600."""

    with contextlib.suppress(OSError):
        root.chmod(0o700)
    for current_root, dirs, files in os.walk(str(root)):
        for d in dirs:
            with contextlib.suppress(OSError):
                (Path(current_root) / d).chmod(0o700)
        for f in files:
            with contextlib.suppress(OSError):
                (Path(current_root) / f).chmod(0o600)


def snapshot_exists() -> bool:
    """Return True when the reviewed snapshot baseline has usable content."""

    root = _snapshot_root()
    return root.is_dir() and (
        any((root / "datasets").glob("*.jsonl"))
        or any((root / "dictionary").glob("*.json"))
        or any((root / "pdfs").glob("*_variables.json"))
    )


def resolve_snapshot_name(name: str | None) -> str:
    """Compatibility shim: the only active snapshot name is the study name."""

    _ = name
    return str(config.STUDY_NAME)


def create_snapshot(name: str | None = None, *, overwrite: bool = False) -> Path:
    """Copy the live trio bundle into ``data/snapshots/{STUDY}/``."""

    _ = name
    trio = _trio_root()
    if not trio.exists() or not trio.is_dir():
        raise SnapshotError(f"Trio bundle missing at {trio}; cannot save snapshot.")

    target = _snapshot_root()
    if target.exists():
        if not overwrite:
            raise SnapshotError(f"Snapshot already exists: {target}")
        _safe_rmtree(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(trio, target, symlinks=False)
    _harden_tree_modes(target)
    return target


def restore_snapshot(name: str | None = None) -> Path:
    """Overwrite the live trio bundle with the reviewed snapshot baseline."""

    _ = name
    source = _snapshot_root()
    if not snapshot_exists():
        raise SnapshotError(f"Reviewed snapshot not found or empty: {source}")

    trio = _trio_root()
    trio.parent.mkdir(parents=True, exist_ok=True)

    staging = trio.with_name(trio.name + ".replacing")
    backup = trio.with_name(trio.name + ".previous")
    for path in (staging, backup):
        if path.exists():
            _safe_rmtree(path)

    shutil.copytree(source, staging, symlinks=False)
    _harden_tree_modes(staging)

    if trio.exists():
        trio.rename(backup)
    try:
        staging.rename(trio)
    except Exception:
        if backup.exists() and not trio.exists():
            backup.rename(trio)
        if staging.exists():
            with contextlib.suppress(Exception):
                _safe_rmtree(staging)
        raise

    if backup.exists():
        with contextlib.suppress(Exception):
            _safe_rmtree(backup)
    return trio


def list_snapshots() -> list[str]:
    """Return the single reviewed snapshot name when it exists."""

    return [str(config.STUDY_NAME)] if snapshot_exists() else []


def latest_snapshot_name() -> str | None:
    """Return the study snapshot name, or None if no baseline exists."""

    return str(config.STUDY_NAME) if snapshot_exists() else None


def _cmd_create(args: argparse.Namespace) -> int:
    try:
        path = create_snapshot(overwrite=args.force)
    except SnapshotError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ Reviewed snapshot saved to {path}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    if not snapshot_exists():
        print("No reviewed snapshot baseline available.")
        return 0
    print(f"Reviewed snapshot baseline: {_snapshot_root()}")
    return 0


def _cmd_restore(_args: argparse.Namespace) -> int:
    try:
        path = restore_snapshot()
    except SnapshotError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ Restored reviewed snapshot into {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.utils.snapshots",
        description="Save or restore the reviewed data/snapshots/{STUDY} baseline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser(
        "create",
        help="Copy output/{STUDY}/trio_bundle/ → data/snapshots/{STUDY}/",
    )
    p_create.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the existing reviewed snapshot baseline.",
    )
    p_create.add_argument(
        "--name",
        default=None,
        help="Ignored compatibility option; snapshots are single-baseline per study.",
    )
    p_create.set_defaults(func=_cmd_create)

    p_list = sub.add_parser("list", help="Show whether the reviewed snapshot exists")
    p_list.set_defaults(func=_cmd_list)

    p_restore = sub.add_parser(
        "restore",
        help="Overwrite the live trio bundle with data/snapshots/{STUDY}/",
    )
    p_restore.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Ignored compatibility option; snapshots are single-baseline per study.",
    )
    p_restore.set_defaults(func=_cmd_restore)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
