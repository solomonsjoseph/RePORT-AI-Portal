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


def test_dictionary_skill_phase_is_invoked_by_orchestrator() -> None:
    """N1: dictionary extraction must be a real skill phase, not only host inline code."""
    source = _ORCH_PATH.read_text(encoding="utf-8")

    assert '"dictionary-to-llm-source"' in source
    assert '"--leg", "extract"' in source
    assert '"P1c:dictionary-extract"' in source


def test_absorb_status_reads_held_and_snapshot(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps({"held_forms": ["2A", "14"], "snapshot_id": "snap_abc"}),
        encoding="utf-8",
    )
    state = ORCH._RunState(study="S", run_id="run_z")
    ORCH._absorb_status(state, tmp_path)
    assert state.held_forms == ["2A", "14"]
    assert state.snapshot_id == "snap_abc"


def test_absorb_status_falls_back_to_sot_joined_gate_outcome(tmp_path: Path) -> None:
    """Count-only status.json + sot_joined_gate_outcome populates held_forms."""
    (tmp_path / "status.json").write_text(
        json.dumps({"held_forms_count": 2, "exit_code": 8}),
        encoding="utf-8",
    )
    (tmp_path / "sot_joined_gate_outcome.json").write_text(
        json.dumps(
            {
                "run_id": "run_z",
                "study": "S",
                "held": True,
                "held_forms": ["15_Feces.xlsx", "20_CoEnroll.xlsx"],
                "held_count": 2,
            }
        ),
        encoding="utf-8",
    )
    state = ORCH._RunState(study="S", run_id="run_z")
    ORCH._absorb_status(state, tmp_path)
    assert state.held_forms == ["15_Feces.xlsx", "20_CoEnroll.xlsx"]


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


def test_preflight_redundant_activates_existing_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C5.5: on a redundant run, preflight points `current` at the existing clean
    snapshot for the fingerprint and records its id on the run state."""
    audit = _patch_config_for_preflight(monkeypatch, tmp_path, study="S", inputs_present=True)
    from scripts.utils import snapshot as snap_mod
    from scripts.utils.input_fingerprint import (
        compute_input_fingerprint,
        fingerprint_record_path,
        write_fingerprint_record,
    )

    write_fingerprint_record(fingerprint_record_path(audit), compute_input_fingerprint(study="S"))

    activated: dict[str, str] = {}
    monkeypatch.setattr(snap_mod, "find_snapshot_by_fingerprint", lambda *_a, **_k: "snap_existing")
    monkeypatch.setattr(
        snap_mod,
        "set_current_snapshot",
        lambda study, sid, **_k: activated.update(study=study, sid=sid),
    )

    state = ORCH._RunState(study="S", run_id="run_e")
    state.path = tmp_path / "run_state.json"
    rc = ORCH._preflight(state, study="S", run_id="run_e", resume_held=False, force=False)
    assert rc == -1
    assert state.status == "skipped_redundant"
    assert state.snapshot_id == "snap_existing"
    assert activated == {"study": "S", "sid": "snap_existing"}


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


# ── Per-form state machine (Note 16) ─────────────────────────────────────────


def test_form_stem_strips_only_dataset_extensions() -> None:
    assert ORCH._form_stem("9_EEval.xlsx") == "9_EEval"
    assert ORCH._form_stem("9_EEval") == "9_EEval"
    assert ORCH._form_stem("v1.2_Form") == "v1.2_Form"  # a dot that is NOT an extension
    assert ORCH._form_stem("data.CSV") == "data"  # case-insensitive


def test_init_and_advance_forms_serialize_per_form_state(tmp_path: Path) -> None:
    state = ORCH._RunState(study="S", run_id="run_f")
    state.path = tmp_path / "run_state.json"
    state.init_forms(["1_Enrollment.xlsx", "9_EEval"], {"1_Enrollment": "fpA", "9_EEval": "fpB"})

    data = json.loads(state.path.read_text())
    assert data["schema"] == 2  # schema bumped for the per-form map
    assert set(data["forms"]) == {"1_Enrollment", "9_EEval"}  # keyed by stem
    assert data["forms"]["1_Enrollment"]["state"] == ORCH.FORM_NOT_STARTED
    assert data["forms"]["1_Enrollment"]["fingerprint"] == "fpA"

    state.advance_forms(ORCH.FORM_RUNNING, from_states={ORCH.FORM_NOT_STARTED})
    data = json.loads(state.path.read_text())
    assert all(f["state"] == ORCH.FORM_RUNNING for f in data["forms"].values())


def test_init_forms_idempotent_keeps_state_refreshes_fingerprint(tmp_path: Path) -> None:
    state = ORCH._RunState(study="S", run_id="run_g")
    state.path = tmp_path / "run_state.json"
    state.init_forms(["A"], {"A": "fp1"})
    state.advance_forms(ORCH.FORM_RUNNING, from_states={ORCH.FORM_NOT_STARTED})
    # A second init (e.g. re-entry) must NOT reset a running form to not_started,
    # but may refresh its fingerprint.
    state.init_forms(["A"], {"A": "fp2"})
    assert state.forms["A"].state == ORCH.FORM_RUNNING
    assert state.forms["A"].fingerprint == "fp2"


def test_absorb_form_outcomes_from_approval_report(tmp_path: Path) -> None:
    """approved → complete, held → held_for_review; a dedup-dropped form (present
    in the map but absent from the report) drops out of the publish-set map."""
    state = ORCH._RunState(study="S", run_id="run_h")
    state.path = tmp_path / "run_state.json"
    state.forms = {
        "A": ORCH._FormRecord(name="A", state=ORCH.FORM_RUNNING, fingerprint="fpA"),
        "B": ORCH._FormRecord(name="B", state=ORCH.FORM_RUNNING, fingerprint="fpB"),
        "C": ORCH._FormRecord(name="C", state=ORCH.FORM_RUNNING),  # deduped away
    }
    (tmp_path / "phi_handling_approval.json").write_text(
        json.dumps({"approved_forms": ["A.xlsx"], "held_forms": ["B.xlsx"]}),
        encoding="utf-8",
    )
    ORCH._absorb_form_outcomes(state, tmp_path, pipeline_failed=False)
    assert state.forms["A"].state == ORCH.FORM_COMPLETE
    assert state.forms["A"].fingerprint == "fpA"  # preserved
    assert state.forms["B"].state == ORCH.FORM_HELD
    assert "C" not in state.forms  # dropped from the publish set


def test_absorb_form_outcomes_pipeline_failure_marks_failed(tmp_path: Path) -> None:
    state = ORCH._RunState(study="S", run_id="run_i")
    state.path = tmp_path / "run_state.json"
    state.forms = {
        "A": ORCH._FormRecord(name="A", state=ORCH.FORM_RUNNING),
        "B": ORCH._FormRecord(name="B", state=ORCH.FORM_COMPLETE),  # already terminal
    }
    ORCH._absorb_form_outcomes(state, tmp_path, pipeline_failed=True)
    assert state.forms["A"].state == ORCH.FORM_FAILED
    assert state.forms["B"].state == ORCH.FORM_COMPLETE  # terminal untouched


def test_scan_for_interrupted_run_finds_in_progress_excluding_current(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "run_old").mkdir(parents=True)
    (runs / "run_old" / "run_state.json").write_text(
        json.dumps({"status": "in_progress", "run_id": "run_old"}), encoding="utf-8"
    )
    (runs / "run_done").mkdir(parents=True)
    (runs / "run_done" / "run_state.json").write_text(
        json.dumps({"status": "complete", "run_id": "run_done"}), encoding="utf-8"
    )
    (runs / "run_new").mkdir(parents=True)
    (runs / "run_new" / "run_state.json").write_text(
        json.dumps({"status": "in_progress", "run_id": "run_new"}), encoding="utf-8"
    )
    found = ORCH._scan_for_interrupted_run(runs, exclude_run_id="run_new")
    assert found == runs / "run_old" / "run_state.json"  # in_progress, not the current run
    assert ORCH._scan_for_interrupted_run(tmp_path / "nope", exclude_run_id="x") is None


def test_recover_interrupted_run_writes_note_and_marks_recovered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import config
    from scripts.utils.input_fingerprint import compute_per_form_fingerprint

    output = tmp_path / "output"
    runs = output / "runs"
    datasets = tmp_path / "datasets"
    datasets.mkdir(parents=True)
    (datasets / "C.xlsx").write_bytes(b"c-bytes")
    (datasets / "D.xlsx").write_bytes(b"d-bytes")
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", output)
    monkeypatch.setattr(config, "DATASETS_DIR", datasets)

    # D's recorded fingerprint MATCHES its current inputs → cache-valid → kept.
    d_fp = compute_per_form_fingerprint("D", study="S", datasets_dir=datasets)

    old = runs / "run_old"
    old.mkdir(parents=True)
    (old / "run_state.json").write_text(
        json.dumps(
            {
                "status": "in_progress",
                "run_id": "run_old",
                "forms": {
                    "A": {"name": "A", "state": ORCH.FORM_RUNNING, "fingerprint": None},
                    "B": {"name": "B", "state": ORCH.FORM_HELD, "fingerprint": None},
                    "C": {"name": "C", "state": ORCH.FORM_COMPLETE, "fingerprint": "STALE_FP"},
                    "D": {"name": "D", "state": ORCH.FORM_COMPLETE, "fingerprint": d_fp},
                },
            }
        ),
        encoding="utf-8",
    )
    new = runs / "run_new"
    new.mkdir(parents=True)
    state = ORCH._RunState(study="S", run_id="run_new")
    state.path = new / "run_state.json"

    summary = ORCH._recover_interrupted_run(state, study="S", run_dir=new)
    assert summary is not None
    assert summary["recovered_from_run"] == "run_old"
    assert summary["reset_running"] == ["A"]
    assert summary["carried_held"] == ["B"]
    # C's recorded fingerprint ("STALE_FP") cannot match; D's matches.
    assert summary["revalidated_complete"] == [
        {"form": "C", "cache_valid": False},
        {"form": "D", "cache_valid": True},
    ]

    # The readback APPLIES the rules to the new run's state (spec lines 751-754).
    assert state.forms["A"].state == ORCH.FORM_NOT_STARTED  # running → reset
    assert state.forms["B"].state == ORCH.FORM_HELD  # held → carried forward
    assert state.forms["C"].state == ORCH.FORM_NOT_STARTED  # complete, changed → re-run
    assert state.forms["D"].state == ORCH.FORM_COMPLETE  # complete, cache-valid → kept
    assert state.forms["D"].fingerprint == d_fp
    # …and the applied state is flushed to the new run_state.json immediately.
    flushed = json.loads(state.path.read_text())
    assert flushed["forms"]["B"]["state"] == ORCH.FORM_HELD

    note = json.loads((new / "run_recovery.json").read_text())
    assert note["recovered_from_run"] == "run_old"
    # The crashed run is marked recovered (atomically) so it is not re-detected.
    prior = json.loads((old / "run_state.json").read_text())
    assert prior["status"] == "failed"
    assert prior["recovered_by_run"] == "run_new"


def test_recover_interrupted_run_noop_when_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import config

    output = tmp_path / "output"
    (output / "runs").mkdir(parents=True)
    monkeypatch.setattr(config, "STUDY_OUTPUT_DIR", output)
    state = ORCH._RunState(study="S", run_id="run_new")
    state.path = output / "runs" / "run_new" / "run_state.json"
    assert ORCH._recover_interrupted_run(state, study="S", run_dir=tmp_path) is None
