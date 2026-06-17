"""Tests for the Wave-5 snapshot overhaul (Note 14, C5.1-C5.7).

Covers the net-new snapshot subsystem behaviour layered on top of the W1
immutable-snapshot core (see ``tests/test_snapshot.py``):

* C5.1 timestamp ids + C5.2 expanded manifest / config capture
* C5.3 current pointer
* C5.4 staleness (four triggers; pure classifier)
* C5.5 input-fingerprint redundant-run snapshot lookup
* C5.6 snapshot diff
* C5.7 partial snapshot + tampering re-hash

All filesystem state is under ``monkeypatch_config``'s tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.utils.snapshot import (
    SnapshotError,
    SnapshotNotFoundError,
    SnapshotTamperedError,
    StalenessSeverity,
    check_snapshot_staleness,
    current_pointer_path,
    diff_snapshots,
    find_snapshot_by_fingerprint,
    get_current_snapshot,
    latest_snapshot,
    load_snapshot,
    set_current_snapshot,
    snapshot_diff_path,
    verify_snapshot_integrity,
    write_snapshot,
    write_snapshot_diff,
)

RUN_ID = "run_testfixedid0001"


# ── helpers (mirror tests/test_snapshot.py) ──────────────────────────────────


def _live_llm_source(study: str) -> Path:
    return Path(config.OUTPUT_DIR) / study / "llm_source"


def _seed_llm_source(
    root: Path, *, marker: str = "alpha", forms: tuple[str, ...] = ("1A_form",)
) -> None:
    ds = root / "dataset_schema" / "files"
    dd = root / "dictionary_mapping" / "jsonl"
    ds.mkdir(parents=True, exist_ok=True)
    dd.mkdir(parents=True, exist_ok=True)
    for form in forms:
        (ds / f"{form}.jsonl").write_text(
            json.dumps({"SUBJID": "RID_X_aaaaaaaaaaaa", "RESULT": marker}) + "\n",
            encoding="utf-8",
        )
    (dd / "1A_form.jsonl").write_text(
        json.dumps({"variable_id": "RESULT", "label": "result"}) + "\n",
        encoding="utf-8",
    )


def _seed_run_artifacts(study: str, run_id: str, *, verifier_passed: bool = True) -> Path:
    run_dir = Path(config.OUTPUT_DIR) / study / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "phi_handling_approval.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "study": study,
                "approved_forms": ["1A_form.xlsx"],
                "held_forms": [],
                "status": "approved",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "verifier_report.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "overall": "pass" if verifier_passed else "fail",
                "exit_code": 0 if verifier_passed else 5,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _seed_config_files(study: str) -> None:
    privacy = config.study_config_path("_study_privacy.yaml", study=study)
    manifest = config.study_config_path("_forms_manifest.yaml", study=study)
    Path(privacy).parent.mkdir(parents=True, exist_ok=True)
    Path(privacy).write_text("jurisdictions: [USA]\n", encoding="utf-8")
    Path(manifest).write_text("required: []\n", encoding="utf-8")


def _make_snapshot(study: str, *, run_id: str = RUN_ID, **kwargs) -> Path:
    _seed_llm_source(_live_llm_source(study))
    _seed_run_artifacts(study, run_id)
    return write_snapshot(study, run_id, **kwargs)


# ── C5.2 expanded manifest + config capture ──────────────────────────────────


class TestExpandedManifest:
    def test_schema_and_provenance_fields_present(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study, created_utc="2026-06-15T14:32:00Z")
        m = load_snapshot(study, dest.name)
        assert m["manifest_schema"] == 2
        assert m["created_utc"] == "2026-06-15T14:32:00Z"
        assert m["snapshot_type"] == 1
        assert m["partial"] is False
        assert m["absent_forms"] == []
        assert m["human_review_records"] == []
        # Provenance keys present (values may be None when sources are absent).
        for key in (
            "phi_rulebook_version",
            "phi_key_fingerprint",
            "compliance_posture",
            "input_fingerprint",
            "input_fingerprint_components",
            "config_files",
            "content_hash",
        ):
            assert key in m

    def test_config_files_captured(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_config_files(study)
        dest = _make_snapshot(study)
        # Copied into the snapshot root.
        assert (dest / "_study_privacy.yaml").is_file()
        assert (dest / "_forms_manifest.yaml").is_file()
        m = load_snapshot(study, dest.name)
        assert m["config_files"]["_study_privacy.yaml"] is not None
        assert m["config_files"]["_forms_manifest.yaml"] is not None

    def test_missing_config_files_are_fail_soft(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)  # no config files seeded
        m = load_snapshot(study, dest.name)
        assert m["config_files"]["_study_privacy.yaml"] is None

    def test_type2_and_human_review_records(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        records = [{"form": "2A", "decision": "kept", "when": "2026-06-15"}]
        dest = _make_snapshot(study, snapshot_type=2, human_review_records=records)
        m = load_snapshot(study, dest.name)
        assert m["snapshot_type"] == 2
        assert m["human_review_records"] == records

    def test_invalid_snapshot_type_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        with pytest.raises(SnapshotError):
            write_snapshot(study, RUN_ID, snapshot_type=3)


# ── C5.7 partial snapshot ─────────────────────────────────────────────────────


class TestPartialSnapshot:
    def test_partial_manifest_labels(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study, partial=True, absent_forms=["9Z_lostpdf"])
        m = load_snapshot(study, dest.name)
        assert m["partial"] is True
        assert m["absent_forms"] == ["9Z_lostpdf"]


# ── C5.3 current pointer ──────────────────────────────────────────────────────


class TestCurrentPointer:
    def test_set_get_roundtrip(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        assert get_current_snapshot(study) is None
        pointer = set_current_snapshot(study, dest.name, updated_utc="2026-06-15T15:00:00Z")
        assert pointer == current_pointer_path(study)
        assert get_current_snapshot(study) == dest.name

    def test_set_nonexistent_raises(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        with pytest.raises(SnapshotNotFoundError):
            set_current_snapshot(study, "snap_doesnotexist")

    def test_get_corrupt_pointer_is_none(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _make_snapshot(study)
        current_pointer_path(study).write_text("not json", encoding="utf-8")
        assert get_current_snapshot(study) is None

    def test_pointer_not_listed_as_snapshot(self, monkeypatch_config: Path) -> None:
        from scripts.utils.snapshot import list_snapshots

        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        set_current_snapshot(study, dest.name)
        assert list_snapshots(study) == [dest.name]  # current.json not counted


# ── C5.7 tampering re-hash ────────────────────────────────────────────────────


class TestTamperingDetection:
    def test_clean_snapshot_verifies(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        assert verify_snapshot_integrity(study, dest.name) is True

    def test_modified_content_detected(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        leaf = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        leaf.write_text(leaf.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
        with pytest.raises(SnapshotTamperedError):
            verify_snapshot_integrity(study, dest.name)

    def test_removed_file_detected(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        (dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl").unlink()
        with pytest.raises(SnapshotTamperedError):
            verify_snapshot_integrity(study, dest.name)

    def test_added_file_detected(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        (dest / "llm_source" / "dataset_schema" / "files" / "rogue.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
        with pytest.raises(SnapshotTamperedError):
            verify_snapshot_integrity(study, dest.name)

    def test_select_rehashes_and_refuses_tampered(self, monkeypatch_config: Path) -> None:
        from scripts.utils.snapshot import select_snapshot_llm_source

        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        leaf = dest / "llm_source" / "dataset_schema" / "files" / "1A_form.jsonl"
        leaf.write_text("{}\n", encoding="utf-8")
        with pytest.raises(SnapshotTamperedError):
            select_snapshot_llm_source(study, dest.name)


# ── C5.4 staleness (pure classifier) ─────────────────────────────────────────


def _manifest(**over) -> dict:
    base = {
        "phi_rulebook_version": 1,
        "phi_key_fingerprint": "key_aaa",
        "input_fingerprint_components": {
            "raw_datasets": "rd1",
            "forms_manifest": "fm1",
            "study_privacy": "sp1",
        },
    }
    base.update(over)
    return base


class TestStalenessClassifier:
    def test_no_findings_when_all_match(self) -> None:
        findings = check_snapshot_staleness(
            _manifest(),
            current_rulebook_version=1,
            current_key_fingerprint="key_aaa",
            current_input_components={
                "raw_datasets": "rd1",
                "forms_manifest": "fm1",
                "study_privacy": "sp1",
            },
        )
        assert findings == []

    def test_rulebook_update_warn(self) -> None:
        findings = check_snapshot_staleness(
            _manifest(),
            current_rulebook_version=2,
            current_key_fingerprint="key_aaa",
        )
        assert [f.trigger for f in findings] == ["rulebook_update"]
        assert findings[0].severity is StalenessSeverity.WARN

    def test_key_rotation_block(self) -> None:
        findings = check_snapshot_staleness(
            _manifest(),
            current_rulebook_version=1,
            current_key_fingerprint="key_bbb",
        )
        assert any(
            f.trigger == "key_rotation" and f.severity is StalenessSeverity.BLOCK for f in findings
        )

    def test_source_data_correction_warn(self) -> None:
        findings = check_snapshot_staleness(
            _manifest(),
            current_rulebook_version=1,
            current_key_fingerprint="key_aaa",
            current_input_components={
                "raw_datasets": "rd2",
                "forms_manifest": "fm1",
                "study_privacy": "sp1",
            },
        )
        assert [f.trigger for f in findings] == ["source_data_correction"]

    def test_config_change_warn(self) -> None:
        findings = check_snapshot_staleness(
            _manifest(),
            current_rulebook_version=1,
            current_key_fingerprint="key_aaa",
            current_input_components={
                "raw_datasets": "rd1",
                "forms_manifest": "fm2",
                "study_privacy": "sp1",
            },
        )
        assert [f.trigger for f in findings] == ["config_change"]

    def test_unknown_values_skip_comparison(self) -> None:
        # A legacy v1 manifest (no provenance) yields no spurious staleness.
        findings = check_snapshot_staleness(
            {"phi_rulebook_version": None, "phi_key_fingerprint": None},
            current_rulebook_version=None,
            current_key_fingerprint=None,
        )
        assert findings == []


# ── C5.5 redundant-run fingerprint lookup ─────────────────────────────────────


def _seed_fingerprint_record(study: str, fingerprint: str, components: dict) -> None:
    from scripts.utils.input_fingerprint import (
        InputFingerprint,
        fingerprint_record_path,
        write_fingerprint_record,
    )

    audit_dir = Path(config.OUTPUT_DIR) / study / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    fp = InputFingerprint(fingerprint=fingerprint, components=components, study=study)
    write_fingerprint_record(fingerprint_record_path(audit_dir), fp)


class TestFingerprintLookup:
    def test_finds_matching_clean_snapshot(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_fingerprint_record(study, "fp_match", {"raw_datasets": "x"})
        dest = _make_snapshot(study, created_utc="2026-06-15T14:30:00Z")
        # The snapshot recorded the seeded fingerprint.
        assert load_snapshot(study, dest.name)["input_fingerprint"] == "fp_match"
        assert find_snapshot_by_fingerprint(study, "fp_match") == dest.name

    def test_no_match_returns_none(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_fingerprint_record(study, "fp_a", {})
        _make_snapshot(study)
        assert find_snapshot_by_fingerprint(study, "fp_other") is None

    def test_unverified_snapshot_not_matched(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_fingerprint_record(study, "fp_x", {})
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID, verifier_passed=False)
        write_snapshot(study, RUN_ID, created_utc="2026-06-15T14:30:00Z")
        assert find_snapshot_by_fingerprint(study, "fp_x") is None

    def test_latest_snapshot_is_most_recent(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        first = write_snapshot(study, RUN_ID, created_utc="2026-06-15T14:30:00Z").name
        second = write_snapshot(study, RUN_ID, created_utc="2026-06-15T15:30:00Z").name
        assert latest_snapshot(study) == second
        assert first != second


# ── C5.6 snapshot diff ────────────────────────────────────────────────────────


class TestSnapshotDiff:
    def test_diff_forms_and_variables(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        # Snapshot A: forms 1A_form + 2B_form.
        _seed_llm_source(_live_llm_source(study), marker="alpha", forms=("1A_form", "2B_form"))
        _seed_run_artifacts(study, RUN_ID)
        old = write_snapshot(study, RUN_ID, created_utc="2026-06-15T14:30:00Z").name

        # Snapshot B: 2B_form removed, 3C_form added, 1A_form content changed
        # AND gains a column.
        live = _live_llm_source(study)
        ds = live / "dataset_schema" / "files"
        (ds / "2B_form.jsonl").unlink()
        (ds / "3C_form.jsonl").write_text(
            json.dumps({"SUBJID": "RID_X_aaaaaaaaaaaa", "RESULT": "z"}) + "\n", encoding="utf-8"
        )
        (ds / "1A_form.jsonl").write_text(
            json.dumps({"SUBJID": "RID_X_aaaaaaaaaaaa", "RESULT": "beta", "NEWCOL": "1"}) + "\n",
            encoding="utf-8",
        )
        new = write_snapshot(study, RUN_ID, created_utc="2026-06-15T15:30:00Z").name

        diff = diff_snapshots(study, old, new)
        assert diff["forms_added"] == ["3C_form"]
        assert diff["forms_removed"] == ["2B_form"]
        assert "dataset_schema/files/1A_form.jsonl" in diff["files_changed"]
        assert diff["variables_changed"]["1A_form"]["added"] == ["NEWCOL"]
        assert diff["variables_changed"]["1A_form"]["removed"] == []

    def test_write_diff_to_audit_zone(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        old = write_snapshot(study, RUN_ID, created_utc="2026-06-15T14:30:00Z").name
        _seed_llm_source(_live_llm_source(study), marker="beta")
        new = write_snapshot(study, RUN_ID, created_utc="2026-06-15T15:30:00Z").name

        diff = diff_snapshots(study, old, new)
        path = write_snapshot_diff(study, old, new, diff)
        assert path == snapshot_diff_path(study, old, new)
        assert path.is_file()
        assert "audit" in path.parts and "snapshot_diffs" in path.parts
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["old_snapshot_id"] == old
        assert written["new_snapshot_id"] == new

    def test_diff_emitted_on_commit(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _seed_llm_source(_live_llm_source(study))
        _seed_run_artifacts(study, RUN_ID)
        old = write_snapshot(study, RUN_ID, created_utc="2026-06-15T14:30:00Z").name
        _seed_llm_source(_live_llm_source(study), marker="beta")
        new = write_snapshot(study, RUN_ID, created_utc="2026-06-15T15:30:00Z").name
        # The commit auto-wrote a diff against the prior snapshot.
        assert snapshot_diff_path(study, old, new).is_file()
