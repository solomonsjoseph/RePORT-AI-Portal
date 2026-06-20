"""N14 tests — data_as_of in the snapshot manifest + Type-2 human-review records."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

import config
from scripts.utils.snapshot import commit_run_snapshot, load_snapshot, write_snapshot

RUN_ID = "run_n14fixedid00001"

_PRIVACY = {
    "jurisdictions": ["USA"],
    "data_as_of": "2024-12-31",
    "rule_refresh": "pinned_only",
    "conflict_policy": "strictest_wins",
    "approval": {"mode": "hybrid", "max_synthetic_attempts": 5},
}


def _seed_llm_source(study: str) -> None:
    root = Path(config.OUTPUT_DIR) / study / "llm_source"
    ds = root / "dataset_schema" / "files"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "1A_form.jsonl").write_text(
        json.dumps({"SUBJID": "RID_X_aaaaaaaaaaaa", "RESULT": "x"}) + "\n", encoding="utf-8"
    )


def _seed_run(study: str, run_id: str) -> Path:
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
        json.dumps({"run_id": run_id, "overall": "pass", "exit_code": 0, "assertions": []}),
        encoding="utf-8",
    )
    return run_dir


def _write_privacy(study: str) -> None:
    config.STUDY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (config.STUDY_CONFIG_DIR / "_study_privacy.yaml").write_text(
        yaml.safe_dump(_PRIVACY), encoding="utf-8"
    )


def test_data_as_of_copied_into_manifest(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _seed_llm_source(study)
    _seed_run(study, RUN_ID)
    _write_privacy(study)

    dest = write_snapshot(study, RUN_ID)
    manifest = load_snapshot(study, dest.name)
    assert manifest["data_as_of"] == "2024-12-31"


def test_data_as_of_absent_is_none(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _seed_llm_source(study)
    _seed_run(study, RUN_ID)
    # no _study_privacy.yaml → fail-soft None (never raises)
    dest = write_snapshot(study, RUN_ID)
    manifest = load_snapshot(study, dest.name)
    assert manifest["data_as_of"] is None


def test_rejected_forms_and_cleanup_report_in_manifest(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _seed_llm_source(study)
    _seed_run(study, RUN_ID)
    config.STUDY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (config.STUDY_CONFIG_DIR / "_forms_manifest.yaml").write_text(
        yaml.safe_dump({"required": ["1A_form.xlsx"], "reject": ["Paste Errors.xlsx"]}),
        encoding="utf-8",
    )
    audit = Path(config.OUTPUT_DIR) / study / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "cleanup_verification_report.json").write_text(
        json.dumps({"passed": True}), encoding="utf-8"
    )

    dest = write_snapshot(study, RUN_ID)
    manifest = load_snapshot(study, dest.name)
    assert manifest["rejected_forms"] == ["Paste Errors.xlsx"]
    assert manifest["cleanup_verification_report_sha256"]  # captured (non-null)
    assert (dest / "cleanup_verification_report.json").is_file()


def test_snapshot_captures_phi_scrub_override(monkeypatch_config: Path) -> None:
    # N11: the per-study phi_scrub.yaml override (compliance_posture etc.) must be
    # captured in the snapshot for reproducibility.
    study = config.STUDY_NAME
    _seed_llm_source(study)
    _seed_run(study, RUN_ID)
    config.STUDY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (config.STUDY_CONFIG_DIR / "phi_scrub.yaml").write_text(
        yaml.safe_dump({"compliance_posture": "limited_dataset"}), encoding="utf-8"
    )

    dest = write_snapshot(study, RUN_ID)
    manifest = load_snapshot(study, dest.name)
    assert manifest["config_files"]["phi_scrub.yaml"]  # captured (non-null sha)
    assert (dest / "phi_scrub.yaml").is_file()


def test_type2_resume_synthesizes_review_record(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _seed_llm_source(study)
    run_dir = _seed_run(study, RUN_ID)

    snap_id = commit_run_snapshot(study=study, run_id=RUN_ID, run_dir=run_dir, resume_held=True)
    assert snap_id is not None
    manifest = load_snapshot(study, snap_id)
    assert manifest["snapshot_type"] == 2
    records = manifest["human_review_records"]
    assert records and records[0]["type"] == "resume_held_republish"


def test_type1_clean_run_has_no_review_records(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _seed_llm_source(study)
    run_dir = _seed_run(study, RUN_ID)

    snap_id = commit_run_snapshot(study=study, run_id=RUN_ID, run_dir=run_dir, resume_held=False)
    assert snap_id is not None
    manifest = load_snapshot(study, snap_id)
    assert manifest["snapshot_type"] == 1
    assert manifest["human_review_records"] == []


def test_explicit_review_records_file_is_used(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _seed_llm_source(study)
    run_dir = _seed_run(study, RUN_ID)
    (run_dir / "human_review_records.json").write_text(
        json.dumps(
            [{"type": "dedup_resolution", "form": "9_form.xlsx", "decision": "kept_superset"}]
        ),
        encoding="utf-8",
    )

    snap_id = commit_run_snapshot(study=study, run_id=RUN_ID, run_dir=run_dir, resume_held=True)
    assert snap_id is not None
    manifest = load_snapshot(study, snap_id)
    assert any(r.get("type") == "dedup_resolution" for r in manifest["human_review_records"])
