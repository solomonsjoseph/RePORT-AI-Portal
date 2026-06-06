"""Tests for the immutable study-snapshot subsystem (W1).

Covered:

* write -> list -> load round-trip
* immutability — a second write with the same id raises
* deterministic id — same llm_source content + run_id -> same id
* manifest content hashes are stable / present
* ``.NO_LLM_ZONE`` sentinel at the snapshot root
* security zone: ``validate_agent_read`` REJECTS ``snapshots/{id}/`` and
  ``snapshots/{id}/phi_handling_approval.json``; PERMITS
  ``snapshots/{id}/llm_source/...`` once that subtree is selected
* the realpath snapshot-root guard exempts only ``<id>/llm_source/``

All filesystem state is under ``monkeypatch_config``'s tmp_path; no real study
data is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.ai_assistant.file_access import (
    ZoneViolationError,
    is_agent_readable,
    validate_agent_read,
)
from scripts.audit.zone_guards import (
    SnapshotZoneViolation,
    deny_if_snapshot_root,
)
from scripts.utils.snapshot import (
    MANIFEST_FILENAME,
    SnapshotError,
    SnapshotExistsError,
    SnapshotNotFoundError,
    list_snapshots,
    load_snapshot,
    select_snapshot_llm_source,
    snapshot_llm_source_path,
    snapshot_path,
    snapshots_root,
    write_snapshot,
)

RUN_ID = "run_testfixedid0001"


# ── helpers ────────────────────────────────────────────────────────────────


def _seed_llm_source(root: Path, *, marker: str = "alpha") -> None:
    """Populate the (already config-patched) llm_source tree with scrubbed files."""
    ds = root / "dataset_schema" / "files"
    dd = root / "dictionary_mapping" / "jsonl"
    ds.mkdir(parents=True, exist_ok=True)
    dd.mkdir(parents=True, exist_ok=True)
    (ds / "1A_form.jsonl").write_text(
        json.dumps({"SUBJID": "RID_X_aaaaaaaaaaaa", "RESULT": marker}) + "\n",
        encoding="utf-8",
    )
    (dd / "1A_form.jsonl").write_text(
        json.dumps({"variable_id": "RESULT", "label": "result"}) + "\n",
        encoding="utf-8",
    )


def _seed_run_artifacts(
    study: str,
    run_id: str,
    *,
    approved: list[str] | None = None,
    held: list[str] | None = None,
    verifier_passed: bool = True,
) -> Path:
    """Write phi_handling_approval.json + verifier_report.json for a run."""
    run_dir = Path(config.OUTPUT_DIR) / study / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    approval = {
        "run_id": run_id,
        "study": study,
        "approved_forms": approved if approved is not None else ["1A_form.xlsx"],
        "held_forms": held if held is not None else [],
        "status": "approved" if not held else "partial",
    }
    (run_dir / "phi_handling_approval.json").write_text(json.dumps(approval), encoding="utf-8")
    verifier = {"run_id": run_id, "verifier_passed": verifier_passed, "assertions": []}
    (run_dir / "verifier_report.json").write_text(json.dumps(verifier), encoding="utf-8")
    return run_dir


# ── path helpers ────────────────────────────────────────────────────────────


class TestPathHelpers:
    def test_snapshots_root_under_output(self, monkeypatch_config: Path) -> None:
        root = snapshots_root(config.STUDY_NAME)
        assert root == Path(config.OUTPUT_DIR) / config.STUDY_NAME / "snapshots"

    def test_snapshot_path_none_is_root(self, monkeypatch_config: Path) -> None:
        assert snapshot_path(config.STUDY_NAME) == snapshots_root(config.STUDY_NAME)

    def test_snapshot_path_with_id(self, monkeypatch_config: Path) -> None:
        p = snapshot_path(config.STUDY_NAME, "snap_abc")
        assert p == snapshots_root(config.STUDY_NAME) / "snap_abc"

    def test_llm_source_path(self, monkeypatch_config: Path) -> None:
        p = snapshot_llm_source_path(config.STUDY_NAME, "snap_abc")
        assert p == snapshots_root(config.STUDY_NAME) / "snap_abc" / "llm_source"

    def test_empty_study_rejected(self) -> None:
        with pytest.raises(SnapshotError):
            snapshots_root("")

    @pytest.mark.parametrize("bad", ["../escape", "a/b", ".", "..", "x\x00y"])
    def test_path_traversal_id_rejected(self, monkeypatch_config: Path, bad: str) -> None:
        with pytest.raises(SnapshotError):
            snapshot_path(config.STUDY_NAME, bad)


# ── write / list / load round-trip ───────────────────────────────────────────


class TestWriteListLoad:
    def test_round_trip(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)

        dest = write_snapshot(study, RUN_ID)
        assert dest.is_dir()

        ids = list_snapshots(study)
        assert ids == [dest.name]

        manifest = load_snapshot(study, dest.name)
        assert manifest["source_run_id"] == RUN_ID
        assert manifest["study"] == study
        assert manifest["snapshot_id"] == dest.name
        assert manifest["verifier_passed"] is True
        assert manifest["approved_forms"] == ["1A_form.xlsx"]
        assert manifest["held_forms"] == []

    def test_copies_llm_source_tree(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)

        copied = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        assert copied.is_file()
        # content preserved verbatim
        original = (
            config.STUDY_LLM_SOURCE_DIR / "dataset_schema" / "files" / "1A_form.jsonl"
        ).read_text(encoding="utf-8")
        assert copied.read_text(encoding="utf-8") == original

    def test_copies_approval_and_verifier(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)

        assert (dest / "phi_handling_approval.json").is_file()
        assert (dest / "verifier_report.json").is_file()
        assert (dest / MANIFEST_FILENAME).is_file()

    def test_held_forms_recorded(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID, approved=["1A_form.xlsx"], held=["9Z_held.xlsx"])
        dest = write_snapshot(study, RUN_ID)
        manifest = load_snapshot(study, dest.name)
        assert manifest["held_forms"] == ["9Z_held.xlsx"]
        assert manifest["approved_forms"] == ["1A_form.xlsx"]

    def test_list_empty_when_no_snapshots(self, monkeypatch_config: Path) -> None:
        assert list_snapshots(config.STUDY_NAME) == []

    def test_list_ignores_partial_dirs(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        write_snapshot(study, RUN_ID)
        # A leftover ``.partial`` dir must not be listed.
        stray = snapshots_root(study) / ".snap_stray.partial"
        stray.mkdir(parents=True, exist_ok=True)
        (stray / MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
        assert ".snap_stray.partial" not in list_snapshots(study)


# ── immutability ─────────────────────────────────────────────────────────────


class TestImmutability:
    def test_second_write_same_id_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)
        snap_id = dest.name

        # Same content + run_id -> same deterministic id -> immutability trips.
        with pytest.raises(SnapshotExistsError):
            write_snapshot(study, RUN_ID)
        # The prior snapshot is untouched.
        assert list_snapshots(study) == [snap_id]

    def test_explicit_existing_id_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        write_snapshot(study, RUN_ID, snapshot_id="snap_fixed")
        with pytest.raises(SnapshotExistsError):
            write_snapshot(study, RUN_ID, snapshot_id="snap_fixed")


# ── deterministic id + stable manifest hashes ────────────────────────────────


class TestDeterministicId:
    def test_same_content_same_id(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR, marker="alpha")
        _seed_run_artifacts(study, RUN_ID)
        first = write_snapshot(study, RUN_ID).name

        # Wipe the snapshot dir; same inputs must mint the same id.
        import shutil

        shutil.rmtree(snapshots_root(study))
        second = write_snapshot(study, RUN_ID).name
        assert first == second

    def test_different_content_different_id(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR, marker="alpha")
        _seed_run_artifacts(study, RUN_ID)
        first = write_snapshot(study, RUN_ID).name

        # Mutate llm_source content -> different manifest -> different id.
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR, marker="beta")
        second = write_snapshot(study, RUN_ID).name
        assert first != second

    def test_different_run_id_different_id(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        first = write_snapshot(study, RUN_ID).name

        other_run = "run_othertestid0002"
        _seed_run_artifacts(study, other_run)
        second = write_snapshot(study, other_run).name
        assert first != second

    def test_manifest_hashes_match_files(self, monkeypatch_config: Path) -> None:
        import hashlib

        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)
        manifest = load_snapshot(study, dest.name)

        tree = manifest["llm_source_manifest"]
        assert "dataset_schema/files/1A_form.jsonl" in tree
        # Re-hash the copied file and compare to the recorded digest.
        copied = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        digest = hashlib.sha256(copied.read_bytes()).hexdigest()
        assert tree["dataset_schema/files/1A_form.jsonl"] == digest


# ── sentinel ─────────────────────────────────────────────────────────────────


class TestNoLlmSentinel:
    def test_sentinel_at_snapshot_root(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        dest = write_snapshot(study, RUN_ID)
        sentinel = dest / config.AUDIT_NO_LLM_SENTINEL_NAME
        assert sentinel.is_file()


# ── missing-artifact fail-closed ─────────────────────────────────────────────


class TestFailClosed:
    def test_missing_llm_source_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        # llm_source dir exists (created by fixture) but is empty -> still hashes
        # to an empty manifest; the failure we want is a *missing* tree.
        import shutil

        shutil.rmtree(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID)

    def test_missing_approval_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        run_dir = Path(config.OUTPUT_DIR) / study / "runs" / RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "verifier_report.json").write_text(
            json.dumps({"verifier_passed": True}), encoding="utf-8"
        )
        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID)

    def test_missing_verifier_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        run_dir = Path(config.OUTPUT_DIR) / study / "runs" / RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "phi_handling_approval.json").write_text(
            json.dumps({"approved_forms": [], "held_forms": []}), encoding="utf-8"
        )
        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID)

    def test_load_missing_snapshot_raises(self, monkeypatch_config: Path) -> None:
        with pytest.raises(SnapshotNotFoundError):
            load_snapshot(config.STUDY_NAME, "snap_doesnotexist")

    def test_select_missing_snapshot_raises(self, monkeypatch_config: Path) -> None:
        with pytest.raises(SnapshotNotFoundError):
            select_snapshot_llm_source(config.STUDY_NAME, "snap_doesnotexist")


# ── security zone ────────────────────────────────────────────────────────────


class TestSecurityZone:
    def _make_snapshot(self) -> Path:
        study = config.STUDY_NAME
        _seed_llm_source(config.STUDY_LLM_SOURCE_DIR)
        _seed_run_artifacts(study, RUN_ID)
        return write_snapshot(study, RUN_ID)

    def test_snapshot_root_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest)

    def test_snapshot_approval_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        approval = dest / "phi_handling_approval.json"
        assert approval.is_file()
        with pytest.raises(ZoneViolationError):
            validate_agent_read(approval)

    def test_snapshot_manifest_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest / MANIFEST_FILENAME)

    def test_snapshot_verifier_report_rejected(self, monkeypatch_config: Path) -> None:
        dest = self._make_snapshot()
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest / "verifier_report.json")

    def test_unselected_llm_source_rejected(self, monkeypatch_config: Path) -> None:
        """Until a snapshot is SELECTED (config repointed), even its
        llm_source subtree is outside the agent read zone."""
        dest = self._make_snapshot()
        leaf = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        assert leaf.is_file()
        with pytest.raises(ZoneViolationError):
            validate_agent_read(leaf)

    def test_selected_llm_source_permitted(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a maintainer selects the snapshot, the loader repoints
        ``config.STUDY_LLM_SOURCE_DIR`` at ``snapshots/{id}/llm_source/`` and
        that subtree (already PHI-scrubbed) becomes readable — while the
        approval/manifest at the snapshot root stay denied."""
        study = config.STUDY_NAME
        dest = self._make_snapshot()
        snap_llm_source = select_snapshot_llm_source(study, dest.name)
        monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", snap_llm_source)

        leaf = snap_llm_source / "dataset_schema" / "files" / "1A_form.jsonl"
        assert validate_agent_read(leaf) == Path(leaf.resolve())
        assert is_agent_readable(leaf)

        # The snapshot ROOT and approval file remain denied even when selected.
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest / "phi_handling_approval.json")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest)


# ── realpath snapshot-root guard (segment logic, independent of tmp paths) ────


class TestSnapshotRootGuardSegments:
    """``deny_if_snapshot_root`` keys on the literal ``output/<study>/snapshots/``
    path segments — exercised here with synthetic absolute paths so the segment
    logic is covered regardless of the tmp-path test layout (which lacks an
    ``output`` segment, relying instead on read-root containment)."""

    def test_root_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root("/srv/output/StudyX/snapshots/snap_abc")

    def test_approval_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root(
                "/srv/output/StudyX/snapshots/snap_abc/phi_handling_approval.json"
            )

    def test_manifest_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root("/srv/output/StudyX/snapshots/snap_abc/snapshot_manifest.json")

    def test_snapshots_dir_itself_denied(self) -> None:
        with pytest.raises(SnapshotZoneViolation):
            deny_if_snapshot_root("/srv/output/StudyX/snapshots")

    def test_llm_source_subtree_exempt(self) -> None:
        # Exempt — no raise.
        deny_if_snapshot_root(
            "/srv/output/StudyX/snapshots/snap_abc/llm_source/dataset_schema/files/x.jsonl"
        )

    def test_non_snapshot_path_exempt(self) -> None:
        deny_if_snapshot_root("/srv/output/StudyX/llm_source/x.jsonl")
        deny_if_snapshot_root("/some/random/path")
