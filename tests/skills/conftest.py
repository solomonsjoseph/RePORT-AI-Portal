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

SKILLS_TEST_STUDY = "Test-Study"


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
    """
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
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
