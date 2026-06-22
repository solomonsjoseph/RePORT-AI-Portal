"""Orchestrator subprocess E2E — Synth-Demo full pipeline (Wave C.7).

Runs ``make study STUDY=Synth-Demo FORCE=1`` in a subprocess and asserts:
  - exit 0
  - expected phase order in ``run_state.json``
  - snapshot committed
  - verifier assertion 13 (status_json_updated) passes

Skipped when ``data/raw/Synth-Demo/datasets/`` is absent (gitignored in CI).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import config

REPO_ROOT = Path(config.BASE_DIR)
STUDY = "Synth-Demo"
SYNTH_DATASETS = REPO_ROOT / "data" / "raw" / STUDY / "datasets"
ENROLLMENT_XLSX = SYNTH_DATASETS / "1_Enrollment.xlsx"

_EXPECTED_PHASES = [
    "P0:preflight",
    "P1c:dictionary-extract",
    "P2:dataset-deduplication",
    "P1:header-extraction",
    "P1b:sot-lean-generate",
    "P2:publish",
    "P8:cleanup-verifier",
    "P9:verify",
    "P10:finalize",
]

_MAKE_TIMEOUT_S = 600


def _require_synth_demo_raw() -> None:
    if not ENROLLMENT_XLSX.is_file():
        pytest.skip(f"Synth-Demo raw data not present: {ENROLLMENT_XLSX}")


def _latest_run_dir(study_output: Path) -> Path:
    runs_root = study_output / "runs"
    run_dirs = sorted(
        (p for p in runs_root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    assert run_dirs, f"no run dirs under {runs_root}"
    return run_dirs[0]


@pytest.mark.slow
def test_orchestrator_e2e_synth_demo_clean_pass() -> None:
    """Full orchestrator subprocess: Synth-Demo → exit 0 + snapshot + phase order."""
    _require_synth_demo_raw()

    proc = subprocess.run(  # noqa: S603
        ["make", "study", f"STUDY={STUDY}", "FORCE=1"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=_MAKE_TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, (
        f"make study exited {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\n"
        f"stderr:\n{proc.stderr[-4000:]}"
    )

    study_output = REPO_ROOT / "output" / STUDY
    run_dir = _latest_run_dir(study_output)
    run_state_path = run_dir / "run_state.json"
    assert run_state_path.is_file(), f"run_state.json missing under {run_dir}"

    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    assert run_state.get("status") == "complete"
    phases = [rec["phase"] for rec in run_state.get("phases", [])]
    assert phases == _EXPECTED_PHASES, f"unexpected phase order: {phases}"

    snapshot_id = run_state.get("snapshot_id")
    assert snapshot_id, "run_state.json must record snapshot_id after P10"
    snapshot_dir = study_output / "snapshots" / snapshot_id
    assert snapshot_dir.is_dir(), f"snapshot dir missing: {snapshot_dir}"
    assert (snapshot_dir / "snapshot_manifest.json").is_file()

    verifier_report = json.loads((run_dir / "verifier_report.json").read_text(encoding="utf-8"))
    assert verifier_report.get("overall") == "pass"
    a13 = next((a for a in verifier_report["assertions"] if a.get("n") == 13), None)
    assert a13 is not None, "assertion 13 (status_json_updated) must be in verifier report"
    assert a13["result"] == "pass", a13
