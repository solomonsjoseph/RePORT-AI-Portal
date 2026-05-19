"""Unit tests for the staging helpers added to main.py in Task 5.

Covers:

- _prepare_staging: purges residue + creates the three leg subdirs.
- _publish_leg: rename happy path, skip-when-empty, trio-exists overwrite,
  cross-filesystem fallback via a patched OSError, atomicity invariant.
- _publish_staging: invokes _publish_leg three times with the right pairs.
- _cleanup_staging: removes the staging tree when present + no-op otherwise.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

import main

# ── _prepare_staging ────────────────────────────────────────────────────────


class TestPrepareStaging:
    def test_creates_three_leg_dirs_on_fresh_run(self, monkeypatch_config: Path) -> None:
        import config

        # Pre-condition: staging may or may not exist; nothing in the three legs.
        assert not config.STAGING_DATASETS_DIR.exists() or not any(
            config.STAGING_DATASETS_DIR.iterdir()
        )

        main._prepare_staging()

        assert config.STUDY_STAGING_DIR.is_dir()
        assert config.STAGING_DATASETS_DIR.is_dir()
        assert config.STAGING_DICTIONARY_DIR.is_dir()

    def test_purges_existing_staging_residue(self, monkeypatch_config: Path) -> None:
        import config

        # Seed leftover files in staging (as a crashed prior run would)
        config.STAGING_DATASETS_DIR.mkdir(parents=True, exist_ok=True)
        (config.STAGING_DATASETS_DIR / "stale.jsonl").write_text('{"stale":1}\n', encoding="utf-8")
        stale_subdir = config.STUDY_STAGING_DIR / "orphan"
        stale_subdir.mkdir(parents=True, exist_ok=True)
        (stale_subdir / "leftover.txt").write_text("x", encoding="utf-8")

        main._prepare_staging()

        # Residue is gone; empty leg dirs exist.
        assert not (config.STAGING_DATASETS_DIR / "stale.jsonl").exists()
        assert not stale_subdir.exists()
        assert config.STAGING_DATASETS_DIR.is_dir()
        assert not any(config.STAGING_DATASETS_DIR.iterdir())
        assert config.STAGING_DICTIONARY_DIR.is_dir()

    def test_prepare_holds_process_lock(self, monkeypatch_config: Path) -> None:
        if os.name != "posix":
            pytest.skip("fcntl lock assertion is POSIX-only")
        fcntl = pytest.importorskip("fcntl")
        import config

        main._prepare_staging()
        lock_path = config.TMP_DIR / f".{config.STUDY_NAME}.pipeline.lock"
        assert lock_path.is_file()

        with lock_path.open("a+", encoding="utf-8") as fh, pytest.raises(BlockingIOError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        main._cleanup_staging()


# ── _publish_leg ────────────────────────────────────────────────────────────


class TestPublishLeg:
    def test_rename_publishes_content(self, monkeypatch_config: Path, tmp_path: Path) -> None:
        import config

        staging = config.STAGING_DATASETS_DIR
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "a.jsonl").write_text('{"k":1}\n', encoding="utf-8")

        trio = config.TRIO_DATASETS_DIR
        # Make sure trio has a prior file that must be replaced.
        trio.mkdir(parents=True, exist_ok=True)
        (trio / "old.jsonl").write_text("old", encoding="utf-8")

        published = main._publish_leg(staging, trio, "datasets")

        assert published is True
        assert not staging.exists(), "staging source should be renamed away"
        assert (trio / "a.jsonl").is_file()
        assert not (trio / "old.jsonl").exists(), "prior trio contents should be removed"

    def test_skip_when_staging_missing(self, monkeypatch_config: Path) -> None:
        import config

        staging = config.STAGING_DATASETS_DIR
        if staging.exists():
            # Remove any auto-created empty dir from the fixture.
            import shutil

            shutil.rmtree(staging)

        trio = config.TRIO_DATASETS_DIR
        trio.mkdir(parents=True, exist_ok=True)
        (trio / "existing.jsonl").write_text("keep", encoding="utf-8")

        published = main._publish_leg(staging, trio, "datasets")

        assert published is False
        assert (trio / "existing.jsonl").read_text(encoding="utf-8") == "keep"

    def test_skip_when_staging_empty(self, monkeypatch_config: Path) -> None:
        import config

        staging = config.STAGING_DATASETS_DIR
        staging.mkdir(parents=True, exist_ok=True)
        # Empty staging.

        trio = config.TRIO_DATASETS_DIR
        trio.mkdir(parents=True, exist_ok=True)
        (trio / "keep.jsonl").write_text("v", encoding="utf-8")

        published = main._publish_leg(staging, trio, "datasets")

        assert published is False
        assert (trio / "keep.jsonl").exists()

    def test_cross_filesystem_rename_falls_back_to_sibling_tmp_then_rename(
        self,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cross-filesystem path copies to a sibling tmp dir then renames into place.

        The first rename (staging → sibling tmp) raises EXDEV.  The fallback copies
        staging into ``trio_dir.parent / '.llm_source.publishing'`` and then
        renames that sibling into the final trio location — which is always on the
        same filesystem.
        """
        import config

        staging = config.STAGING_DATASETS_DIR
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "a.jsonl").write_text("x", encoding="utf-8")

        trio = config.TRIO_DATASETS_DIR
        trio.parent.mkdir(parents=True, exist_ok=True)

        # Allow the second rename (sibling_tmp → trio) to succeed; only the
        # first rename (staging → trio) raises EXDEV.
        _original_rename = Path.rename
        _first_call_done: list[bool] = [False]

        def _raise_first_rename(self: Path, target: Any) -> None:
            if not _first_call_done[0]:
                _first_call_done[0] = True
                raise OSError("cross-filesystem rename not supported [EXDEV]")
            return _original_rename(self, target)

        monkeypatch.setattr(Path, "rename", _raise_first_rename)

        published = main._publish_leg(staging, trio, "datasets")

        assert published is True
        assert (trio / "a.jsonl").read_text(encoding="utf-8") == "x"
        assert not staging.exists(), "staging source should be cleaned after copy"
        # The sibling tmp dir must not be left behind.
        sibling_tmp = trio.parent / ".llm_source.publishing"
        assert not sibling_tmp.exists(), "sibling tmp dir must be cleaned up on success"

    def test_mid_publish_crash_leaves_llm_source_absent_or_full(
        self,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Atomicity invariant: a crash partway through the cross-filesystem publish
        path must leave ``trio_dir`` either absent or fully populated — never
        half-populated.

        We force the first rename to EXDEV so the sibling-tmp fallback path is
        exercised, then inject a crash after ``shutil.copytree`` partially
        populates the sibling tmp dir but before the final rename promotes it
        to ``trio_dir``.

        With the old non-atomic copytree-to-final-location pattern the crash
        would leave a partial ``trio_dir``.  With the new sibling-tmp + rename
        pattern the crash leaves ``trio_dir`` absent (it was never renamed into
        place).
        """
        import config

        staging = config.STAGING_DATASETS_DIR
        staging.mkdir(parents=True, exist_ok=True)
        file_count = 5
        for i in range(file_count):
            (staging / f"file_{i}.jsonl").write_text(f'{{"i":{i}}}\n', encoding="utf-8")

        trio = config.TRIO_DATASETS_DIR
        trio.parent.mkdir(parents=True, exist_ok=True)
        # trio does not pre-exist — fresh publish scenario.

        # Force the first rename (staging → sibling_tmp) to EXDEV so the
        # cross-filesystem fallback path is exercised.
        _original_rename = Path.rename
        _first_call_done: list[bool] = [False]

        def _raise_first_rename(self: Path, target: Any) -> None:
            if not _first_call_done[0]:
                _first_call_done[0] = True
                raise OSError("cross-filesystem rename not supported [EXDEV]")
            return _original_rename(self, target)

        monkeypatch.setattr(Path, "rename", _raise_first_rename)

        # Simulate a crash after copytree partially populates the sibling tmp dir
        # but before the final rename.  We write one file then raise.
        _real_copytree = shutil.copytree

        def _partial_copytree(src: Any, dst: Any, **kwargs: Any) -> None:
            # Create the destination dir and write exactly one file to simulate
            # a partial write, then raise.
            dst = Path(dst)
            dst.mkdir(parents=True, exist_ok=True)
            src_files = list(Path(src).glob("*.jsonl"))
            if src_files:
                shutil.copy2(src_files[0], dst / src_files[0].name)
            raise RuntimeError("simulated crash mid-copytree (partial write)")

        monkeypatch.setattr(shutil, "copytree", _partial_copytree)

        with pytest.raises(RuntimeError, match="simulated crash"):
            main._publish_leg(staging, trio, "datasets")

        # KEY ATOMICITY INVARIANT: trio_dir (the published llm_source/ leg) must
        # be absent.  The partial write went into the sibling tmp dir, which was
        # never renamed into trio_dir, so the final location is clean.
        assert not trio.exists(), (
            "llm_source/ must not exist after a crash before the final rename — "
            "found partial content which violates the atomicity invariant"
        )


# ── _publish_staging ────────────────────────────────────────────────────────


class TestPublishStaging:
    def test_invokes_all_three_legs(
        self,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config

        calls: list[tuple[Path, Path, str]] = []

        def _fake(staging: Path, trio: Path, leg: str) -> bool:
            calls.append((staging, trio, leg))
            return leg == "datasets"  # return a varying bool to verify mapping

        monkeypatch.setattr(main, "_publish_leg", _fake)
        result = main._publish_staging()

        assert result == {"datasets": True, "dictionary": False}
        assert len(calls) == 2

        legs_seen = {leg for _, _, leg in calls}
        assert legs_seen == {"datasets", "dictionary"}

        # Each call uses the staging → trio pair for its leg.
        pairs = {leg: (staging, trio) for staging, trio, leg in calls}
        assert pairs["datasets"] == (
            Path(config.STAGING_DATASETS_DIR),
            Path(config.TRIO_DATASETS_DIR),
        )
        assert pairs["dictionary"] == (
            Path(config.STAGING_DICTIONARY_DIR),
            Path(config.DICTIONARY_JSON_OUTPUT_DIR),
        )


# ── _cleanup_staging ────────────────────────────────────────────────────────


class TestCleanupStaging:
    def test_removes_existing_staging(self, monkeypatch_config: Path) -> None:
        import config

        # Seed the staging tree
        config.STUDY_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        (config.STUDY_STAGING_DIR / "stray.txt").write_text("x", encoding="utf-8")

        main._cleanup_staging()

        assert not config.STUDY_STAGING_DIR.exists()

    def test_noop_when_staging_absent(self, monkeypatch_config: Path) -> None:
        import shutil

        import config

        if config.STUDY_STAGING_DIR.exists():
            shutil.rmtree(config.STUDY_STAGING_DIR)

        # Must not raise.
        main._cleanup_staging()
        assert not config.STUDY_STAGING_DIR.exists()


# ── _emit_output_signpost ───────────────────────────────────────────────────


class TestEmitOutputSignpost:
    def test_writes_readme_with_expected_sections(self, monkeypatch_config: Path) -> None:
        import config

        main._emit_output_signpost()

        readme = Path(config.STUDY_OUTPUT_DIR) / "README.md"
        assert readme.is_file()
        body = readme.read_text(encoding="utf-8")

        # Orientation — must name every top-level subtree.
        assert "`llm_source/`" in body
        assert "`audit/`" in body
        assert "`agent/`" in body

        # Zone taxonomy language preserved.
        assert "GREEN zone" in body
        assert "lineage_manifest.json" in body

        # Must include the current study name and version.
        assert config.STUDY_NAME in body
        from __version__ import __version__

        assert __version__ in body

    def test_is_idempotent_and_overwrites_stale_content(self, monkeypatch_config: Path) -> None:
        import config

        readme = Path(config.STUDY_OUTPUT_DIR) / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text("STALE CONTENT", encoding="utf-8")

        main._emit_output_signpost()

        body = readme.read_text(encoding="utf-8")
        assert "STALE CONTENT" not in body
        assert "llm_source/" in body

    def test_creates_study_output_dir_when_absent(
        self, monkeypatch_config: Path, tmp_path: Path
    ) -> None:
        import config

        # Point STUDY_OUTPUT_DIR at a fresh location the helper must create.
        fresh = tmp_path / "fresh-study-root"
        assert not fresh.exists()
        # monkeypatch_config already installed study-scoped paths; override here.
        config.STUDY_OUTPUT_DIR = fresh  # type: ignore[attr-defined]

        main._emit_output_signpost()

        assert fresh.is_dir()
        assert (fresh / "README.md").is_file()
