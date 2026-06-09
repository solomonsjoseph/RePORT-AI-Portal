"""Pipeline-lock env-var handoff between the skill wrapper and main.py.

The skill wrapper (``scripts/skills/extract_to_llm_source.py``) acquires the
study lockfile via fcntl flock, then spawns ``main.py --pipeline`` as a
subprocess.  Without coordination, that subprocess would attempt to flock the
same file held by its parent and fail (POSIX behaviour — fcntl flocks are
inherited but a fresh ``open + flock`` from the child blocks/raises).

The handoff: the wrapper sets ``REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT=1`` in
the subprocess env AND ``REPORTAL_PIPELINE_LOCK_PARENT_PID`` to its own PID.
main.py's lock helper honours the skip ONLY when both conditions hold:
  1. REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT == "1"
  2. REPORTAL_PIPELINE_LOCK_PARENT_PID names a live process equal to os.getppid()

If PARENT_PID is absent, stale, or mismatched, the skip is NOT applied and
acquisition proceeds normally (GAP-3 hardening).

Direct ``python main.py --pipeline`` invocations leave the env unset and go
through the normal acquire path.
"""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def fresh_main(monkeypatch: pytest.MonkeyPatch):
    """Import main.py with module-state reset so each test starts clean."""
    import main as _main

    importlib.reload(_main)
    monkeypatch.setattr(_main, "_PIPELINE_LOCK_FILE", None, raising=False)
    return _main


class TestLockSkipOnParentHeld:
    def test_acquire_returns_without_opening_file_when_env_set(
        self, fresh_main, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """GAP-3: skip path requires HELD_BY_PARENT=1 AND a valid PARENT_PID that
        matches os.getppid() and is alive. Use os.getpid() as the claimed PID
        (this process is alive) and monkeypatch os.getppid to return os.getpid()
        so the equality check passes."""
        my_pid = os.getpid()
        monkeypatch.setenv("REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT", "1")
        monkeypatch.setenv("REPORTAL_PIPELINE_LOCK_PARENT_PID", str(my_pid))
        # Make getppid() return our own PID so claimed_pid == os.getppid() is True.
        monkeypatch.setattr(fresh_main.os, "getppid", lambda: my_pid)
        monkeypatch.setattr(fresh_main.config, "TMP_DIR", str(tmp_path), raising=False)

        fresh_main._acquire_pipeline_lock(study="Indo-VAP")

        assert not (tmp_path / ".Indo-VAP.pipeline.lock").exists(), (
            "Acquire must skip file creation entirely when env signals parent holds the lock"
        )
        assert fresh_main._PIPELINE_LOCK_FILE is None, (
            "Acquire must leave the module-level handle untouched"
        )

    def test_acquire_falls_through_when_parent_pid_absent(
        self, fresh_main, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """GAP-3 hardening: HELD_BY_PARENT=1 but PARENT_PID absent → PID validation
        fails → real acquisition happens (lock file IS created).
        """
        monkeypatch.setenv("REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT", "1")
        monkeypatch.delenv("REPORTAL_PIPELINE_LOCK_PARENT_PID", raising=False)
        monkeypatch.setattr(fresh_main.config, "TMP_DIR", str(tmp_path), raising=False)

        fresh_main._acquire_pipeline_lock(study="Indo-VAP")
        try:
            assert (tmp_path / ".Indo-VAP.pipeline.lock").exists(), (
                "Acquire must create the lockfile when PARENT_PID is absent "
                "(baton cannot be validated, fall through to real acquisition)"
            )
            assert fresh_main._PIPELINE_LOCK_FILE is not None, (
                "Acquire must populate the module-level handle"
            )
        finally:
            fresh_main._release_pipeline_lock(study="Indo-VAP")

    def test_release_is_noop_when_env_set(
        self, fresh_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT", "1")

        fresh_main._release_pipeline_lock(study="Indo-VAP")  # must not raise

        assert fresh_main._PIPELINE_LOCK_FILE is None


class TestLockAcquiredNormallyWithoutEnv:
    def test_acquire_creates_lockfile_when_env_unset(
        self, fresh_main, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.delenv("REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT", raising=False)
        monkeypatch.setattr(fresh_main.config, "TMP_DIR", str(tmp_path), raising=False)

        fresh_main._acquire_pipeline_lock(study="Indo-VAP")
        try:
            assert (tmp_path / ".Indo-VAP.pipeline.lock").exists(), (
                "Acquire must create the lockfile when env is not set"
            )
            assert fresh_main._PIPELINE_LOCK_FILE is not None, (
                "Acquire must populate the module-level handle"
            )
        finally:
            fresh_main._release_pipeline_lock(study="Indo-VAP")
