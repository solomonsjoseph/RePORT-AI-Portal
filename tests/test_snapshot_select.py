"""Tests for the Load Study UI snapshot selector (W2).

Covered:

* ``available_snapshots`` lists written snapshots with advisory metadata.
* ``activate_snapshot`` re-runs the PHI residual gate and repoints the
  assistant read zone at the selected ``llm_source/`` ONLY — the snapshot root,
  approval, manifest, and verifier report stay DENIED.
* an unknown / path-bearing snapshot id is rejected (fail-closed); the read zone
  is left untouched.
* a snapshot whose ``llm_source/`` carries a PHI residual is refused by the gate.

All filesystem state is under ``monkeypatch_config``'s tmp_path; no real study
data is touched. Seeds a snapshot via ``snapshot.write_snapshot``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.ai_assistant.file_access import ZoneViolationError, validate_agent_read
from scripts.ai_assistant.ui.snapshot_select import (
    SnapshotActivationError,
    activate_snapshot,
    available_snapshots,
)
from scripts.utils import snapshot

RUN_ID = "run_selecttestid001"


# ── helpers ──────────────────────────────────────────────────────────────────


def _seed_llm_source(root: Path, *, marker: str = "alpha") -> None:
    """Populate the (config-patched) llm_source tree with PHI-clean files."""
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


def _make_snapshot(
    study: str,
    *,
    marker: str = "alpha",
    approved: list[str] | None = None,
    held: list[str] | None = None,
    verifier_passed: bool = True,
) -> Path:
    _seed_llm_source(config.STUDY_LLM_SOURCE_DIR, marker=marker)
    _seed_run_artifacts(
        study, RUN_ID, approved=approved, held=held, verifier_passed=verifier_passed
    )
    return snapshot.write_snapshot(study, RUN_ID)


# ── available_snapshots ──────────────────────────────────────────────────────


class TestAvailableSnapshots:
    def test_empty_when_no_snapshots(self, monkeypatch_config: Path) -> None:
        assert available_snapshots(config.STUDY_NAME) == []

    def test_lists_written_snapshot(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(
            study, approved=["1A_form.xlsx"], held=["9Z_held.xlsx"], verifier_passed=True
        )
        entries = available_snapshots(study)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"] == dest.name
        assert entry["source_run_id"] == RUN_ID
        assert entry["approved_count"] == 1
        assert entry["held_count"] == 1
        assert entry["verifier_passed"] is True

    def test_skips_corrupt_manifest(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        # Corrupt the manifest -> load_snapshot raises -> snapshot omitted.
        (dest / snapshot.MANIFEST_FILENAME).write_text("{ not json", encoding="utf-8")
        assert available_snapshots(study) == []


# ── activate_snapshot ────────────────────────────────────────────────────────


class TestActivateSnapshot:
    def test_repoints_read_zone_to_llm_source(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)

        exposed = activate_snapshot(study, dest.name)

        # The returned path is the snapshot's llm_source subtree...
        assert exposed == snapshot.snapshot_llm_source_path(study, dest.name)
        # ...and config now points the assistant read zone at it.
        assert exposed == config.STUDY_LLM_SOURCE_DIR

        # A scrubbed leaf inside the selected subtree is now readable.
        leaf = exposed / "dataset_schema" / "files" / "1A_form.jsonl"
        assert validate_agent_read(leaf) == Path(leaf.resolve())

    def test_root_and_approval_stay_denied(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        activate_snapshot(study, dest.name)

        # Only llm_source/ is exposed; the root + sidecar metadata stay denied.
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest)
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest / "phi_handling_approval.json")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest / snapshot.MANIFEST_FILENAME)
        with pytest.raises(ZoneViolationError):
            validate_agent_read(dest / "verifier_report.json")

    def test_unknown_id_rejected(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        study = config.STUDY_NAME
        before = config.STUDY_LLM_SOURCE_DIR
        with pytest.raises(SnapshotActivationError):
            activate_snapshot(study, "snap_doesnotexist")
        # Fail-closed: the read zone is untouched.
        assert before == config.STUDY_LLM_SOURCE_DIR

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "x\x00y"])
    def test_path_bearing_id_rejected(
        self, monkeypatch_config: Path, bad: str
    ) -> None:
        study = config.STUDY_NAME
        before = config.STUDY_LLM_SOURCE_DIR
        with pytest.raises(SnapshotActivationError):
            activate_snapshot(study, bad)
        assert before == config.STUDY_LLM_SOURCE_DIR

    def test_empty_snapshot_id_rejected(self, monkeypatch_config: Path) -> None:
        with pytest.raises(SnapshotActivationError):
            activate_snapshot(config.STUDY_NAME, "")

    def test_phi_residual_refuses_activation(
        self, monkeypatch_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a snapshot's llm_source carries a PHI residual, the re-run leak
        gate must refuse to expose it — fail-closed, read zone untouched."""
        study = config.STUDY_NAME
        dest = _make_snapshot(study)
        before = config.STUDY_LLM_SOURCE_DIR

        # Inject a blocking PHI pattern (an email) into the snapshot's
        # llm_source after the fact, simulating a residual that slipped through.
        leaf = (
            snapshot.snapshot_llm_source_path(study, dest.name)
            / "dataset_schema"
            / "files"
            / "leak.jsonl"
        )
        leaf.write_text(
            json.dumps({"CONTACT": "patient@example.com"}) + "\n", encoding="utf-8"
        )

        with pytest.raises(SnapshotActivationError):
            activate_snapshot(study, dest.name)
        # Read zone NOT repointed because the gate failed.
        assert before == config.STUDY_LLM_SOURCE_DIR
