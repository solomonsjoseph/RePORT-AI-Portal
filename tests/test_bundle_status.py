"""Tests for chat UI published-bundle readiness detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.ai_assistant.ui.bundle_status import (
    bundle_readiness_issues,
    held_set_notice,
    published_bundle_exists,
)


def _point_bundle_config(monkeypatch: pytest.MonkeyPatch, llm_source: Path) -> None:
    raw_root = llm_source.parents[2] / "data" / "raw" / "Study"
    monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", llm_source)
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", llm_source / "dataset_schema" / "files")
    monkeypatch.setattr(
        config,
        "DICTIONARY_JSON_OUTPUT_DIR",
        llm_source / "dictionary_mapping" / "jsonl",
    )
    monkeypatch.setattr(config, "DATA_DICTIONARY_DIR", raw_root / "data_dictionary")
    monkeypatch.setattr(config, "LLM_SOURCE_SOT_DIR", llm_source / "SoT")
    monkeypatch.setattr(
        config,
        "LLM_SOURCE_LEGACY_SOURCE_TRUTH_DIR",
        llm_source / "source_truth",
    )


def test_published_bundle_requires_dataset_jsonl_and_plugin_sot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    datasets = config.TRIO_DATASETS_DIR
    policy_dir = config.LLM_SOURCE_SOT_DIR / "6_HIV" / "pdf"
    datasets.mkdir(parents=True)
    policy_dir.mkdir(parents=True)
    (datasets / "6_HIV.jsonl").write_text('{"_metadata": true}\n', encoding="utf-8")
    (policy_dir / "6_HIV_policy.yaml").write_text("variables: {}\n", encoding="utf-8")

    assert published_bundle_exists() is True


def test_published_bundle_requires_dictionary_mapping_when_raw_dictionary_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    policy_dir = config.LLM_SOURCE_SOT_DIR / "6_HIV" / "pdf"
    policy_dir.mkdir(parents=True)
    config.DATA_DICTIONARY_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    (policy_dir / "6_HIV_policy.yaml").write_text("variables: {}\n", encoding="utf-8")
    (config.DATA_DICTIONARY_DIR / "dictionary.csv").write_text(
        "variable,label\nHIV_HIV,HIV result\n",
        encoding="utf-8",
    )

    assert published_bundle_exists() is False
    assert any("dictionary mapping JSONL" in issue for issue in bundle_readiness_issues())


def test_published_bundle_accepts_dictionary_mapping_when_raw_dictionary_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    policy_dir = config.LLM_SOURCE_SOT_DIR / "6_HIV" / "pdf"
    policy_dir.mkdir(parents=True)
    config.DATA_DICTIONARY_DIR.mkdir(parents=True)
    config.DICTIONARY_JSON_OUTPUT_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    (policy_dir / "6_HIV_policy.yaml").write_text("variables: {}\n", encoding="utf-8")
    (config.DATA_DICTIONARY_DIR / "dictionary.csv").write_text(
        "variable,label\nHIV_HIV,HIV result\n",
        encoding="utf-8",
    )
    (config.DICTIONARY_JSON_OUTPUT_DIR / "dictionary.jsonl").write_text(
        '{"variable": "HIV_HIV"}\n',
        encoding="utf-8",
    )

    assert published_bundle_exists() is True


def test_published_bundle_rejects_dataset_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )

    assert published_bundle_exists() is False


def test_published_bundle_accepts_legacy_source_truth_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    legacy_dir = config.LLM_SOURCE_LEGACY_SOURCE_TRUTH_DIR
    legacy_dir.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    (legacy_dir / "6_HIV_policy.lean.yaml").write_text("variables: {}\n", encoding="utf-8")

    assert published_bundle_exists() is True


# ── held_set_notice (W2): advisory, non-blocking, never raises ───────────────


def _write_run_status(
    study: str,
    run_id: str,
    status: dict,
    *,
    approval: dict | None = None,
) -> Path:
    """Seed a run dir with status.json (+ optional approval) under OUTPUT_DIR."""
    run_dir = Path(config.OUTPUT_DIR) / study / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if approval is not None:
        (run_dir / "phi_handling_approval.json").write_text(json.dumps(approval), encoding="utf-8")
    return run_dir


def test_held_set_notice_none_when_no_runs(monkeypatch_config: Path) -> None:
    # No runs/ dir exists at all -> advisory returns None, never raises.
    assert held_set_notice(config.STUDY_NAME) is None


def test_held_set_notice_none_for_clean_run(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _write_run_status(
        study,
        "run_clean00000000",
        {"publish_status": "complete", "held_forms": [], "exit_code": 0},
    )
    assert held_set_notice(study) is None


def test_held_set_notice_text_when_forms_held(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _write_run_status(
        study,
        "run_held000000001",
        {
            "publish_status": "partial",
            "held_forms": ["9Z_held.xlsx", "8Y_other.xlsx"],
            "exit_code": 8,
        },
    )
    notice = held_set_notice(study)
    assert notice is not None
    assert "9Z_held.xlsx" in notice
    assert "8Y_other.xlsx" in notice
    # Advisory framing: querying approved data may continue.
    assert "approved" in notice.lower()


def test_held_set_notice_includes_reasons_from_approval(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    _write_run_status(
        study,
        "run_held000000002",
        {"publish_status": "partial", "held_forms": ["9Z_held.xlsx"], "exit_code": 8},
        approval={
            "approved_forms": ["1A_form.xlsx"],
            "held_forms": ["9Z_held.xlsx"],
            "forms": [
                {
                    "form_name": "9Z_held.xlsx",
                    "status": "held",
                    "reasons": ["phi_coverage_hold: column PATIENT_NAME looks like a name"],
                }
            ],
        },
    )
    notice = held_set_notice(study)
    assert notice is not None
    assert "phi_coverage_hold" in notice
    assert "PATIENT_NAME" in notice


def test_held_set_notice_uses_latest_run(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    # Older run held a form; the newest (name-sorted) run is clean.
    _write_run_status(
        study,
        "run_aaa000000001",
        {"publish_status": "partial", "held_forms": ["OLD.xlsx"], "exit_code": 8},
    )
    _write_run_status(
        study,
        "run_zzz000000002",
        {"publish_status": "complete", "held_forms": [], "exit_code": 0},
    )
    assert held_set_notice(study) is None


def test_held_set_notice_never_raises_on_malformed_status(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    run_dir = Path(config.OUTPUT_DIR) / study / "runs" / "run_bad000000001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text("{ this is not json", encoding="utf-8")
    # Malformed JSON -> skipped -> no parseable status -> None (no exception).
    assert held_set_notice(study) is None


def test_held_set_notice_never_raises_on_missing_status_file(monkeypatch_config: Path) -> None:
    study = config.STUDY_NAME
    # A run dir with no status.json at all.
    (Path(config.OUTPUT_DIR) / study / "runs" / "run_empty00000001").mkdir(
        parents=True, exist_ok=True
    )
    assert held_set_notice(study) is None
