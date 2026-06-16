"""Tests for the pipeline orchestrator state machine (Wave 4 B3.7).

Exercises the orchestrator's pure state-machine helpers and the native Phase-0
preflight (config validation, input-fingerprint redundant-run check) without
launching the heavy publish subprocesses.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

import config

_ORCH_PATH = (
    Path(config.BASE_DIR)
    / "plugins"
    / "report-ai-study-pipeline"
    / "skills"
    / "report-ai-study-pipeline"
    / "scripts"
    / "run.py"
)


def _load_orchestrator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("orchestrator_run_under_test", _ORCH_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve cls.__module__ in sys.modules.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ORCH = _load_orchestrator()


def test_run_state_roundtrip_and_flush(tmp_path: Path) -> None:
    state = ORCH._RunState(study="S", run_id="run_x")
    state.path = tmp_path / "run_state.json"
    rec = state.phase("P0:preflight")
    rec.status, rec.exit_code = "complete", 0
    state.status = "complete"
    state.flush()

    data = json.loads((tmp_path / "run_state.json").read_text())
    assert data["schema"] == ORCH.RUN_STATE_SCHEMA
    assert data["study"] == "S"
    assert data["status"] == "complete"
    assert data["phases"][0]["phase"] == "P0:preflight"
    assert data["phases"][0]["exit_code"] == 0


def test_baton_env_sets_lock_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    env = ORCH._baton_env(run_id="run_y", study="S")
    assert env["REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT"] == "1"
    assert env["REPORTAL_PIPELINE_LOCK_PARENT_PID"] == str(os.getpid())
    assert env["REPORTAL_RUN_ID"] == "run_y"
    assert env["STUDY_NAME"] == "S"


def test_absorb_status_reads_held_and_snapshot(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps({"held_forms": ["2A", "14"], "snapshot_id": "snap_abc"}),
        encoding="utf-8",
    )
    state = ORCH._RunState(study="S", run_id="run_z")
    ORCH._absorb_status(state, tmp_path)
    assert state.held_forms == ["2A", "14"]
    assert state.snapshot_id == "snap_abc"


def _patch_config_for_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, study: str, inputs_present: bool
) -> Path:
    cfg_dir = tmp_path / "config" / study
    raw_datasets = tmp_path / "raw" / study / "datasets"
    audit = tmp_path / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    if inputs_present:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "_forms_manifest.yaml").write_text("required: []\n", encoding="utf-8")
        (cfg_dir / "_study_privacy.yaml").write_text("jurisdictions: [USA]\n", encoding="utf-8")
        raw_datasets.mkdir(parents=True, exist_ok=True)
        (raw_datasets / "form.xlsx").write_bytes(b"x")

    monkeypatch.setattr(
        config, "study_config_path", lambda fn, study=None: tmp_path / "config" / study / fn
    )
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", audit)
    monkeypatch.setattr(config, "DATASETS_DIR", raw_datasets)
    monkeypatch.setattr(config, "ensure_run_directories", lambda **_kwargs: None)
    return audit


def test_preflight_fails_closed_on_missing_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_config_for_preflight(monkeypatch, tmp_path, study="S", inputs_present=False)
    state = ORCH._RunState(study="S", run_id="run_a")
    state.path = tmp_path / "run_state.json"
    rc = ORCH._preflight(state, study="S", run_id="run_a", resume_held=False, force=False)
    assert rc == 2
    assert state.phases[-1].status == "failed"


def test_preflight_passes_when_inputs_present_and_no_recorded_fp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_config_for_preflight(monkeypatch, tmp_path, study="S", inputs_present=True)
    state = ORCH._RunState(study="S", run_id="run_b")
    state.path = tmp_path / "run_state.json"
    rc = ORCH._preflight(state, study="S", run_id="run_b", resume_held=False, force=False)
    assert rc == 0
    assert state.input_fingerprint is not None
    assert state.phases[-1].status == "complete"


def test_preflight_short_circuits_on_redundant_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    audit = _patch_config_for_preflight(monkeypatch, tmp_path, study="S", inputs_present=True)
    from scripts.utils.input_fingerprint import (
        compute_input_fingerprint,
        fingerprint_record_path,
        write_fingerprint_record,
    )

    fp = compute_input_fingerprint(study="S")
    write_fingerprint_record(fingerprint_record_path(audit), fp)

    state = ORCH._RunState(study="S", run_id="run_c")
    state.path = tmp_path / "run_state.json"
    rc = ORCH._preflight(state, study="S", run_id="run_c", resume_held=False, force=False)
    assert rc == -1  # redundant sentinel
    assert state.status == "skipped_redundant"


def test_publish_subprocess_resolves_main_py_at_repo_root() -> None:
    """Regression: after the Wave-2 move, extract_to_llm_source sat 5 levels deep,
    so ``Path(__file__).parent.parent.parent / 'main.py'`` resolved to a
    nonexistent ``skills/main.py`` and every full publish died with exit 2. The
    fix routes the main.py path through config.BASE_DIR. Lock both facts."""
    ext_path = (
        Path(config.BASE_DIR)
        / "plugins"
        / "report-ai-study-pipeline"
        / "skills"
        / "dataset-to-llm-source"
        / "scripts"
        / "extract_to_llm_source.py"
    )
    src = ext_path.read_text(encoding="utf-8")
    assert "Path(_config.BASE_DIR)" in src or "Path(config.BASE_DIR)" in src
    assert "Path(__file__).parent.parent.parent" not in src
    assert (Path(config.BASE_DIR) / "main.py").is_file()


def test_assertions_and_gate_use_merged_scrub_config_loader() -> None:
    """Risk #8: assertions 12/14 and the approval gate must load the EFFECTIVE
    deep-merged config (load_scrub_config(study=...)) — not a single-file load —
    so the decided-vs-applied / coverage checks and the published-raw gate
    evaluate the SAME config run_scrub applied once per-study overrides land."""
    ext_path = (
        Path(config.BASE_DIR)
        / "plugins"
        / "report-ai-study-pipeline"
        / "skills"
        / "dataset-to-llm-source"
        / "scripts"
        / "extract_to_llm_source.py"
    )
    src = ext_path.read_text(encoding="utf-8")
    # Exactly the three risk-#8 sites use the merged study= loader.
    assert src.count("load_scrub_config(study=study)") == 3
    # The old single-file effective-path load must be gone from those sites.
    assert "load_scrub_config(scrub_config_path)" not in src
    assert "load_scrub_config(Path(config.PHI_SCRUB_CONFIG_PATH))" not in src


def test_preflight_force_overrides_redundancy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    audit = _patch_config_for_preflight(monkeypatch, tmp_path, study="S", inputs_present=True)
    from scripts.utils.input_fingerprint import (
        compute_input_fingerprint,
        fingerprint_record_path,
        write_fingerprint_record,
    )

    write_fingerprint_record(fingerprint_record_path(audit), compute_input_fingerprint(study="S"))

    state = ORCH._RunState(study="S", run_id="run_d")
    state.path = tmp_path / "run_state.json"
    rc = ORCH._preflight(state, study="S", run_id="run_d", resume_held=False, force=True)
    assert rc == 0  # --force ignores the redundant fingerprint
