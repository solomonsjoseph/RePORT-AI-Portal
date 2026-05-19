"""Tests for scripts.skills.extract_to_llm_source.destroy_staging_and_attest.

Coverage:
    A. Happy path — staging dir is removed; attestation JSON is complete.
    B. Failure path — if secure_remove_tree is a no-op, DestructionIncompleteError raised.
    C. Symlinks/empty subdirs — smoke test that calling through to secure_remove_tree works.
    E. Run-id overwrite — calling twice with same run_id succeeds cleanly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.security import secure_env
from scripts.skills.extract_to_llm_source import (
    DestructionIncompleteError,
    destroy_staging_and_attest,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _patch_write_zone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Allow secure_remove_tree to operate on tmp_path by patching zone markers."""
    monkeypatch.setattr(secure_env, "_TMP_MARKER", os.path.realpath(str(tmp_path)))
    monkeypatch.setattr(secure_env, "_OUTPUT_MARKER", os.path.realpath(str(tmp_path)))


def _make_staging(staging_dir: Path) -> list[Path]:
    """Seed staging_dir with a few files and return their paths."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    files = [
        staging_dir / "file_a.jsonl",
        staging_dir / "subdir" / "file_b.json",
        staging_dir / "subdir" / "deep" / "file_c.bin",
    ]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"phi-data-" + f.name.encode())
    return files


# ---------------------------------------------------------------------------
# A. Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_staging_dir_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        _make_staging(staging_dir)

        destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-001",
            staging_dir=staging_dir,
            output_dir=output_dir,
        )

        assert not staging_dir.exists(), "staging_dir should be gone after attestation"

    def test_attestation_file_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        _make_staging(staging_dir)

        attest_path = destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-002",
            staging_dir=staging_dir,
            output_dir=output_dir,
        )

        assert attest_path.exists(), "attestation file must exist"
        assert attest_path == output_dir / "runs" / "run-002" / "destruction_attestation.json"

    def test_attestation_json_has_all_required_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        _make_staging(staging_dir)

        attest_path = destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-003",
            staging_dir=staging_dir,
            output_dir=output_dir,
        )

        data = json.loads(attest_path.read_text(encoding="utf-8"))

        # All 9 required fields
        required_fields = {
            "run_id",
            "study",
            "started_utc",
            "completed_utc",
            "staging_path",
            "removed_paths",
            "files_destroyed",
            "cryptographic_erasure",
            "apfs_cow_disclaimer",
        }
        assert required_fields == set(data.keys()), f"Field mismatch: {set(data.keys())}"

        # Type checks
        assert data["run_id"] == "run-003"
        assert data["study"] == "Indo-VAP"
        assert isinstance(data["started_utc"], str)
        assert isinstance(data["completed_utc"], str)
        assert isinstance(data["staging_path"], str)
        assert isinstance(data["removed_paths"], list)
        assert isinstance(data["files_destroyed"], int)
        assert data["files_destroyed"] == 3, "seeded 3 files"
        assert data["cryptographic_erasure"] is False
        assert isinstance(data["apfs_cow_disclaimer"], str)

    def test_removed_paths_are_relative_strings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        _make_staging(staging_dir)

        attest_path = destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-004",
            staging_dir=staging_dir,
            output_dir=output_dir,
        )

        data = json.loads(attest_path.read_text(encoding="utf-8"))
        for rp in data["removed_paths"]:
            # Must be relative (no leading slash)
            assert not rp.startswith("/"), f"path should be relative: {rp}"

    def test_returns_path_object(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        _make_staging(staging_dir)

        result = destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-005",
            staging_dir=staging_dir,
            output_dir=output_dir,
        )

        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# B. Failure path — staging still exists after secure_remove_tree
# ---------------------------------------------------------------------------


class TestFailurePath:
    def test_raises_destruction_incomplete_error_when_staging_not_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        _make_staging(staging_dir)

        # Mock secure_remove_tree to be a no-op
        with patch(
            "scripts.skills.extract_to_llm_source.secure_remove_tree",
            return_value=None,
        ):
            with pytest.raises(DestructionIncompleteError):
                destroy_staging_and_attest(
                    study="Indo-VAP",
                    run_id="run-fail",
                    staging_dir=staging_dir,
                    output_dir=output_dir,
                )

    def test_no_attestation_written_on_incomplete_destruction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        _make_staging(staging_dir)

        attest_path = output_dir / "runs" / "run-fail2" / "destruction_attestation.json"

        with patch(
            "scripts.skills.extract_to_llm_source.secure_remove_tree",
            return_value=None,
        ):
            with pytest.raises(DestructionIncompleteError):
                destroy_staging_and_attest(
                    study="Indo-VAP",
                    run_id="run-fail2",
                    staging_dir=staging_dir,
                    output_dir=output_dir,
                )

        # Attestation must NOT exist — incomplete destruction
        assert not attest_path.exists()


# ---------------------------------------------------------------------------
# C. Symlinks and empty subdirs
# ---------------------------------------------------------------------------


class TestSymlinksAndEmptyDirs:
    def test_staging_with_empty_subdirs_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        staging_dir.mkdir(parents=True)
        (staging_dir / "empty_subdir").mkdir()
        (staging_dir / "only_file.txt").write_bytes(b"content")

        attest_path = destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-empty",
            staging_dir=staging_dir,
            output_dir=output_dir,
        )

        assert not staging_dir.exists()
        data = json.loads(attest_path.read_text(encoding="utf-8"))
        assert data["files_destroyed"] == 1

    def test_staging_with_symlink_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        output_dir = tmp_path / "output" / "Indo-VAP"
        staging_dir.mkdir(parents=True)
        real_file = staging_dir / "real.txt"
        real_file.write_bytes(b"data")
        link = staging_dir / "link.txt"
        link.symlink_to(real_file)

        # After secure_remove_tree the dir should not exist
        destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-symlink",
            staging_dir=staging_dir,
            output_dir=output_dir,
        )

        assert not staging_dir.exists()


# ---------------------------------------------------------------------------
# E. Run-id sub-directory overwrite
# ---------------------------------------------------------------------------


class TestRunIdOverwrite:
    def test_rerun_same_run_id_overwrites_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_write_zone(monkeypatch, tmp_path)
        output_dir = tmp_path / "output" / "Indo-VAP"

        # First call
        staging_dir_1 = tmp_path / "tmp" / "Indo-VAP"
        _make_staging(staging_dir_1)
        attest_path_1 = destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-same",
            staging_dir=staging_dir_1,
            output_dir=output_dir,
        )
        data_1 = json.loads(attest_path_1.read_text(encoding="utf-8"))
        completed_1 = data_1["completed_utc"]

        # Second call — rebuild staging, same run_id
        staging_dir_2 = tmp_path / "tmp" / "Indo-VAP"
        _make_staging(staging_dir_2)
        attest_path_2 = destroy_staging_and_attest(
            study="Indo-VAP",
            run_id="run-same",
            staging_dir=staging_dir_2,
            output_dir=output_dir,
        )

        assert attest_path_1 == attest_path_2, "same run_id should resolve to same path"
        data_2 = json.loads(attest_path_2.read_text(encoding="utf-8"))
        # The file was overwritten with new timestamps
        assert isinstance(data_2["completed_utc"], str)
        # The overwrite did not raise — that's the main assertion


# ---------------------------------------------------------------------------
# F. PHI guard (I-5) — reject removed_paths with subject-ID-like segments
# ---------------------------------------------------------------------------


class TestPhiGuard:
    def test_destroy_staging_and_attest_raises_if_path_segment_matches_subject_id_pattern(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """I-5: Guard must reject removed_paths that contain subject-ID-like segments.

        Uses realistic subject-ID patterns from SUBJECT_ID_PATTERNS:
        - SUBJ-\d+ (e.g., SUBJ-1234)
        - SC\d{4,} (e.g., SC0001)
        - FID\d* (e.g., FID1)
        """
        _patch_write_zone(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / "Indo-VAP"
        staging_dir.mkdir(parents=True, exist_ok=True)
        output_dir = tmp_path / "output" / "Indo-VAP"

        # Plant a file whose name matches SUBJECT_ID_PATTERNS.
        # Using "SUBJ-1234.jsonl" which matches \bSUBJ[-_]?\d+\b
        phi_file = staging_dir / "SUBJ-1234.jsonl"
        phi_file.write_text("data")

        # Should raise ValueError mentioning SUBJECT_ID_PATTERNS
        with pytest.raises(ValueError, match="SUBJECT_ID_PATTERNS"):
            destroy_staging_and_attest(
                study="Indo-VAP",
                run_id="run-phi-guard",
                staging_dir=staging_dir,
                output_dir=output_dir,
            )
