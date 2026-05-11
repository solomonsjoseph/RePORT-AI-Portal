"""Non-destructive restore drill for the reviewed snapshot baseline."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import config
from scripts.utils import snapshots


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, symlinks=False)


def run_restore_drill() -> None:
    """Exercise snapshot restore against a temporary copy of production paths."""

    live_trio = Path(config.TRIO_BUNDLE_DIR)
    reviewed_snapshot = Path(config.STUDY_SNAPSHOTS_DIR)
    if not snapshots.snapshot_exists():
        raise snapshots.SnapshotError(f"Reviewed snapshot missing: {reviewed_snapshot}")
    if not Path(config.PHI_KEY_PATH).is_file():
        raise snapshots.SnapshotError(f"PHI key missing: {config.PHI_KEY_PATH}")

    with tempfile.TemporaryDirectory(prefix="report-ai-restore-drill-") as tmp:
        root = Path(tmp)
        # Mirror the live clean-zone dir name from config (avoids a hard-coded
        # legacy literal that the Phase 5b lint_legacy_dirs check forbids).
        drill_trio = root / Path(config.TRIO_BUNDLE_DIR).name
        drill_snapshot = root / "snapshot"
        _copy_if_exists(live_trio, drill_trio)
        shutil.copytree(reviewed_snapshot, drill_snapshot, symlinks=False)

        old_trio = config.TRIO_BUNDLE_DIR
        old_snapshot = config.STUDY_SNAPSHOTS_DIR
        try:
            config.TRIO_BUNDLE_DIR = drill_trio
            config.STUDY_SNAPSHOTS_DIR = drill_snapshot
            restored = snapshots.restore_snapshot()
        finally:
            config.TRIO_BUNDLE_DIR = old_trio
            config.STUDY_SNAPSHOTS_DIR = old_snapshot

        if not any((restored / "datasets").glob("*.jsonl")):
            raise snapshots.SnapshotError("Restore drill produced an empty trio bundle.")


def main() -> int:
    try:
        run_restore_drill()
    except snapshots.SnapshotError as exc:
        print(f"✗ Restore drill failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"✗ Restore drill crashed: {exc}", file=sys.stderr)
        return 1
    print("✓ Restore drill passed without modifying live data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
