"""Operator restore-point helpers for the published trio_bundle.

A *restore point* is a full copy of ``output/{STUDY}/trio_bundle/`` saved
under ``output/{STUDY}/agent/restore_points/<name>/``. Restore points
intentionally contain **only** the LLM-readable trio bundle (datasets,
dictionary, pdfs, variables.json) — never audit logs, telemetry, or
conversations. This mirrors the read zone enforced by
:func:`scripts.security.assert_trio_bundle_zone`.

Restore points live under the agent-state tier (beside ``analysis/``,
``conversations/``, ``telemetry/``) because they are agent-owned
operational state: the operator's recovery target when a pipeline run
fails. Keeping them inside the fully-gitignored ``output/`` tree keeps
PHI-scrubbed cohort bytes out of git by default.

This is *distinct* from :data:`config.STUDY_SNAPSHOTS_DIR` (the
version-controlled baseline at ``snapshots/{STUDY}/`` at the repo root).
Restore points are gitignored, scratch, multi-named; snapshots are
tracked, manually-curated, single-baseline. The pipeline's PDF
orchestrator only reads the *snapshot* baseline; this CLI module is for
operator recovery only.

Public API
----------
- :func:`create_snapshot` — copy the current trio bundle into a named restore point.
- :func:`list_snapshots` — return available restore-point names, newest first.
- :func:`restore_snapshot` — overwrite the live trio bundle with a restore point.
- :func:`latest_snapshot_name` — convenience picker for auto-restore flows.

The ``snapshot`` terminology is preserved in function names for back-compat
with existing CLI invocations (``python -m scripts.utils.snapshots``); the
underlying storage path is :data:`config.STUDY_RESTORE_POINTS_DIR`.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import config


def _safe_rmtree(path: Path, *, ignore_errors: bool = False) -> None:
    """Delete *path*, refusing to follow symlinks at the root.

    ``shutil.rmtree`` is TOCTOU-vulnerable when the root path can be replaced
    with a symlink between an outer ``exists()`` check and the call. We
    refuse to operate on a symlink at all — caller must remove the link
    explicitly. This is the minimum protection appropriate for snapshots,
    which live under the agent-writable zone but are not assumed to be
    co-tenant-hostile.
    """
    if path.is_symlink():
        # Unlink the symlink itself; do NOT follow it into its target.
        path.unlink()
        return
    shutil.rmtree(path, ignore_errors=ignore_errors)

__all__ = [
    "SnapshotError",
    "create_snapshot",
    "latest_snapshot_name",
    "list_snapshots",
    "main",
    "resolve_snapshot_name",
    "restore_snapshot",
]


class SnapshotError(RuntimeError):
    """Raised when a snapshot operation cannot be completed."""


def _snapshots_root() -> Path:
    """Return the operator restore-point root, NOT the tracked baseline.

    Functions in this module create/list/restore *named runs*. Those land
    in ``output/{STUDY}/agent/restore_points/`` (gitignored) — never in
    ``snapshots/{STUDY}/`` (tracked baseline; the pipeline's PDF orchestrator
    fallback reads from there and a multi-run dump would corrupt it)."""
    return Path(config.STUDY_RESTORE_POINTS_DIR)


def _trio_root() -> Path:
    return Path(config.TRIO_BUNDLE_DIR)


def _make_timestamp_name() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")


def resolve_snapshot_name(name: str | None) -> str:
    """Return the effective snapshot name.

    Provides a preview-friendly default-fill for CLI callers that need
    to display the resolved name *before* calling :func:`create_snapshot`.
    Note: :func:`create_snapshot` independently applies the same default
    fill when called with ``None``; both paths call
    :func:`_make_timestamp_name` and produce consistent names within the
    same second.

    Passing ``None`` or the empty string yields a fresh UTC timestamp
    (``run-YYYYmmddTHHMMSSZ``); any other value is returned verbatim.
    Validation (no slashes, no leading dot) still happens inside
    :func:`create_snapshot`.
    """
    if name is None or not name.strip():
        return _make_timestamp_name()
    return name.strip()


def create_snapshot(name: str | None = None, *, overwrite: bool = False) -> Path:
    """Copy the live trio bundle into ``snapshots/<name>/``.

    Parameters
    ----------
    name:
        Snapshot name. Defaults to a UTC timestamp (``run-YYYYmmddTHHMMSSZ``).
    overwrite:
        When True, replace an existing snapshot with the same name.

    Raises
    ------
    SnapshotError:
        If the trio bundle is missing, the snapshot already exists (and
        ``overwrite`` is False), or the copy fails.
    """

    trio = _trio_root()
    if not trio.exists() or not trio.is_dir():
        raise SnapshotError(f"Trio bundle missing at {trio}; cannot snapshot.")

    snap_name = (name or _make_timestamp_name()).strip()
    if not snap_name or "/" in snap_name or snap_name.startswith("."):
        raise SnapshotError(f"Invalid snapshot name: {snap_name!r}")

    target = _snapshots_root() / snap_name
    if target.exists():
        if not overwrite:
            raise SnapshotError(f"Snapshot already exists: {target}")
        # Use _safe_rmtree (refuses to follow a symlink at the root) instead
        # of bare shutil.rmtree to close a TOCTOU vector where an attacker
        # swaps the snapshot directory with a symlink to /etc between the
        # exists() check and the rmtree call.
        _safe_rmtree(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    # copytree handles nested structure; trio bundle is self-contained.
    shutil.copytree(trio, target, symlinks=False)
    # Tighten permissions: snapshot may contain quasi-identifiers even when
    # the trio bundle is PHI-scrubbed. Walk the tree and set dirs to 0o700
    # and files to 0o600 so the snapshot isn't world-readable under the
    # default umask 0o022.
    _harden_tree_modes(target)
    return target


def _harden_tree_modes(root: Path) -> None:
    """Set every dir under *root* to mode 0o700 and every file to 0o600.

    Best-effort: per-entry ``chmod`` failures are logged at debug level but
    do not abort, because partial hardening still beats no hardening.
    """
    with contextlib.suppress(OSError):
        root.chmod(0o700)
    for current_root, dirs, files in os.walk(str(root)):
        for d in dirs:
            with contextlib.suppress(OSError):
                (Path(current_root) / d).chmod(0o700)
        for f in files:
            with contextlib.suppress(OSError):
                (Path(current_root) / f).chmod(0o600)


def list_snapshots() -> list[str]:
    """Return snapshot names sorted newest-first by mtime."""

    root = _snapshots_root()
    if not root.exists():
        return []
    entries = [p for p in root.iterdir() if p.is_dir()]
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in entries]


def latest_snapshot_name() -> str | None:
    """Return the most recent snapshot name, or ``None`` if none exist.

    Prefers a snapshot literally named ``initial`` when present, because that
    is the canonical restore point shipped with the repo; otherwise returns
    the newest timestamp-named snapshot.
    """

    names = list_snapshots()
    if "initial" in names:
        return "initial"
    return names[0] if names else None


def restore_snapshot(name: str) -> Path:
    """Overwrite the live trio bundle with ``snapshots/<name>/``.

    The replacement is done in-place: first the live bundle is moved aside
    to a ``.replacing`` sibling, then the snapshot is copied into place, and
    finally the aside copy is removed. If the copy fails, the original is
    restored.
    """

    if not name or "/" in name:
        raise SnapshotError(f"Invalid snapshot name: {name!r}")

    source = _snapshots_root() / name
    if not source.exists() or not source.is_dir():
        raise SnapshotError(f"Snapshot not found: {source}")

    trio = _trio_root()
    trio.parent.mkdir(parents=True, exist_ok=True)

    staging = trio.with_name(trio.name + ".replacing")
    if staging.exists():
        _safe_rmtree(staging)

    backup = trio.with_name(trio.name + ".previous")
    if backup.exists():
        _safe_rmtree(backup)

    # Copy snapshot into a staging directory first so a partial copy cannot
    # leave the live bundle broken.
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


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cmd_create(args: argparse.Namespace) -> int:
    snap_name = resolve_snapshot_name(args.name)
    target_dir = _snapshots_root() / snap_name
    print(
        f"Copying {_trio_root()} → {target_dir}"
        + (" (overwrite)" if args.force else "")
    )
    try:
        path = create_snapshot(snap_name, overwrite=args.force)
    except SnapshotError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ Snapshot saved to {path}")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    names = list_snapshots()
    if not names:
        print("No snapshots available.")
        return 0
    print("Snapshots (newest first):")
    for n in names:
        print(f"  - {n}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    try:
        path = restore_snapshot(args.name)
    except SnapshotError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(f"✓ Restored {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m scripts.utils.snapshots``.

    Subcommands mirror the library surface — ``create``, ``list``,
    ``restore`` — with boolean flags (``--force``) instead of
    string-truthy env var parsing so ``FORCE=0`` cannot accidentally
    enable overwrite.
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.utils.snapshots",
        description=(
            "Copy the published trio bundle into a named snapshot, "
            "list existing snapshots, or restore one."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser(
        "create",
        help="Copy output/{STUDY}/trio_bundle/ → output/{STUDY}/agent/restore_points/<name>/",
    )
    p_create.add_argument(
        "--name",
        default=None,
        help="Snapshot name (default: UTC timestamp run-YYYYmmddTHHMMSSZ)",
    )
    p_create.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing snapshot with the same name.",
    )
    p_create.set_defaults(func=_cmd_create)

    p_list = sub.add_parser("list", help="List snapshots, newest first")
    p_list.set_defaults(func=_cmd_list)

    p_restore = sub.add_parser(
        "restore",
        help="Overwrite the live trio bundle with the named snapshot",
    )
    p_restore.add_argument("name", help="Snapshot name to restore")
    p_restore.set_defaults(func=_cmd_restore)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
