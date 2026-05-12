"""Config constants for Phases 0-4 must exist, resolve correctly, and pin invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

import config

PATH_CONSTANTS_BY_PHASE: dict[str, tuple[str, ...]] = {
    "phase0": (
        "SOT_DIR",
        "RAW_PDF_DIR",
        "PILOT_RESULTS_DIR",
        "SOT_GAP_DRAFTS_DIR",
        "SOT_GAP_COVERAGE_PATH",
        "SOT_GAP_REPORT_PATH",
        "SOT_EVIDENCE_PACK_DRAFTS_DIR",
    ),
    "phase1": (
        "PHI_TECHNIQUES_INVENTORY_PATH",
        "PHI_COVERAGE_MATRIX_PATH",
        "PHI_SWEEP_FINDINGS_PATH",
        "PHI_SWEEP_HITL_DRAFTS_DIR",
        "PHI_SWEEP_PR_DRAFTS_DIR",
    ),
    "phase2": (
        "LLM_SOURCE_DATASET_SCHEMA_FILES_DIR",
        "LLM_SOURCE_DATASET_SCHEMA_CATALOG_PATH",
        "LLM_SOURCE_DICTIONARY_CATALOG_PATH",
        "LLM_SOURCE_DICTIONARY_MAPPING_DIR",
        "LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR",
        "LLM_SOURCE_EVIDENCE_PACKS_DIR",
        "LLM_SOURCE_CONCEPT_DIR",
    ),
    "phase3": (
        "STUDY_AUDIT_DIR",
        "PHI_ID_MAPPING_PATH",
        "CROSS_VERIFY_REPEAT_LEDGER_PATH",
        "CROSS_VERIFY_SAFE_REPORT_PATH",
        "CROSS_VERIFY_AGENT_WORKDIR",
        "CROSS_VERIFY_PR_DRAFTS_DIR",
        "CROSS_VERIFY_HITL_DRAFTS_DIR",
    ),
    "phase4": ("AUDIT_SENTINEL_ALARM_PATH",),
}


@pytest.mark.parametrize(
    "name",
    [name for names in PATH_CONSTANTS_BY_PHASE.values() for name in names],
)
def test_path_constant_exists_and_is_path_like(name: str) -> None:
    value = getattr(config, name)
    assert isinstance(value, (str, Path)), f"{name} not a path-like"
    assert str(value), f"{name} is empty"


# ---------------------------------------------------------------------------
# Phase 0
# ---------------------------------------------------------------------------


def test_sot_dir_resolves_to_default_study() -> None:
    sot_dir = Path(str(config.SOT_DIR))
    assert sot_dir.name == "Indo-VAP"
    assert sot_dir.parent.name == "SoT"


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def test_phi_techniques_inventory_path() -> None:
    p = config.PHI_TECHNIQUES_INVENTORY_PATH
    assert p.name == "2026-05-08-phi-techniques-inventory.md"
    assert p.parent.name == "specs"


def test_phi_coverage_matrix_path() -> None:
    assert config.PHI_COVERAGE_MATRIX_PATH.name == "2026-05-08-phi-coverage-matrix.md"


def test_phi_sweep_findings_path() -> None:
    p = config.PHI_SWEEP_FINDINGS_PATH
    assert p.name == "phi_sweep_findings.json"
    assert p.parent == config.TMP_DIR


def test_phi_sweep_hitl_drafts_dir() -> None:
    p = config.PHI_SWEEP_HITL_DRAFTS_DIR
    assert p.name == "phi_sweep_hitl_drafts"
    assert p.parent == config.TMP_DIR


def test_phi_sweep_pr_drafts_dir() -> None:
    p = config.PHI_SWEEP_PR_DRAFTS_DIR
    assert p.name == "phi_sweep_pr_drafts"
    assert p.parent == config.TMP_DIR


# ---------------------------------------------------------------------------
# Phase 2
# ---------------------------------------------------------------------------


def test_dataset_schema_files_dir() -> None:
    p = config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR
    assert p.parent.name == "dataset_schema"
    assert p.parent.parent == config.STUDY_LLM_SOURCE_DIR


def test_dataset_schema_catalog_path() -> None:
    p = config.LLM_SOURCE_DATASET_SCHEMA_CATALOG_PATH
    assert p.name == "catalog.json"
    assert p.parent == config.STUDY_LLM_SOURCE_DIR / "dataset_schema"


def test_dictionary_catalog_path() -> None:
    p = config.LLM_SOURCE_DICTIONARY_CATALOG_PATH
    assert p.name == "catalog.json"
    assert p.parent == config.LLM_SOURCE_DICTIONARY_MAPPING_DIR


def test_llm_source_dictionary_mapping_dir() -> None:
    p = config.LLM_SOURCE_DICTIONARY_MAPPING_DIR
    assert p.name == "dictionary_mapping"
    assert p.parent == config.STUDY_LLM_SOURCE_DIR


def test_llm_source_dictionary_mapping_jsonl_dir() -> None:
    p = config.LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR
    assert p.name == "jsonl"
    assert p.parent == config.LLM_SOURCE_DICTIONARY_MAPPING_DIR


def test_evidence_packs_dir() -> None:
    p = config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    assert p.name == "evidence_packs"
    assert p.parent == config.LLM_SOURCE_STUDY_METADATA_DIR
    assert p.parent.parent == config.STUDY_LLM_SOURCE_DIR


def test_concept_dir() -> None:
    p = config.LLM_SOURCE_CONCEPT_DIR
    assert p.name == "concept"
    assert p.parent == config.STUDY_LLM_SOURCE_DIR


def test_size_threshold_constants() -> None:
    assert config.LEAN_CATALOG_DICTIONARY_MAX_BYTES == 20 * 1024
    assert config.LEAN_CATALOG_DATASET_SCHEMA_MAX_BYTES == 50 * 1024
    assert config.LEAN_CATALOG_STUDY_METADATA_MAX_BYTES == 200 * 1024


# ---------------------------------------------------------------------------
# Phase 3
# ---------------------------------------------------------------------------


def test_study_audit_dir() -> None:
    p = config.STUDY_AUDIT_DIR
    assert p.name == "audit"
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


# ---------------------------------------------------------------------------
# Phase 4
# ---------------------------------------------------------------------------


def test_audit_no_llm_sentinel_name() -> None:
    assert config.AUDIT_NO_LLM_SENTINEL_NAME == ".NO_LLM_ZONE"


def test_audit_sentinel_alarm_path() -> None:
    p = config.AUDIT_SENTINEL_ALARM_PATH
    assert p.name == "audit_sentinel_alarms.jsonl"
    assert p.parent == config.TMP_DIR


def test_audit_no_llm_zone_attribute_name() -> None:
    assert config.AUDIT_NO_LLM_ZONE_ATTRIBUTE == "report-ai-portal-no-llm"
