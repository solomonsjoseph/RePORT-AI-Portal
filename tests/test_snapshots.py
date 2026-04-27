"""Tests for trio_bundle snapshot / restore helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.utils import snapshots


@pytest.fixture
def _isolated_trio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Build a tiny synthetic trio bundle + snapshot root under ``tmp_path``."""
    import config

    trio = tmp_path / "trio_bundle"
    snaps = tmp_path / "snapshots"
    (trio / "datasets").mkdir(parents=True)
    (trio / "dictionary").mkdir(parents=True)
    (trio / "pdfs").mkdir(parents=True)
    (trio / "variables.json").write_text('{"hello": "world"}', encoding="utf-8")
    (trio / "datasets" / "1_demo.jsonl").write_text(
        '{"row": 1}\n', encoding="utf-8"
    )

    monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", trio)
    monkeypatch.setattr(config, "STUDY_SNAPSHOTS_DIR", snaps)
    return trio, snaps


def test_create_snapshot_default_name(_isolated_trio: tuple[Path, Path]) -> None:
    _, snaps = _isolated_trio
    path = snapshots.create_snapshot()
    assert path.exists()
    assert path.parent == snaps
    assert path.name.startswith("run-")
    assert (path / "variables.json").read_text(encoding="utf-8") == '{"hello": "world"}'


def test_create_snapshot_named(_isolated_trio: tuple[Path, Path]) -> None:
    _, snaps = _isolated_trio
    path = snapshots.create_snapshot("checkpoint-a")
    assert path == snaps / "checkpoint-a"
    assert (path / "datasets" / "1_demo.jsonl").exists()


def test_create_snapshot_rejects_bad_name(_isolated_trio: tuple[Path, Path]) -> None:
    with pytest.raises(snapshots.SnapshotError):
        snapshots.create_snapshot("bad/name")
    with pytest.raises(snapshots.SnapshotError):
        snapshots.create_snapshot(".hidden")


def test_create_snapshot_requires_trio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config

    monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", tmp_path / "does-not-exist")
    monkeypatch.setattr(config, "STUDY_SNAPSHOTS_DIR", tmp_path / "snaps")
    with pytest.raises(snapshots.SnapshotError):
        snapshots.create_snapshot("x")


def test_create_snapshot_duplicate_without_overwrite(
    _isolated_trio: tuple[Path, Path],
) -> None:
    snapshots.create_snapshot("dup")
    with pytest.raises(snapshots.SnapshotError):
        snapshots.create_snapshot("dup")


def test_create_snapshot_overwrite(_isolated_trio: tuple[Path, Path]) -> None:
    trio, _ = _isolated_trio
    snapshots.create_snapshot("dup")
    # Change the live bundle then snapshot again with overwrite=True.
    (trio / "variables.json").write_text('{"v": 2}', encoding="utf-8")
    path = snapshots.create_snapshot("dup", overwrite=True)
    assert (path / "variables.json").read_text(encoding="utf-8") == '{"v": 2}'


def test_list_snapshots_newest_first(
    _isolated_trio: tuple[Path, Path],
) -> None:
    import os
    import time

    snapshots.create_snapshot("first")
    time.sleep(0.01)
    # bump mtime of second snapshot explicitly in case filesystem is coarse.
    p2 = snapshots.create_snapshot("second")
    now = time.time() + 5
    os.utime(p2, (now, now))

    names = snapshots.list_snapshots()
    assert names[0] == "second"
    assert "first" in names


def test_latest_snapshot_prefers_initial(
    _isolated_trio: tuple[Path, Path],
) -> None:
    snapshots.create_snapshot("run-one")
    snapshots.create_snapshot("initial")
    assert snapshots.latest_snapshot_name() == "initial"


def test_latest_snapshot_none_when_empty(
    _isolated_trio: tuple[Path, Path],
) -> None:
    assert snapshots.latest_snapshot_name() is None


def test_restore_snapshot_overwrites_live(
    _isolated_trio: tuple[Path, Path],
) -> None:
    trio, _ = _isolated_trio
    snapshots.create_snapshot("good")
    # Corrupt live bundle.
    (trio / "variables.json").write_text('{"bad": true}', encoding="utf-8")
    (trio / "datasets" / "1_demo.jsonl").unlink()

    snapshots.restore_snapshot("good")
    assert (trio / "variables.json").read_text(encoding="utf-8") == '{"hello": "world"}'
    assert (trio / "datasets" / "1_demo.jsonl").exists()


def test_restore_snapshot_missing(_isolated_trio: tuple[Path, Path]) -> None:
    with pytest.raises(snapshots.SnapshotError):
        snapshots.restore_snapshot("does-not-exist")


def test_restore_snapshot_rejects_bad_name(
    _isolated_trio: tuple[Path, Path],
) -> None:
    with pytest.raises(snapshots.SnapshotError):
        snapshots.restore_snapshot("../etc")


# ── resolve_snapshot_name ────────────────────────────────────────────────────


class TestResolveSnapshotName:
    def test_none_yields_timestamp(self) -> None:
        out = snapshots.resolve_snapshot_name(None)
        assert out.startswith("run-")
        # YYYYmmddTHHMMSSZ → 16 chars after the "run-" prefix.
        assert len(out) == len("run-YYYYmmddTHHMMSSZ")

    def test_empty_string_yields_timestamp(self) -> None:
        assert snapshots.resolve_snapshot_name("").startswith("run-")
        assert snapshots.resolve_snapshot_name("   ").startswith("run-")

    def test_explicit_name_preserved(self) -> None:
        assert snapshots.resolve_snapshot_name("cohort-a") == "cohort-a"

    def test_whitespace_stripped(self) -> None:
        assert snapshots.resolve_snapshot_name("  cohort-a  ") == "cohort-a"


# ── main (CLI) ───────────────────────────────────────────────────────────────


class TestCli:
    def test_create_with_default_name_succeeds(
        self, _isolated_trio: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, snaps = _isolated_trio
        rc = snapshots.main(["create"])
        assert rc == 0
        out = capsys.readouterr().out
        # Banner names the target directory BEFORE the ✓ line (C).
        assert "Copying" in out
        assert "→" in out
        # Exactly one snapshot created, starting with run-.
        names = [p.name for p in snaps.iterdir() if p.is_dir()]
        assert len(names) == 1
        assert names[0].startswith("run-")

    def test_create_with_name_override(
        self, _isolated_trio: tuple[Path, Path]
    ) -> None:
        _, snaps = _isolated_trio
        rc = snapshots.main(["create", "--name", "cohort-a"])
        assert rc == 0
        assert (snaps / "cohort-a").is_dir()

    def test_create_without_force_rejects_existing(
        self, _isolated_trio: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No --force flag → refuses to overwrite. This is the B guarantee:
        FORCE=0 in the Makefile translates to "no flag passed" here, so
        the CLI must refuse (not silently overwrite as v1's bool('0')==True
        would have done)."""
        snapshots.main(["create", "--name", "cohort-a"])
        rc = snapshots.main(["create", "--name", "cohort-a"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_create_with_force_overwrites(
        self, _isolated_trio: tuple[Path, Path]
    ) -> None:
        trio, snaps = _isolated_trio
        snapshots.main(["create", "--name", "cohort-a"])
        # Modify the trio bundle so the overwrite is observable.
        (trio / "variables.json").write_text('{"hello": "v2"}', encoding="utf-8")
        rc = snapshots.main(["create", "--name", "cohort-a", "--force"])
        assert rc == 0
        assert (
            (snaps / "cohort-a" / "variables.json").read_text(encoding="utf-8")
            == '{"hello": "v2"}'
        )

    def test_list_empty_and_populated(
        self, _isolated_trio: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = snapshots.main(["list"])
        assert rc == 0
        assert "No snapshots available." in capsys.readouterr().out

        snapshots.main(["create", "--name", "cohort-a"])
        snapshots.main(["create", "--name", "cohort-b"])
        capsys.readouterr()  # clear

        rc = snapshots.main(["list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "cohort-a" in out
        assert "cohort-b" in out

    def test_restore_round_trip(
        self, _isolated_trio: tuple[Path, Path]
    ) -> None:
        trio, _ = _isolated_trio
        snapshots.main(["create", "--name", "baseline"])
        # Corrupt the live trio bundle.
        (trio / "variables.json").write_text('{"hello": "corrupt"}', encoding="utf-8")
        rc = snapshots.main(["restore", "baseline"])
        assert rc == 0
        assert (trio / "variables.json").read_text(encoding="utf-8") == '{"hello": "world"}'

    def test_missing_subcommand_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit):
            snapshots.main([])
