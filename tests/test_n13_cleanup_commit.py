"""Tests for Note 13: the two-list workspace cleanup verifier + shared committer."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.utils.cleanup_verifier import verify_workspace_cleanup


def _setup_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    import config

    paths = {
        "BASE_DIR": tmp_path,
        "STUDY_STAGING_DIR": tmp_path / "tmp" / "Study",
        "STAGING_DATASETS_DIR": tmp_path / "tmp" / "Study" / "datasets",
        "STAGING_SOT_DIR": tmp_path / "tmp" / "Study" / "SoT",
        "STAGING_HEADERS_DIR": tmp_path / "tmp" / "Study" / "headers",
        "STUDY_LLM_SOURCE_DIR": tmp_path / "out" / "llm_source",
        "STUDY_AUDIT_DIR": tmp_path / "out" / "audit",
        "STUDY_SNAPSHOTS_OUTPUT_DIR": tmp_path / "out" / "snapshots",
        "STUDY_CONFIG_DIR": tmp_path / "config" / "Study",
        "STUDY_DATA_DIR": tmp_path / "data" / "raw" / "Study",
    }
    for k, v in paths.items():
        monkeypatch.setattr(config, k, v, raising=False)
    # must-remain present; must-be-gone absent
    for k in (
        "STUDY_LLM_SOURCE_DIR",
        "STUDY_AUDIT_DIR",
        "STUDY_SNAPSHOTS_OUTPUT_DIR",
        "STUDY_CONFIG_DIR",
        "STUDY_DATA_DIR",
    ):
        paths[k].mkdir(parents=True, exist_ok=True)
    return paths


def test_clean_workspace_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_workspace(monkeypatch, tmp_path)
    run_dir = tmp_path / "out" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    rep = verify_workspace_cleanup(study="Study", run_dir=run_dir)
    assert rep.ok, [f.target for f in rep.findings]
    assert rep.checked_must_gone >= 5
    assert rep.checked_must_remain == 5


def test_must_gone_present_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _setup_workspace(monkeypatch, tmp_path)
    run_dir = tmp_path / "out" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    paths["STAGING_DATASETS_DIR"].mkdir(parents=True, exist_ok=True)  # leftover staging
    rep = verify_workspace_cleanup(study="Study", run_dir=run_dir)
    assert not rep.ok
    assert any(f.phase == "must_gone" for f in rep.findings)


def test_must_remain_missing_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    paths = _setup_workspace(monkeypatch, tmp_path)
    run_dir = tmp_path / "out" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    shutil.rmtree(paths["STUDY_LLM_SOURCE_DIR"])  # data-loss event
    rep = verify_workspace_cleanup(study="Study", run_dir=run_dir)
    assert not rep.ok
    assert any(f.phase == "must_remain" for f in rep.findings)


def test_anomaly_unexpected_staging_leftover_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # N13 Phase 3: an unexpected leftover under the ephemeral staging root (not a
    # known staging subdir) is flagged as an anomaly — never deleted, halts the run.
    paths = _setup_workspace(monkeypatch, tmp_path)
    run_dir = tmp_path / "out" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    paths["STUDY_STAGING_DIR"].mkdir(parents=True, exist_ok=True)
    (paths["STUDY_STAGING_DIR"] / "garbage_from_failed_run.tmp").write_text(
        "x", encoding="utf-8"
    )
    rep = verify_workspace_cleanup(study="Study", run_dir=run_dir)
    assert not rep.ok
    assert any(f.phase == "anomaly" for f in rep.findings)
    assert rep.checked_anomaly >= 1


def test_anomaly_skips_known_staging_subdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A known staging subdir present is a Phase-1 must_gone finding, NOT a Phase-3
    # anomaly (no double-report / false positive).
    paths = _setup_workspace(monkeypatch, tmp_path)
    run_dir = tmp_path / "out" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    paths["STAGING_DATASETS_DIR"].mkdir(parents=True, exist_ok=True)
    rep = verify_workspace_cleanup(study="Study", run_dir=run_dir)
    assert any(f.phase == "must_gone" for f in rep.findings)
    assert not any(f.phase == "anomaly" for f in rep.findings)


def test_held_cleanup_token_excluded_from_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_workspace(monkeypatch, tmp_path)
    run_dir = tmp_path / "out" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "cleanup.in_progress").write_text("1", encoding="utf-8")
    # When the orchestrator holds the token, it must NOT be flagged.
    assert verify_workspace_cleanup(
        study="Study", run_dir=run_dir, expect_cleanup_token_present=True
    ).ok
    # Without the exemption, the same token IS a must-gone violation.
    assert not verify_workspace_cleanup(study="Study", run_dir=run_dir).ok


def test_commit_run_snapshot_records_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """commit_run_snapshot writes snapshot_id into status.json (write_snapshot mocked)."""
    import scripts.utils.snapshot as snap

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(json.dumps({"study": "S"}), encoding="utf-8")

    captured: dict = {}

    def _fake_write_snapshot(study, run_id, **kwargs):
        captured.update(kwargs)
        return tmp_path / "snapshots" / "snap_TEST"

    monkeypatch.setattr(snap, "write_snapshot", _fake_write_snapshot)

    sid = snap.commit_run_snapshot(
        study="S", run_id="r1", run_dir=run_dir, cleanup_verifier_passed=True
    )
    assert sid == "snap_TEST"
    status = json.loads((run_dir / "status.json").read_text())
    assert status["snapshot_id"] == "snap_TEST"
    # the proof flag is threaded through to write_snapshot (Note 14 residual)
    assert captured.get("cleanup_verifier_passed") is True


def test_commit_run_snapshot_immutability_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.utils.snapshot as snap

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text(json.dumps({"study": "S"}), encoding="utf-8")

    def _raise(*a, **k):
        raise snap.SnapshotExistsError("already exists")

    monkeypatch.setattr(snap, "write_snapshot", _raise)
    assert snap.commit_run_snapshot(study="S", run_id="r1", run_dir=run_dir) is None
    status = json.loads((run_dir / "status.json").read_text())
    assert "snapshot_id" not in status  # immutability guard is benign, no failure recorded
    assert "snapshot_failed" not in status


def test_standalone_inline_snapshot_cleanup_helper_persists_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config
    import scripts.skills.extract_to_llm_source as skill_mod
    from scripts.utils import cleanup_verifier

    paths = _setup_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", tmp_path / "out" / "llm_source" / "files")
    run_dir = tmp_path / "out" / "runs" / "run_cleanup"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(
        cleanup_verifier,
        "verify_cleanup",
        lambda *_args: SimpleNamespace(ok=True, findings=[]),
    )
    monkeypatch.setattr(
        cleanup_verifier,
        "verify_workspace_cleanup",
        lambda **_kwargs: SimpleNamespace(
            ok=True,
            findings=[],
            checked_must_gone=6,
            checked_must_remain=5,
            checked_anomaly=0,
        ),
    )

    assert skill_mod._verify_cleanup_before_inline_snapshot(study="Study", run_dir=run_dir)

    report = paths["STUDY_AUDIT_DIR"] / "cleanup_verification_report.json"
    assert report.is_file()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ledger_ok"] is True
    assert data["workspace_ok"] is True
    assert data["checked_must_remain"] == 5
