"""Pipeline-lock env-var handoff between the skill wrapper and main.py.

The skill wrapper (``scripts/skills/extract_to_llm_source.py``) acquires the
study lockfile via fcntl flock, then spawns ``main.py --pipeline`` as a
subprocess.  Without coordination, that subprocess would attempt to flock the
same file held by its parent and fail (POSIX behaviour — fcntl flocks are
inherited but a fresh ``open + flock`` from the child blocks/raises).

The handoff: the wrapper sets ``REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT=1`` in
the subprocess env, and main.py's lock helpers honour it by no-op-ing.
Direct ``python main.py --pipeline`` invocations leave the env unset and go
through the normal acquire path.
"""

from __future__ import annotations

import importlib

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
        monkeypatch.setenv("REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT", "1")
        monkeypatch.setattr(fresh_main.config, "TMP_DIR", str(tmp_path), raising=False)

        fresh_main._acquire_pipeline_lock(study="Indo-VAP")

        assert not (tmp_path / ".Indo-VAP.pipeline.lock").exists(), (
            "Acquire must skip file creation entirely when env signals parent holds the lock"
        )
        assert fresh_main._PIPELINE_LOCK_FILE is None, (
            "Acquire must leave the module-level handle untouched"
        )

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
