"""Phase 1 config constants must be exported and resolve under TMP_DIR."""

from __future__ import annotations

from pathlib import Path

import config


def test_phi_techniques_inventory_path() -> None:
    p = config.PHI_TECHNIQUES_INVENTORY_PATH
    assert isinstance(p, Path)
    assert p.name == "2026-05-08-phi-techniques-inventory.md"
    assert p.parent.name == "specs"


def test_phi_coverage_matrix_path() -> None:
    p = config.PHI_COVERAGE_MATRIX_PATH
    assert isinstance(p, Path)
    assert p.name == "2026-05-08-phi-coverage-matrix.md"


def test_phi_sweep_findings_path() -> None:
    p = config.PHI_SWEEP_FINDINGS_PATH
    assert isinstance(p, Path)
    assert p.name == "phi_sweep_findings.json"
    assert p.parent == config.TMP_DIR


def test_phi_sweep_hitl_drafts_dir() -> None:
    p = config.PHI_SWEEP_HITL_DRAFTS_DIR
    assert isinstance(p, Path)
    assert p.name == "phi_sweep_hitl_drafts"
    assert p.parent == config.TMP_DIR


def test_phi_sweep_pr_drafts_dir() -> None:
    p = config.PHI_SWEEP_PR_DRAFTS_DIR
    assert isinstance(p, Path)
    assert p.name == "phi_sweep_pr_drafts"
    assert p.parent == config.TMP_DIR
