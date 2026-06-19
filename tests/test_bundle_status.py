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


def _seed_joined_sot(form: str = "6_HIV") -> None:
    joined_dir = config.LLM_SOURCE_SOT_DIR / form / "joined"
    joined_dir.mkdir(parents=True, exist_ok=True)
    (joined_dir / f"{form}_joined_query_view.yaml").write_text(
        f"study: Study\nform: {form}\nvariables: {{}}\n",
        encoding="utf-8",
    )


def test_published_bundle_requires_dataset_jsonl_and_joined_sot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    datasets = config.TRIO_DATASETS_DIR
    datasets.mkdir(parents=True)
    (datasets / "6_HIV.jsonl").write_text('{"_metadata": true}\n', encoding="utf-8")
    _seed_joined_sot()

    assert published_bundle_exists() is True


def test_published_bundle_requires_dictionary_mapping_when_raw_dictionary_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    config.DATA_DICTIONARY_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    _seed_joined_sot()
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
    config.DATA_DICTIONARY_DIR.mkdir(parents=True)
    config.DICTIONARY_JSON_OUTPUT_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    _seed_joined_sot()
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


def test_published_bundle_rejects_policy_yaml_without_joined_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Policy YAML alone does not satisfy bundle readiness (Note 3)."""
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    policy_dir = config.LLM_SOURCE_SOT_DIR / "6_HIV" / "pdf"
    policy_dir.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    (policy_dir / "6_HIV_policy.yaml").write_text("variables: {}\n", encoding="utf-8")

    assert published_bundle_exists() is False
    assert any("joined query views" in issue for issue in bundle_readiness_issues())


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


# ── #5/#9: chronological (completed_utc) + terminal-only run selection ─────────


def test_held_set_notice_picks_latest_by_completed_utc_not_name(
    monkeypatch_config: Path,
) -> None:
    """The lexicographically-LARGER run name has the EARLIER completed_utc.

    Name-sort (the old buggy behaviour) would pick ``run_zzz...`` (clean) and
    return None. Chronological selection by completed_utc must instead pick the
    NEWER ``run_aaa...`` (held) and surface its notice.
    """
    study = config.STUDY_NAME
    # Name 'zzz' sorts last/largest but is chronologically OLDER (clean run).
    _write_run_status(
        study,
        "run_zzz000000001",
        {
            "publish_status": "complete",
            "held_forms": [],
            "exit_code": 0,
            "completed_utc": "2026-06-01T00:00:00+00:00",
        },
    )
    # Name 'aaa' sorts first/smallest but is chronologically NEWER (held run).
    _write_run_status(
        study,
        "run_aaa000000002",
        {
            "publish_status": "partial",
            "held_forms": ["NEW_HELD.xlsx"],
            "exit_code": 8,
            "completed_utc": "2026-06-05T00:00:00+00:00",
        },
    )
    notice = held_set_notice(study)
    assert notice is not None
    assert "NEW_HELD.xlsx" in notice


def test_held_set_notice_ignores_non_terminal_run(monkeypatch_config: Path) -> None:
    """A non-terminal run (exit_code 6 needs-advice) must be ignored even if it
    is the chronologically newest and reports held forms."""
    study = config.STUDY_NAME
    # Terminal partial run, older.
    _write_run_status(
        study,
        "run_terminal00001",
        {
            "publish_status": "partial",
            "held_forms": ["OLD_HELD.xlsx"],
            "exit_code": 8,
            "completed_utc": "2026-06-01T00:00:00+00:00",
        },
    )
    # Non-terminal (paused/needs-advice) run, newer — must NOT be selected.
    _write_run_status(
        study,
        "run_nonterminal02",
        {
            "publish_status": "held",
            "held_forms": ["INFLIGHT.xlsx"],
            "exit_code": 6,
            "completed_utc": "2026-06-05T00:00:00+00:00",
        },
    )
    notice = held_set_notice(study)
    assert notice is not None
    # The terminal run is used; the newer non-terminal run is ignored.
    assert "OLD_HELD.xlsx" in notice
    assert "INFLIGHT.xlsx" not in notice


def test_held_set_notice_none_when_only_non_terminal_runs(monkeypatch_config: Path) -> None:
    """If every run is non-terminal, there is no published outcome to report."""
    study = config.STUDY_NAME
    _write_run_status(
        study,
        "run_paused0000001",
        {
            "publish_status": "held",
            "held_forms": ["INFLIGHT.xlsx"],
            "exit_code": 6,
            "completed_utc": "2026-06-05T00:00:00+00:00",
        },
    )
    assert held_set_notice(study) is None
