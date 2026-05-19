"""Tests for scripts/utils/run_context.py — scan_for_in_progress_scrubs and related."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.utils.run_context import (
    SCRUB_RECOVERY_MESSAGE,
    scan_for_in_progress_scrubs,
)


class TestScanForInProgressScrubs:
    def test_empty_runs_dir_returns_empty_list(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        result = scan_for_in_progress_scrubs(runs_dir)
        assert result == []

    def test_nonexistent_runs_dir_returns_empty_list(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs_does_not_exist"
        result = scan_for_in_progress_scrubs(runs_dir)
        assert result == []

    def test_finds_single_in_progress_token(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_abc123"
        run_dir.mkdir(parents=True)
        token = run_dir / "scrub.in_progress"
        token.write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_scrubs(runs_dir)
        assert result == [token]

    def test_no_token_in_run_dir_returns_empty(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_abc123"
        run_dir.mkdir(parents=True)
        # directory exists but no scrub.in_progress file

        result = scan_for_in_progress_scrubs(runs_dir)
        assert result == []

    def test_finds_two_tokens_from_different_run_ids(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        for run_id in ("run_aaa", "run_bbb"):
            run_dir = runs_dir / run_id
            run_dir.mkdir(parents=True)
            (run_dir / "scrub.in_progress").write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_scrubs(runs_dir)
        assert len(result) == 2
        names = {p.parent.name for p in result}
        assert names == {"run_aaa", "run_bbb"}

    def test_returns_path_objects(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_xyz"
        run_dir.mkdir(parents=True)
        (run_dir / "scrub.in_progress").write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_scrubs(runs_dir)
        assert all(isinstance(p, Path) for p in result)

    def test_only_matches_exact_filename(self, tmp_path: Path) -> None:
        """Files named differently should not be returned."""
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_abc"
        run_dir.mkdir(parents=True)
        (run_dir / "scrub.in_progress.bak").write_text("{}", encoding="utf-8")
        (run_dir / "other.json").write_text("{}", encoding="utf-8")

        result = scan_for_in_progress_scrubs(runs_dir)
        assert result == []


class TestScrubRecoveryMessage:
    def test_constant_is_exported(self) -> None:
        assert SCRUB_RECOVERY_MESSAGE is not None

    def test_constant_is_string(self) -> None:
        assert isinstance(SCRUB_RECOVERY_MESSAGE, str)

    def test_constant_contains_path_placeholder(self) -> None:
        assert "{path}" in SCRUB_RECOVERY_MESSAGE

    def test_constant_is_formattable(self) -> None:
        """The {path} placeholder must be usable with .format()."""
        formatted = SCRUB_RECOVERY_MESSAGE.format(path="/some/path/scrub.in_progress")
        assert "/some/path/scrub.in_progress" in formatted
