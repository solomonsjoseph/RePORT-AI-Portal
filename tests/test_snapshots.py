"""Tests for the reviewed snapshot baseline helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.utils import snapshots


@pytest.fixture
def _isolated_trio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    import config

    trio = tmp_path / "output" / "Indo-VAP" / "trio_bundle"
    snap = tmp_path / "data" / "snapshots" / "Indo-VAP"
    (trio / "datasets").mkdir(parents=True)
    (trio / "dictionary").mkdir(parents=True)
    (trio / "pdfs").mkdir(parents=True)
    (trio / "variables.json").write_text('{"hello": "world"}', encoding="utf-8")
    (trio / "datasets" / "1_demo.jsonl").write_text('{"row": 1}\n', encoding="utf-8")

    monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", trio)
    monkeypatch.setattr(config, "STUDY_SNAPSHOTS_DIR", snap)
    monkeypatch.setattr(config, "STUDY_NAME", "Indo-VAP")
    return trio, snap


def test_create_snapshot_saves_single_reviewed_baseline(
    _isolated_trio: tuple[Path, Path],
) -> None:
    _, snap = _isolated_trio
    path = snapshots.create_snapshot()
    assert path == snap
    assert snapshots.snapshot_exists()
    assert (snap / "variables.json").read_text(encoding="utf-8") == '{"hello": "world"}'
    assert (snap / "datasets" / "1_demo.jsonl").exists()


def test_create_snapshot_requires_force_to_overwrite(
    _isolated_trio: tuple[Path, Path],
) -> None:
    trio, snap = _isolated_trio
    snapshots.create_snapshot()
    (trio / "variables.json").write_text('{"v": 2}', encoding="utf-8")

    with pytest.raises(snapshots.SnapshotError):
        snapshots.create_snapshot()

    snapshots.create_snapshot(overwrite=True)
    assert (snap / "variables.json").read_text(encoding="utf-8") == '{"v": 2}'


def test_create_snapshot_requires_live_trio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config

    monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", tmp_path / "missing")
    monkeypatch.setattr(config, "STUDY_SNAPSHOTS_DIR", tmp_path / "data" / "snapshots" / "x")
    with pytest.raises(snapshots.SnapshotError):
        snapshots.create_snapshot()


def test_restore_snapshot_overwrites_live_trio(
    _isolated_trio: tuple[Path, Path],
) -> None:
    trio, _ = _isolated_trio
    snapshots.create_snapshot()

    (trio / "variables.json").write_text('{"bad": true}', encoding="utf-8")
    (trio / "datasets" / "1_demo.jsonl").unlink()

    snapshots.restore_snapshot()
    assert (trio / "variables.json").read_text(encoding="utf-8") == '{"hello": "world"}'
    assert (trio / "datasets" / "1_demo.jsonl").exists()


def test_restore_snapshot_missing(_isolated_trio: tuple[Path, Path]) -> None:
    with pytest.raises(snapshots.SnapshotError):
        snapshots.restore_snapshot()


def test_list_and_latest_snapshot_are_single_baseline(
    _isolated_trio: tuple[Path, Path],
) -> None:
    assert snapshots.list_snapshots() == []
    assert snapshots.latest_snapshot_name() is None

    snapshots.create_snapshot()
    assert snapshots.list_snapshots() == ["Indo-VAP"]
    assert snapshots.latest_snapshot_name() == "Indo-VAP"
    assert snapshots.resolve_snapshot_name("anything") == "Indo-VAP"


class TestCli:
    def test_create_restore_round_trip(
        self,
        _isolated_trio: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        trio, snap = _isolated_trio
        assert snapshots.main(["create"]) == 0
        assert snap.is_dir()

        (trio / "variables.json").write_text('{"bad": true}', encoding="utf-8")
        assert snapshots.main(["restore"]) == 0
        assert (trio / "variables.json").read_text(encoding="utf-8") == '{"hello": "world"}'

        out = capsys.readouterr().out
        assert "Reviewed snapshot" in out

    def test_create_requires_force_for_existing(
        self,
        _isolated_trio: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert snapshots.main(["create"]) == 0
        assert snapshots.main(["create"]) == 1
        assert "already exists" in capsys.readouterr().err

    def test_list_reports_absence_and_presence(
        self,
        _isolated_trio: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert snapshots.main(["list"]) == 0
        assert "No reviewed snapshot" in capsys.readouterr().out

        snapshots.main(["create"])
        capsys.readouterr()
        assert snapshots.main(["list"]) == 0
        assert "Reviewed snapshot baseline" in capsys.readouterr().out
