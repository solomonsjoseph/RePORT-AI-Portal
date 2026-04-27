"""Tests for scripts/security/secure_env.py — zone guard enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.security.secure_env import (
    ZoneViolationError,
    assert_clean_zone,
    assert_not_raw,
    assert_output_not_in_data,
    assert_output_zone,
    assert_write_zone,
    validate_paths,
)

# ── assert_not_raw ──────────────────────────────────────────────────────────


class TestAssertNotRaw:
    def test_raw_data_dir_raises(self) -> None:
        raw = config.RAW_DATA_DIR / "some_file.csv"
        with pytest.raises(ZoneViolationError):
            assert_not_raw(raw)

    def test_output_dir_passes(self) -> None:
        assert_not_raw(config.OUTPUT_DIR / "safe.jsonl")

    def test_relative_path_outside_raw_passes(self, tmp_path: Path) -> None:
        assert_not_raw(tmp_path / "file.txt")

    def test_string_path_accepted(self) -> None:
        assert_not_raw(str(config.OUTPUT_DIR / "ok.jsonl"))


# ── assert_output_zone ─────────────────────────────────────────────────────


class TestAssertOutputZone:
    def test_output_dir_passes(self) -> None:
        assert_output_zone(config.OUTPUT_DIR / "study" / "file.jsonl")

    def test_raw_dir_raises(self) -> None:
        with pytest.raises(ZoneViolationError):
            assert_output_zone(config.RAW_DATA_DIR / "file.csv")

    def test_tmp_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ZoneViolationError):
            assert_output_zone(tmp_path / "file.txt")

    def test_string_path_accepted(self) -> None:
        assert_output_zone(str(config.OUTPUT_DIR / "file.jsonl"))


# ── assert_clean_zone ──────────────────────────────────────────────────────


class TestAssertCleanZone:
    def test_trio_bundle_passes(self) -> None:
        assert_clean_zone(config.TRIO_BUNDLE_DIR / "datasets" / "file.jsonl")

    def test_raw_dir_raises(self) -> None:
        with pytest.raises(ZoneViolationError):
            assert_clean_zone(config.RAW_DATA_DIR / "file.csv")


# ── assert_output_not_in_data ──────────────────────────────────────────────


class TestAssertOutputNotInData:
    def test_data_dir_raises(self) -> None:
        with pytest.raises(ZoneViolationError):
            assert_output_not_in_data(config.DATA_DIR / "output_here.jsonl")

    def test_output_dir_passes(self) -> None:
        assert_output_not_in_data(config.OUTPUT_DIR / "file.jsonl")


# ── validate_paths ─────────────────────────────────────────────────────────


class TestValidatePaths:
    def test_deny_raw_blocks_raw_path(self) -> None:
        with pytest.raises(ZoneViolationError):
            validate_paths([config.RAW_DATA_DIR / "x.csv"], deny_raw=True)

    def test_deny_raw_false_still_enforces_output_zone(self) -> None:
        # validate_paths always calls assert_output_zone, so raw/ paths fail
        # regardless of deny_raw setting
        with pytest.raises(ZoneViolationError):
            validate_paths([config.RAW_DATA_DIR / "x.csv"], deny_raw=False)

    def test_multiple_paths_checked(self) -> None:
        with pytest.raises(ZoneViolationError):
            validate_paths(
                [config.OUTPUT_DIR / "ok.jsonl", config.RAW_DATA_DIR / "bad.csv"],
                deny_raw=True,
            )


# ── assert_write_zone ──────────────────────────────────────────────────────


class TestAssertWriteZone:
    """assert_write_zone accepts OUTPUT_DIR + TMP_DIR, rejects everything else."""

    def test_output_dir_passes(self) -> None:
        assert_write_zone(config.OUTPUT_DIR / "study" / "file.jsonl")

    def test_tmp_dir_passes(self) -> None:
        assert_write_zone(config.TMP_DIR / "Indo-VAP" / "datasets" / "file.jsonl")

    def test_outside_both_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Path outside both OUTPUT_DIR and TMP_DIR raises ZoneViolationError.

        Uses disjoint roots to mirror production isolation: the markers are
        completely separate, so a path not under either must fail.
        """
        import scripts.security.secure_env as _se

        out_root = tmp_path / "out"
        stg_root = tmp_path / "stg"
        out_root.mkdir()
        stg_root.mkdir()
        monkeypatch.setattr(_se, "_OUTPUT_MARKER", str(out_root.resolve()))
        monkeypatch.setattr(_se, "_TMP_MARKER", str(stg_root.resolve()))

        with pytest.raises(ZoneViolationError):
            assert_write_zone(tmp_path / "elsewhere" / "file.txt")

    def test_output_subdir_passes_with_disjoint_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.security.secure_env as _se

        out_root = tmp_path / "out"
        stg_root = tmp_path / "stg"
        out_root.mkdir()
        stg_root.mkdir()
        monkeypatch.setattr(_se, "_OUTPUT_MARKER", str(out_root.resolve()))
        monkeypatch.setattr(_se, "_TMP_MARKER", str(stg_root.resolve()))

        assert_write_zone(out_root / "study" / "trio_bundle" / "file.jsonl")

    def test_tmp_subdir_passes_with_disjoint_markers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.security.secure_env as _se

        out_root = tmp_path / "out"
        stg_root = tmp_path / "stg"
        out_root.mkdir()
        stg_root.mkdir()
        monkeypatch.setattr(_se, "_OUTPUT_MARKER", str(out_root.resolve()))
        monkeypatch.setattr(_se, "_TMP_MARKER", str(stg_root.resolve()))

        assert_write_zone(stg_root / "Indo-VAP" / "datasets" / "file.jsonl")

    def test_assert_output_zone_still_rejects_tmp_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """assert_output_zone must NOT accept TMP_DIR paths — audits stay strict."""
        import scripts.security.secure_env as _se

        out_root = tmp_path / "out"
        stg_root = tmp_path / "stg"
        out_root.mkdir()
        stg_root.mkdir()
        monkeypatch.setattr(_se, "_OUTPUT_MARKER", str(out_root.resolve()))
        monkeypatch.setattr(_se, "_TMP_MARKER", str(stg_root.resolve()))

        with pytest.raises(ZoneViolationError):
            assert_output_zone(stg_root / "Indo-VAP" / "datasets" / "file.jsonl")
