"""Tests for scripts/utils/secure_staging.py.

Covers:
* prepare_staging — directory creation with mode 0700 + umask 0077
* prepare_staging — wipes prior residue via secure_remove_tree
* prepare_staging — rejects roots outside the write zone
* secure_remove_tree — overwrites file contents before unlinking
* secure_remove_tree — handles empty dirs + missing paths gracefully
* scoped_umask — restores previous umask on exit, even on exception
* resolve_staging_root — honors REPORTALIN_TMPFS_STAGING when /dev/shm exists
* resolve_staging_root — falls back when env flag absent or tmpfs unavailable
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from scripts.security.secure_env import ZoneViolationError
from scripts.utils import secure_staging

# ── scoped_umask ────────────────────────────────────────────────────────────


class TestScopedUmask:
    def test_applies_and_restores(self) -> None:
        original = os.umask(0o022)
        os.umask(original)  # reset to known baseline without side-effect
        try:
            with secure_staging.scoped_umask(0o077) as previous:
                assert previous == original
                # Inside the block, the effective umask is 0o077.
                current = os.umask(0o022)
                assert current == 0o077
                os.umask(0o077)
        finally:
            os.umask(original)
        # After exit, umask restored to original.
        after = os.umask(0o022)
        assert after == original

    def test_restores_on_exception(self) -> None:
        original = os.umask(0o022)
        os.umask(original)
        try:
            with pytest.raises(RuntimeError), secure_staging.scoped_umask(0o077):
                raise RuntimeError("boom")
            after = os.umask(0o022)
            assert after == original
        finally:
            os.umask(original)


# ── prepare_staging ─────────────────────────────────────────────────────────


class TestPrepareStaging:
    def test_creates_root_and_subdirs_with_mode_0700(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Monkeypatch zone guard so tmp_path is a valid write zone for this test.
        import config

        monkeypatch.setattr(config, "TMP_DIR", tmp_path)
        # secure_env caches _TMP_MARKER at import; override it directly.
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_TMP_MARKER", os.path.realpath(str(tmp_path)))

        root = tmp_path / "staging_study"
        subdirs = [root / "datasets", root / "dictionary", root / "pdfs"]
        secure_staging.prepare_staging(root, subdirs=subdirs)

        assert root.is_dir()
        for sub in subdirs:
            assert sub.is_dir()

        if sys.platform != "win32":
            # POSIX-only assertion: mode 0700 on root + every subdir.
            assert stat.S_IMODE(root.stat().st_mode) == 0o700
            for sub in subdirs:
                assert stat.S_IMODE(sub.stat().st_mode) == 0o700

    def test_wipes_residue_on_prepare(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config

        monkeypatch.setattr(config, "TMP_DIR", tmp_path)
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_TMP_MARKER", os.path.realpath(str(tmp_path)))

        root = tmp_path / "staging_study"
        # Seed residue — one file + one sub-sub-file.
        root.mkdir(parents=True)
        (root / "stale.jsonl").write_bytes(b"old content")
        (root / "subdir").mkdir()
        (root / "subdir" / "deep.jsonl").write_bytes(b"deeper")

        secure_staging.prepare_staging(root, subdirs=[root / "datasets"])

        # Residue gone.
        assert not (root / "stale.jsonl").exists()
        assert not (root / "subdir").exists()
        # New subdir present.
        assert (root / "datasets").is_dir()

    def test_rejects_path_outside_write_zone(self, tmp_path: Path) -> None:
        # A path that is NOT under output/ or tmp/ as resolved by secure_env.
        # (secure_env._TMP_MARKER points at the real tmp/ in the project
        # during normal test runs, not tmp_path — so tmp_path is invalid.)
        outsider = tmp_path / "should_not_be_here"
        with pytest.raises(ZoneViolationError):
            secure_staging.prepare_staging(outsider, subdirs=[])


# ── secure_remove_tree ──────────────────────────────────────────────────────


class TestSecureRemoveTree:
    def test_removes_all_files_and_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import config

        monkeypatch.setattr(config, "TMP_DIR", tmp_path)
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_TMP_MARKER", os.path.realpath(str(tmp_path)))

        root = tmp_path / "victim"
        root.mkdir()
        (root / "a.jsonl").write_bytes(b"alpha-secret")
        (root / "nested").mkdir()
        (root / "nested" / "b.jsonl").write_bytes(b"beta-secret")
        (root / "nested" / "deep").mkdir()
        (root / "nested" / "deep" / "c.jsonl").write_bytes(b"gamma-secret")

        secure_staging.secure_remove_tree(root)

        assert not root.exists()

    def test_missing_path_is_noop(self, tmp_path: Path) -> None:
        # Does not raise.
        secure_staging.secure_remove_tree(tmp_path / "never_existed")

    def test_overwrite_before_unlink(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Assert the overwrite helper runs via monkeypatched spy.

        We cannot observe the disk post-unlink, so instead we patch
        ``_overwrite_file`` to record which paths it was called on, and
        verify every regular file under root is overwritten before the
        tree teardown.
        """
        import config

        monkeypatch.setattr(config, "TMP_DIR", tmp_path)
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_TMP_MARKER", os.path.realpath(str(tmp_path)))

        root = tmp_path / "victim"
        root.mkdir()
        files = [root / "a.jsonl", root / "b.jsonl", root / "sub" / "c.jsonl"]
        (root / "sub").mkdir()
        for f in files:
            f.write_bytes(b"data")

        overwritten: list[Path] = []

        def fake_overwrite(path: Path) -> None:
            overwritten.append(path)

        monkeypatch.setattr(secure_staging, "_overwrite_file", fake_overwrite)
        secure_staging.secure_remove_tree(root)

        # Every regular file got an overwrite call.
        assert {p.name for p in overwritten} == {f.name for f in files}

    def test_rejects_path_outside_write_zone(self, tmp_path: Path) -> None:
        outsider = tmp_path / "not_in_zone"
        outsider.mkdir()
        (outsider / "file.txt").write_bytes(b"x")
        with pytest.raises(ZoneViolationError):
            secure_staging.secure_remove_tree(outsider)


# ── _overwrite_file internal ────────────────────────────────────────────────


class TestOverwriteFile:
    """Direct tests for the private _overwrite_file helper.

    These assertions do NOT violate the hard PHI rule — we write synthetic
    random bytes to a temp file, then confirm the overwrite is byte-
    different from our known pre-content. No real pipeline data is
    involved.
    """

    KNOWN_MARKER: ClassVar[bytes] = b"ORIGINAL" * 1024  # 8 KiB of known content

    def test_overwrites_regular_file(self, tmp_path: Path) -> None:
        target = tmp_path / "content.bin"
        target.write_bytes(self.KNOWN_MARKER)

        secure_staging._overwrite_file(target)

        # Overwrite keeps the same file size (same number of bytes written).
        after = target.read_bytes()
        assert len(after) == len(self.KNOWN_MARKER)
        assert after != self.KNOWN_MARKER  # must be different bytes

    def test_empty_file_passthrough(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.bin"
        target.write_bytes(b"")
        # Does not raise; file remains empty.
        secure_staging._overwrite_file(target)
        assert target.read_bytes() == b""

    def test_missing_file_passthrough(self, tmp_path: Path) -> None:
        # Does not raise.
        secure_staging._overwrite_file(tmp_path / "never_existed.bin")

    def test_symlink_skipped(self, tmp_path: Path) -> None:
        if sys.platform == "win32":
            pytest.skip("symlinks are permission-gated on Windows")
        source = tmp_path / "real.bin"
        source.write_bytes(b"real-content")
        link = tmp_path / "alias.bin"
        link.symlink_to(source)
        # Symlink skipped → source file content untouched.
        secure_staging._overwrite_file(link)
        assert source.read_bytes() == b"real-content"


# ── resolve_staging_root ────────────────────────────────────────────────────


class TestResolveStagingRoot:
    def test_default_when_env_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPORTALIN_TMPFS_STAGING", raising=False)
        default = tmp_path / "fallback"
        result = secure_staging.resolve_staging_root(default, study_name="TEST")
        assert result == default

    def test_default_when_env_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPORTALIN_TMPFS_STAGING", "0")
        default = tmp_path / "fallback"
        result = secure_staging.resolve_staging_root(default, study_name="TEST")
        assert result == default

    def test_tmpfs_when_available_and_env_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the tmpfs check to succeed against our tmp_path, then assert
        # the returned root is derived from that simulated mount.
        monkeypatch.setenv("REPORTALIN_TMPFS_STAGING", "1")
        simulated_tmpfs = tmp_path / "shm"
        simulated_tmpfs.mkdir()
        monkeypatch.setattr(secure_staging, "_TMPFS_ROOT", simulated_tmpfs)
        monkeypatch.setattr(secure_staging, "_tmpfs_is_available", lambda: True)
        default = tmp_path / "fallback"
        result = secure_staging.resolve_staging_root(default, study_name="MY_STUDY")
        assert result == simulated_tmpfs / "report_ai_portal" / "MY_STUDY"

    def test_fallback_when_env_true_but_tmpfs_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTALIN_TMPFS_STAGING", "1")
        monkeypatch.setattr(secure_staging, "_tmpfs_is_available", lambda: False)
        default = tmp_path / "fallback"
        result = secure_staging.resolve_staging_root(default, study_name="TEST")
        assert result == default


# ── End-to-end: prepare → write → remove lifecycle ──────────────────────────


class TestLifecycle:
    def test_full_round_trip_secure_delete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Prepare staging, write files, securely remove, verify gone."""
        import config

        monkeypatch.setattr(config, "TMP_DIR", tmp_path)
        from scripts.security import secure_env

        monkeypatch.setattr(secure_env, "_TMP_MARKER", os.path.realpath(str(tmp_path)))

        root = tmp_path / "lifecycle"
        secure_staging.prepare_staging(root, subdirs=[root / "datasets", root / "pdfs"])

        # Write some synthetic content.
        (root / "datasets" / "f1.jsonl").write_bytes(secrets.token_bytes(1024))
        (root / "pdfs" / "f2.json").write_bytes(secrets.token_bytes(512))

        # Teardown.
        secure_staging.secure_remove_tree(root)

        assert not root.exists()
