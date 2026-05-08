"""Phase 3 config constants must export cross-verify paths and threshold."""

from __future__ import annotations

from pathlib import Path

import config


def test_study_audit_dir() -> None:
    p = config.STUDY_AUDIT_DIR
    assert isinstance(p, Path)
    assert p.name == "audit"
    # parent is the per-study output root (whatever the project calls it)
    assert p.parent.name == config.STUDY_NAME or p.parent == config.STUDY_OUTPUT_DIR


def test_phi_id_mapping_path() -> None:
    p = config.PHI_ID_MAPPING_PATH
    assert p.name == "phi_id_mapping.json"
    assert p.parent == config.STUDY_AUDIT_DIR


def test_cross_verify_repeat_ledger_path() -> None:
    p = config.CROSS_VERIFY_REPEAT_LEDGER_PATH
    assert p.name == "cross_verify_repeat_ledger.json"
    assert p.parent == config.STUDY_AUDIT_DIR


def test_cross_verify_safe_report_path() -> None:
    p = config.CROSS_VERIFY_SAFE_REPORT_PATH
    assert p.name == "cross_verify_safe_report.json"
    assert p.parent == config.TMP_DIR


def test_cross_verify_agent_workdir() -> None:
    p = config.CROSS_VERIFY_AGENT_WORKDIR
    assert p.name == "cross_verify_agent_workdir"
    assert p.parent == config.TMP_DIR


def test_cross_verify_drafts_dirs() -> None:
    assert config.CROSS_VERIFY_PR_DRAFTS_DIR.name == "cross_verify_pr_drafts"
    assert config.CROSS_VERIFY_HITL_DRAFTS_DIR.name == "cross_verify_hitl_drafts"
    assert config.CROSS_VERIFY_PR_DRAFTS_DIR.parent == config.TMP_DIR


def test_cross_verify_repeat_threshold() -> None:
    assert config.CROSS_VERIFY_REPEAT_THRESHOLD == 2
