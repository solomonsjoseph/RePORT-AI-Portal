"""Tests for the cleanup.in_progress token mechanism in scripts/utils/run_context.py.

Mirrors the scrub.in_progress tests in tests/utils/test_run_context.py — the
cleanup token is the dataset-cleanup-leg analogue (Note 13 Gap 7).
"""

from __future__ import annotations

from pathlib import Path

from scripts.utils.run_context import (
    CLEANUP_RECOVERY_MESSAGE,
    delete_cleanup_token,
    scan_for_in_progress_cleanups,
    write_cleanup_token,
)


class TestScanForInProgressCleanups:
    def test_empty_runs_dir_returns_empty_list(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        result = scan_for_in_progress_cleanups(runs_dir)
        assert result == []

    def test_nonexistent_runs_dir_returns_empty_list(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs_does_not_exist"
        result = scan_for_in_progress_cleanups(runs_dir)
        assert result == []

    def test_finds_single_in_progress_token(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_abc123"
        run_dir.mkdir(parents=True)
        token = run_dir / "cleanup.in_progress"
        token.write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_cleanups(runs_dir)
        assert result == [token]

    def test_no_token_in_run_dir_returns_empty(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_abc123"
        run_dir.mkdir(parents=True)
        # directory exists but no cleanup.in_progress file

        result = scan_for_in_progress_cleanups(runs_dir)
        assert result == []

    def test_finds_two_tokens_from_different_run_ids(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        for run_id in ("run_aaa", "run_bbb"):
            run_dir = runs_dir / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "cleanup.in_progress").write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_cleanups(runs_dir)
        assert len(result) == 2
        names = {p.parent.name for p in result}
        assert names == {"run_aaa", "run_bbb"}

    def test_returns_path_objects(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_xyz"
        run_dir.mkdir(parents=True)
        (run_dir / "cleanup.in_progress").write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_cleanups(runs_dir)
        assert all(isinstance(p, Path) for p in result)

    def test_only_matches_exact_filename(self, tmp_path: Path) -> None:
        """Files named differently should not be returned."""
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_abc"
        run_dir.mkdir(parents=True)
        (run_dir / "cleanup.in_progress.bak").write_text("{}", encoding="utf-8")
        (run_dir / "other.json").write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_cleanups(runs_dir)
        assert result == []

    def test_does_not_match_scrub_token(self, tmp_path: Path) -> None:
        """A scrub.in_progress token must not be reported as a cleanup token."""
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_abc"
        run_dir.mkdir(parents=True)
        (run_dir / "scrub.in_progress").write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_cleanups(runs_dir)
        assert result == []


class TestWriteCleanupToken:
    def test_returns_token_path(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "run_abc"
        run_dir.mkdir(parents=True)
        token = write_cleanup_token(run_dir)
        assert token == run_dir / "cleanup.in_progress"

    def test_creates_token_file(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "run_abc"
        run_dir.mkdir(parents=True)
        token = write_cleanup_token(run_dir)
        assert token.is_file()

    def test_creates_missing_run_dir(self, tmp_path: Path) -> None:
        """write_cleanup_token creates the run dir if it does not yet exist."""
        run_dir = tmp_path / "runs" / "run_not_yet_created"
        assert not run_dir.exists()
        token = write_cleanup_token(run_dir)
        assert token.is_file()
        assert run_dir.is_dir()


class TestDeleteCleanupToken:
    def test_deletes_existing_token(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "run_abc"
        run_dir.mkdir(parents=True)
        token = write_cleanup_token(run_dir)
        assert token.is_file()

        delete_cleanup_token(run_dir)
        assert not token.exists()

    def test_missing_token_is_idempotent(self, tmp_path: Path) -> None:
        """Deleting an absent token is a no-op, not an error."""
        run_dir = tmp_path / "runs" / "run_abc"
        run_dir.mkdir(parents=True)
        # no token written
        delete_cleanup_token(run_dir)  # must not raise
        delete_cleanup_token(run_dir)  # twice — still idempotent


class TestRoundTrip:
    def test_write_scan_delete_scan(self, tmp_path: Path) -> None:
        """Full life-cycle: write -> scan finds it -> delete -> scan empty."""
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_roundtrip"
        run_dir.mkdir(parents=True)

        token = write_cleanup_token(run_dir)
        assert scan_for_in_progress_cleanups(runs_dir) == [token]

        delete_cleanup_token(run_dir)
        assert scan_for_in_progress_cleanups(runs_dir) == []


class TestMultiRunRestartDetection:
    def test_completed_run_not_flagged_aborted_run_is(self, tmp_path: Path) -> None:
        """Simulate a restart: a cleanly-completed run leaves no token, an
        aborted run leaves a surviving token that the scan detects."""
        runs_dir = tmp_path / "runs"

        # Run A completed cleanly: token written then deleted.
        run_a = runs_dir / "run_a"
        run_a.mkdir(parents=True)
        write_cleanup_token(run_a)
        delete_cleanup_token(run_a)

        # Run B aborted mid-cleanup: token survives.
        run_b = runs_dir / "run_b"
        run_b.mkdir(parents=True)
        write_cleanup_token(run_b)

        # On restart, only the aborted run is detected.
        survivors = scan_for_in_progress_cleanups(runs_dir)
        assert len(survivors) == 1
        assert survivors[0].parent.name == "run_b"

    def test_multiple_aborted_runs_all_detected(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        for run_id in ("run_x", "run_y", "run_z"):
            run_dir = runs_dir / run_id
            run_dir.mkdir(parents=True)
            write_cleanup_token(run_dir)

        survivors = scan_for_in_progress_cleanups(runs_dir)
        assert {p.parent.name for p in survivors} == {"run_x", "run_y", "run_z"}


class TestCleanupRecoveryMessage:
    def test_constant_is_exported(self) -> None:
        assert CLEANUP_RECOVERY_MESSAGE is not None

    def test_constant_is_string(self) -> None:
        assert isinstance(CLEANUP_RECOVERY_MESSAGE, str)

    def test_constant_contains_path_placeholder(self) -> None:
        assert "{path}" in CLEANUP_RECOVERY_MESSAGE

    def test_constant_is_formattable(self) -> None:
        """The {path} placeholder must be usable with .format()."""
        formatted = CLEANUP_RECOVERY_MESSAGE.format(path="/some/path/cleanup.in_progress")
        assert "/some/path/cleanup.in_progress" in formatted
