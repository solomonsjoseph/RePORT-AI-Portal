"""Shared fixtures and helpers for the tests/skills package.

Plain helper functions are importable by test files directly:

    from tests.skills.conftest import (
        patch_config,
        write_valid_ledger,
        make_staging,
        make_datasets_dir,
        fake_destroy,
    )

All helpers are deterministic, side-effect-free outside tmp_path, and use
no real PHI or dataset values.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import config
from scripts.audit.ledger import dataset_phi_ledger_path

# ---------------------------------------------------------------------------
# Shared study name used by both CLI test files
# ---------------------------------------------------------------------------

SKILLS_TEST_STUDY = "Indo-VAP"


# ---------------------------------------------------------------------------
# Consolidated helper functions (plain functions, not fixtures)
#
# Both test_scrub_outcome_partial.py and test_extract_to_llm_source_cli.py
# previously had local copies of these helpers.  The copy in
# test_scrub_outcome_partial.py patched RAW_DATA_DIR; the copy in
# test_extract_to_llm_source_cli.py did not — a latent divergence now closed
# by always patching RAW_DATA_DIR here.
# ---------------------------------------------------------------------------


def patch_config(monkeypatch: Any, tmp_path: Path, study: str = SKILLS_TEST_STUDY) -> None:
    """Redirect config path constants to tmp_path-based locations.

    Patches OUTPUT_DIR, TMP_DIR, DATASETS_DIR, and RAW_DATA_DIR so every
    file operation in the skill under test is hermetically isolated inside
    tmp_path.  RAW_DATA_DIR was absent from the extract_to_llm_source_cli
    copy — this consolidated version ensures both files receive the patch.

    Also patches the Note 13 workspace-cleanup must-remain paths so standalone
    inline snapshot commits can run the real verifier in tests.
    """
    study_output = tmp_path / "output" / study
    llm_source = study_output / "llm_source"
    audit = study_output / "audit"
    snapshots = study_output / "snapshots"
    study_config = tmp_path / "data" / "raw" / study
    study_data = tmp_path / "data" / "raw" / study
    staging_root = tmp_path / "tmp" / study

    for d in (llm_source, audit, snapshots, study_config, study_data):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(config, "STUDY_NAME", study, raising=False)
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", study_output, raising=False)
    monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", llm_source, raising=False)
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", audit, raising=False)
    monkeypatch.setattr(config, "STUDY_SNAPSHOTS_OUTPUT_DIR", snapshots, raising=False)
    monkeypatch.setattr(config, "STUDY_CONFIG_DIR", study_config, raising=False)
    monkeypatch.setattr(config, "STUDY_DATA_DIR", study_data, raising=False)
    monkeypatch.setattr(
        config,
        "TRIO_DATASETS_DIR",
        llm_source / "dataset_schema" / "files",
        raising=False,
    )
    monkeypatch.setattr(config, "STUDY_STAGING_DIR", staging_root, raising=False)
    monkeypatch.setattr(config, "STAGING_DATASETS_DIR", staging_root / "datasets", raising=False)
    monkeypatch.setattr(config, "STAGING_SOT_DIR", staging_root / "SoT", raising=False)
    monkeypatch.setattr(config, "STAGING_HEADERS_DIR", staging_root / "headers", raising=False)
    monkeypatch.setattr(
        config,
        "DATASETS_DIR",
        tmp_path / f"data/raw/{study}/datasets",
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "RAW_DATA_DIR",
        tmp_path / "data" / "raw",
        raising=False,
    )
    # Study config now lives under config/<study>/ (Note 11). Point CONFIG_DIR at
    # the same tmp/data/raw root so study_config_path("_forms_manifest.yaml",
    # study=<study>) resolves to tmp/data/raw/<study>/_forms_manifest.yaml —
    # where the skills tests already write their manifests.
    monkeypatch.setattr(
        config,
        "CONFIG_DIR",
        tmp_path / "data" / "raw",
        raising=False,
    )


def write_valid_ledger(
    output_dir: Path,
    study: str = SKILLS_TEST_STUDY,
    run_id: str = "run_x",
) -> None:
    """Write a per-dataset PHI ledger with non-null hashes.

    The run_id defaults to ``"run_x"`` to match the original
    test_extract_to_llm_source_cli.py copy.  Callers that pin a specific
    run_id (e.g. test_scrub_outcome_partial.py) can pass it explicitly.
    """
    audit_dir = output_dir / study / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    ledger = {
        "run_id": run_id,
        "scrub_config_hash": "abc123",
        "input_dataset_hash": "def456",
    }
    ledger_path = dataset_phi_ledger_path(audit_dir, "approved.xlsx")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")


def make_staging(staging_dir: Path) -> None:
    """Create a non-empty staging directory with a dummy JSONL file."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "dummy.jsonl").write_bytes(b"data")


def make_datasets_dir(datasets_dir: Path) -> None:
    """Create the datasets directory (no files — manifest absent → no raise)."""
    datasets_dir.mkdir(parents=True, exist_ok=True)


def fake_destroy(**kwargs: Any) -> Path:
    """Mock destroy_staging_and_attest: remove staging + write stub attestation.

    Accepts keyword arguments matching the real ``destroy_staging_and_attest``
    signature (``staging_dir``, ``output_dir``, ``run_id``).
    """
    shutil.rmtree(str(kwargs["staging_dir"]), ignore_errors=True)
    attest_path = kwargs["output_dir"] / "runs" / kwargs["run_id"] / "destruction_attestation.json"
    attest_path.parent.mkdir(parents=True, exist_ok=True)
    attest_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
    return attest_path
