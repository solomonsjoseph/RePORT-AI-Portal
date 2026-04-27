"""Unit tests for the staging helpers added to main.py in Task 5.

Covers:

- _prepare_staging: purges residue + creates the three leg subdirs.
- _publish_leg: rename happy path, skip-when-empty, trio-exists overwrite,
  cross-filesystem fallback via a patched OSError.
- _publish_staging: invokes _publish_leg three times with the right pairs.
- _cleanup_staging: removes the staging tree when present + no-op otherwise.
"""

from __future__ import annotations

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
        assert config.STAGING_PDFS_DIR.is_dir()

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
        assert config.STAGING_PDFS_DIR.is_dir()


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

    def test_cross_filesystem_rename_falls_back_to_copytree(
        self,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config

        staging = config.STAGING_DATASETS_DIR
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "a.jsonl").write_text("x", encoding="utf-8")

        trio = config.TRIO_DATASETS_DIR
        trio.mkdir(parents=True, exist_ok=True)

        # Force rename to fail like an EXDEV cross-device error.
        def _boom(self: Path, target: Any) -> None:
            raise OSError("cross-filesystem rename not supported")

        monkeypatch.setattr(Path, "rename", _boom)

        published = main._publish_leg(staging, trio, "datasets")

        assert published is True
        assert (trio / "a.jsonl").read_text(encoding="utf-8") == "x"
        assert not staging.exists(), "staging source should be cleaned after copytree"


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

        assert result == {"datasets": True, "dictionary": False, "pdfs": False}
        assert len(calls) == 3

        legs_seen = {leg for _, _, leg in calls}
        assert legs_seen == {"datasets", "dictionary", "pdfs"}

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
        assert pairs["pdfs"] == (
            Path(config.STAGING_PDFS_DIR),
            Path(config.PDF_EXTRACTIONS_DIR),
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
        assert "`trio_bundle/`" in body
        assert "`audit/`" in body
        assert "`agent/`" in body

        # Zone taxonomy language preserved.
        assert "GREEN zone" in body
        assert "lineage_manifest.json" in body

        # Must include the current study name and version.
        assert config.STUDY_NAME in body
        from __version__ import __version__

        assert __version__ in body

    def test_is_idempotent_and_overwrites_stale_content(
        self, monkeypatch_config: Path
    ) -> None:
        import config

        readme = Path(config.STUDY_OUTPUT_DIR) / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text("STALE CONTENT", encoding="utf-8")

        main._emit_output_signpost()

        body = readme.read_text(encoding="utf-8")
        assert "STALE CONTENT" not in body
        assert "trio_bundle/" in body

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
