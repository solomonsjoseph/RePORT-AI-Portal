"""Tests for the Note 9 run-scoped AI-aligned scrub overlay.

The overlay is written with COMPLETE lists and merges LAST, so it both takes
effect (run_scrub applies the aligned regex) and is covered by the config hash.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import config
from scripts.security.phi_scrub import (
    effective_scrub_config_hash,
    load_scrub_config,
    write_generated_scrub_overlay,
)

_BASE = {
    "compliance_posture": "safe_harbor",
    "subject_id_fields": ["SUBJID"],
    "date_fields": [r"\bvisit_date\b"],
    "drop_fields": [r"\bemail\b"],
    "id_fields": [{"pattern": "^SUBJID$", "label": "SUBJ"}],
}


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "phi_scrub.yaml").write_text(yaml.safe_dump(_BASE), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DEFAULTS_DIR", defaults)
    monkeypatch.setattr(config, "PHI_SCRUB_CONFIG_FILENAME", "phi_scrub.yaml")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")  # no per-study override
    out = tmp_path / "out"
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", out)
    return out


def test_overlay_extends_patterns_and_preserves_base(tmp_path, monkeypatch):
    out = _setup(tmp_path, monkeypatch)
    run_dir = out / "runs" / "run_t"
    monkeypatch.setenv("REPORTAL_RUN_ID", "run_t")

    aligned = [
        {
            "action": "jitter_date",
            "regex_pattern": "^fec_nac$",
            "inferred_variable_type": "birth_date",
        },
        {
            "action": "pseudonymize",
            "regex_pattern": "^enrol_code$",
            "inferred_variable_type": "study_id",
        },
    ]
    overlay_path = write_generated_scrub_overlay(aligned, run_dir=run_dir, study="X")
    assert overlay_path is not None and overlay_path.is_file()

    cfg = load_scrub_config(study="X")
    assert cfg is not None
    # aligned patterns now take effect
    assert cfg.field_is_date("fec_nac")
    assert cfg.field_is_id("enrol_code")
    # base patterns preserved (complete-list merge, not replace-wipe)
    assert cfg.field_is_date("visit_date")
    assert cfg.field_is_drop("email")
    assert cfg.id_label_for("SUBJID") == "SUBJ"


def test_overlay_changes_effective_hash(tmp_path, monkeypatch):
    out = _setup(tmp_path, monkeypatch)
    run_dir = out / "runs" / "run_t"

    # No overlay yet (REPORTAL_RUN_ID unset) → baseline hash.
    monkeypatch.delenv("REPORTAL_RUN_ID", raising=False)
    base_hash = effective_scrub_config_hash(study="X")

    monkeypatch.setenv("REPORTAL_RUN_ID", "run_t")
    write_generated_scrub_overlay(
        [{"action": "drop", "regex_pattern": "^national_id$", "inferred_variable_type": "gov_id"}],
        run_dir=run_dir,
        study="X",
    )
    overlay_hash = effective_scrub_config_hash(study="X")
    assert overlay_hash != base_hash  # the overlay is folded into the hash


def test_no_aligned_rules_writes_nothing(tmp_path, monkeypatch):
    out = _setup(tmp_path, monkeypatch)
    run_dir = out / "runs" / "run_t"
    assert write_generated_scrub_overlay([], run_dir=run_dir, study="X") is None
