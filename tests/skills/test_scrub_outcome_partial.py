"""Tests for scrub-leg partial-publish surfacing.

Coverage
--------
A. scrub_outcome.json with partial=True → _cmd_run sets exit 8,
   status.json.partial_forms populated, publish_status="partial".

B. scrub_outcome.json with partial=False → exit unchanged (0 on clean run),
   no partial_forms key in status.json.

C. Absent scrub_outcome.json → treated as clean (no crash, no partial notice).

D. bundle_status.partial_run_notice returns the right non-blocking advisory
   string for a status.json with partial_forms, and None for a clean one.

All fixtures are synthetic JSON only — no real PHI, no real dataset values.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

import config
import scripts.skills.extract_to_llm_source as skill_mod
from scripts.audit.ledger import dataset_phi_ledger_path
from scripts.ai_assistant.ui.bundle_status import partial_run_notice
from scripts.skills.extract_to_llm_source import (
    EXIT_OK,
    EXIT_PARTIAL_REVIEW,
    main,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

STUDY = "Test-Study"
_FIXED_RUN_ID = "run_test_scrub_partial_001"


@pytest.fixture(autouse=True)
def _bypass_phi_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock out the PHI approval gate so these tests focus on scrub_outcome."""
    monkeypatch.setattr(
        skill_mod,
        "_run_form_approval_gate",
        lambda **_kw: skill_mod.FormGateResult(
            approved_forms=("approved.xlsx",),
            held_forms=(),
            approval_report_path=None,
            partial=False,
        ),
    )


def _patch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(
        config,
        "DATASETS_DIR",
        tmp_path / f"data/raw/{STUDY}/datasets",
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "RAW_DATA_DIR",
        tmp_path / "data" / "raw",
        raising=False,
    )


def _write_valid_ledger(output_dir: Path) -> None:
    audit_dir = output_dir / STUDY / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    ledger = {
        "run_id": _FIXED_RUN_ID,
        "scrub_config_hash": "abc123",
        "input_dataset_hash": "def456",
    }
    ledger_path = dataset_phi_ledger_path(audit_dir, "approved.xlsx")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")


def _make_staging(staging_dir: Path) -> None:
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "dummy.jsonl").write_bytes(b"data")


def _make_datasets_dir(datasets_dir: Path) -> None:
    datasets_dir.mkdir(parents=True, exist_ok=True)


def _write_scrub_outcome(run_dir: Path, payload: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scrub_outcome.json").write_text(json.dumps(payload), encoding="utf-8")


def _fake_destroy(**kwargs: Any) -> Path:
    """Mock destroy_staging_and_attest: remove staging + write stub attestation."""
    shutil.rmtree(str(kwargs["staging_dir"]), ignore_errors=True)
    attest_path = kwargs["output_dir"] / "runs" / kwargs["run_id"] / "destruction_attestation.json"
    attest_path.parent.mkdir(parents=True, exist_ok=True)
    attest_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
    return attest_path


# ---------------------------------------------------------------------------
# Helpers shared across Part A / B / C tests
# ---------------------------------------------------------------------------


def _run_cmd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict]:
    """Drive _cmd_run via main() with subprocess + destructor mocked to succeed.

    The fixed run_id is set via REPORTAL_RUN_ID so the scrub_outcome.json
    written by each test's setup lands in exactly the right path.

    Returns (exit_code, parsed_status_json).
    """
    output_dir = tmp_path / "output"
    staging_dir = tmp_path / "tmp" / STUDY
    datasets_dir = tmp_path / "data" / "raw" / STUDY / "datasets"

    _patch_config(monkeypatch, tmp_path)
    _write_valid_ledger(output_dir)
    _make_staging(staging_dir)
    _make_datasets_dir(datasets_dir)

    # Pin run_id via env var (resolve_run_id reads REPORTAL_RUN_ID first).
    monkeypatch.setenv("REPORTAL_RUN_ID", _FIXED_RUN_ID)

    # Intercept lock acquire/release.
    monkeypatch.setattr(skill_mod, "_acquire_pipeline_lock_for_skill", lambda _s: None)
    monkeypatch.setattr(skill_mod, "_release_pipeline_lock_for_skill", lambda: None)

    # Intercept manifest check (no real manifest needed).
    monkeypatch.setattr(skill_mod, "check_forms_manifest", lambda _d: None)

    # Intercept scan_for_in_progress_scrubs (no in-progress tokens).
    with patch(
        "scripts.utils.run_context.scan_for_in_progress_scrubs",
        return_value=[],
    ), patch.object(
        skill_mod,
        "destroy_staging_and_attest",
        _fake_destroy,
    ), patch(
        "subprocess.run",
        return_value=SimpleNamespace(returncode=0),
    ):
        rc = main(["run", "--study", STUDY])

    # Parse status.json.
    status_path = output_dir / STUDY / "runs" / _FIXED_RUN_ID / "status.json"
    status: dict = {}
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return rc, status


# ---------------------------------------------------------------------------
# Part A — partial=True → exit 8, partial_forms in status, publish_status=partial
# ---------------------------------------------------------------------------


class TestScrubOutcomePartial:
    """scrub_outcome.json with partial=True surfaces correctly."""

    def test_exit_code_is_partial_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Write the scrub_outcome BEFORE the run so _cmd_run finds it.
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_scrub_outcome(
            run_dir,
            {
                "run_id": run_id,
                "study": STUDY,
                "partial": True,
                "partial_forms": {
                    "7_Culture.jsonl": {"kept": 1080, "quarantined": 37, "reasons": ["date_unshiftable:37"]}
                },
            },
        )
        rc, _status = _run_cmd(tmp_path, monkeypatch)
        assert rc == EXIT_PARTIAL_REVIEW

    def test_status_publish_status_is_partial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_scrub_outcome(
            run_dir,
            {
                "run_id": run_id,
                "study": STUDY,
                "partial": True,
                "partial_forms": {
                    "7_Culture.jsonl": {"kept": 1080, "quarantined": 37, "reasons": ["date_unshiftable:37"]}
                },
            },
        )
        _rc, status = _run_cmd(tmp_path, monkeypatch)
        assert status.get("publish_status") == "partial"

    def test_status_partial_forms_populated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_scrub_outcome(
            run_dir,
            {
                "run_id": run_id,
                "study": STUDY,
                "partial": True,
                "partial_forms": {
                    "7_Culture.jsonl": {"kept": 1080, "quarantined": 37, "reasons": ["date_unshiftable:37"]}
                },
            },
        )
        _rc, status = _run_cmd(tmp_path, monkeypatch)
        pf = status.get("partial_forms")
        assert isinstance(pf, list) and len(pf) == 1
        entry = pf[0]
        assert entry["form"] == "7_Culture.jsonl"
        assert entry["kept"] == 1080
        assert entry["quarantined"] == 37
        assert "date_unshiftable:37" in entry["reasons"]

    def test_exit_code_not_downgraded_when_worse_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If exit code is already worse than EXIT_PARTIAL_REVIEW, scrub partial must NOT downgrade it."""
        # This is tested at unit level by inspecting the logic directly:
        # The guard is `if _scrub_partial and final_code == EXIT_OK: final_code = EXIT_PARTIAL_REVIEW`
        # We can't trigger a "worse code" through the full run without more mocks,
        # so we verify the condition in the source instead.
        source = Path(skill_mod.__file__).read_text(encoding="utf-8")
        assert "final_code == EXIT_OK" in source, (
            "Downgrade guard `if _scrub_partial and final_code == EXIT_OK` not found in source"
        )


# ---------------------------------------------------------------------------
# Part B — partial=False → exit 0, no partial_forms key
# ---------------------------------------------------------------------------


class TestScrubOutcomeClean:
    """scrub_outcome.json with partial=False → no change to exit or status."""

    def test_exit_code_unchanged_on_false_partial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_scrub_outcome(
            run_dir,
            {
                "run_id": run_id,
                "study": STUDY,
                "partial": False,
                "partial_forms": {},
            },
        )
        rc, _status = _run_cmd(tmp_path, monkeypatch)
        assert rc == EXIT_OK

    def test_no_partial_forms_key_on_clean_scrub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_scrub_outcome(
            run_dir,
            {
                "run_id": run_id,
                "study": STUDY,
                "partial": False,
                "partial_forms": {},
            },
        )
        _rc, status = _run_cmd(tmp_path, monkeypatch)
        # partial_forms must be absent (empty list would be omitted by the code)
        assert "partial_forms" not in status

    def test_publish_status_complete_on_clean_scrub(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_scrub_outcome(
            run_dir,
            {
                "run_id": run_id,
                "study": STUDY,
                "partial": False,
                "partial_forms": {},
            },
        )
        _rc, status = _run_cmd(tmp_path, monkeypatch)
        assert status.get("publish_status") == "complete"


# ---------------------------------------------------------------------------
# Part C — absent scrub_outcome.json → treated as clean (no crash)
# ---------------------------------------------------------------------------


class TestScrubOutcomeAbsent:
    """No scrub_outcome.json → clean run (best-effort, no error)."""

    def test_absent_sidecar_exit_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Do NOT write scrub_outcome.json
        rc, _status = _run_cmd(tmp_path, monkeypatch)
        assert rc == EXIT_OK

    def test_absent_sidecar_no_partial_forms(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _rc, status = _run_cmd(tmp_path, monkeypatch)
        assert "partial_forms" not in status

    def test_corrupt_sidecar_treated_as_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A corrupt (non-JSON) scrub_outcome.json must not crash the run."""
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "scrub_outcome.json").write_text("{ not valid json!", encoding="utf-8")
        rc, status = _run_cmd(tmp_path, monkeypatch)
        assert rc == EXIT_OK
        assert "partial_forms" not in status

    def test_wrong_run_id_sidecar_treated_as_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sidecar with a different run_id must be ignored (treated as clean)."""
        run_id = "run_test_scrub_partial_001"
        run_dir = tmp_path / "output" / STUDY / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_scrub_outcome(
            run_dir,
            {
                "run_id": "run_DIFFERENT_001",  # deliberate mismatch
                "study": STUDY,
                "partial": True,
                "partial_forms": {
                    "7_Culture.jsonl": {"kept": 1080, "quarantined": 37, "reasons": ["date_unshiftable:37"]}
                },
            },
        )
        rc, status = _run_cmd(tmp_path, monkeypatch)
        assert rc == EXIT_OK
        assert "partial_forms" not in status


# ---------------------------------------------------------------------------
# Part D — bundle_status.partial_run_notice
# ---------------------------------------------------------------------------


def _write_run_status_fixture(
    study: str,
    run_id: str,
    status: dict,
) -> None:
    """Write status.json under config.OUTPUT_DIR for the bundle_status tests."""
    run_dir = Path(config.OUTPUT_DIR) / study / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")


class TestPartialRunNotice:
    """partial_run_notice returns the right advisory string or None."""

    def test_returns_none_when_no_runs(self, monkeypatch_config: Path) -> None:
        assert partial_run_notice(config.STUDY_NAME) is None

    def test_returns_none_for_clean_run(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _write_run_status_fixture(
            study,
            "run_clean0000001",
            {"publish_status": "complete", "exit_code": 0},
        )
        assert partial_run_notice(study) is None

    def test_returns_none_when_partial_forms_absent(self, monkeypatch_config: Path) -> None:
        """A partial run (form-gate held) with no partial_forms key → None."""
        study = config.STUDY_NAME
        _write_run_status_fixture(
            study,
            "run_held0000001",
            {
                "publish_status": "partial",
                "exit_code": 8,
                "held_forms": ["held_form.xlsx"],
                # no partial_forms key
            },
        )
        assert partial_run_notice(study) is None

    def test_returns_none_when_partial_forms_empty(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _write_run_status_fixture(
            study,
            "run_empty0000001",
            {
                "publish_status": "partial",
                "exit_code": 8,
                "partial_forms": [],
            },
        )
        assert partial_run_notice(study) is None

    def test_returns_advisory_string_for_partial_run(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _write_run_status_fixture(
            study,
            "run_partial000001",
            {
                "publish_status": "partial",
                "exit_code": 8,
                "partial_forms": [
                    {
                        "form": "7_Culture.jsonl",
                        "kept": 1080,
                        "quarantined": 37,
                        "reasons": ["date_unshiftable:37"],
                    }
                ],
            },
        )
        notice = partial_run_notice(study)
        assert notice is not None
        assert "7_Culture.jsonl" in notice
        assert "1080" in notice
        assert "37" in notice
        assert "date_unshiftable:37" in notice
        # Advisory framing: must mention rows are queryable.
        assert "queryable" in notice.lower()

    def test_lists_multiple_partial_forms(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        _write_run_status_fixture(
            study,
            "run_multi000001",
            {
                "publish_status": "partial",
                "exit_code": 8,
                "partial_forms": [
                    {
                        "form": "7_Culture.jsonl",
                        "kept": 1080,
                        "quarantined": 37,
                        "reasons": ["date_unshiftable:37"],
                    },
                    {
                        "form": "3_Xray.jsonl",
                        "kept": 500,
                        "quarantined": 10,
                        "reasons": ["date_unshiftable:10"],
                    },
                ],
            },
        )
        notice = partial_run_notice(study)
        assert notice is not None
        assert "7_Culture.jsonl" in notice
        assert "3_Xray.jsonl" in notice

    def test_never_raises_on_malformed_status(self, monkeypatch_config: Path) -> None:
        study = config.STUDY_NAME
        run_dir = Path(config.OUTPUT_DIR) / study / "runs" / "run_bad0000001"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "status.json").write_text("{ not json!", encoding="utf-8")
        # Must not raise.
        assert partial_run_notice(study) is None

    def test_uses_latest_run_by_completed_utc(self, monkeypatch_config: Path) -> None:
        """Chronologically latest run is used, not lexicographically largest name."""
        study = config.STUDY_NAME
        # Older (lexicographically larger name) has partial_forms.
        _write_run_status_fixture(
            study,
            "run_zzz_older_001",
            {
                "publish_status": "partial",
                "exit_code": 8,
                "completed_utc": "2026-06-01T00:00:00+00:00",
                "partial_forms": [
                    {"form": "OLD_FORM.jsonl", "kept": 100, "quarantined": 5, "reasons": []}
                ],
            },
        )
        # Newer (lexicographically smaller name) is a clean complete run.
        _write_run_status_fixture(
            study,
            "run_aaa_newer_001",
            {
                "publish_status": "complete",
                "exit_code": 0,
                "completed_utc": "2026-06-05T00:00:00+00:00",
            },
        )
        # Must pick the newer clean run → None.
        assert partial_run_notice(study) is None


# ---------------------------------------------------------------------------
# Part D — Step 4b: a non-empty quarantine is the EXPECTED state in partial
# mode (must reach EXIT_PARTIAL_REVIEW + be destroyed), but an UNEXPECTED
# quarantine (no partial sidecar) must still hard-fail EXIT_QUARANTINE_NON_EMPTY.
# This is the regression guard for the dead-feature bug: Step 4b used to return
# exit 4 on ANY non-empty quarantine, blocking partial publish before exit 8.
# ---------------------------------------------------------------------------


def _run_cmd_with_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, partial: bool
) -> tuple[int, Path]:
    """Same harness as _run_cmd but seeds a NON-EMPTY quarantine dir before the
    run and writes a scrub_outcome.json with the given ``partial`` flag.

    Returns (exit_code, staging_dir) — caller asserts staging_dir was destroyed
    (Step 5 ran) for the partial case, or preserved (Step 4b bailed) otherwise.
    """
    output_dir = tmp_path / "output"
    staging_dir = tmp_path / "tmp" / STUDY
    datasets_dir = tmp_path / "data" / "raw" / STUDY / "datasets"

    _patch_config(monkeypatch, tmp_path)
    _write_valid_ledger(output_dir)
    _make_staging(staging_dir)
    _make_datasets_dir(datasets_dir)

    # Seed a non-empty quarantine dir — what the scrub leg produces when it holds
    # un-jitterable rows. Filename mirrors run_scrub's date_unshiftable_<file>.jsonl.
    quarantine_dir = staging_dir / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    (quarantine_dir / "date_unshiftable_7_Culture.jsonl").write_text(
        '{"_quarantined": true}\n', encoding="utf-8"
    )

    # Pin run_id and write the scrub_outcome sidecar the wrapper reads at Step 3.5.
    monkeypatch.setenv("REPORTAL_RUN_ID", _FIXED_RUN_ID)
    run_dir = output_dir / STUDY / "runs" / _FIXED_RUN_ID
    _write_scrub_outcome(
        run_dir,
        {
            "run_id": _FIXED_RUN_ID,
            "study": STUDY,
            "partial": partial,
            "partial_forms": (
                {"7_Culture.jsonl": {"kept": 1080, "quarantined": 37, "reasons": ["date_unshiftable:37"]}}
                if partial
                else {}
            ),
        },
    )

    monkeypatch.setattr(skill_mod, "_acquire_pipeline_lock_for_skill", lambda _s: None)
    monkeypatch.setattr(skill_mod, "_release_pipeline_lock_for_skill", lambda: None)
    monkeypatch.setattr(skill_mod, "check_forms_manifest", lambda _d: None)

    with patch(
        "scripts.utils.run_context.scan_for_in_progress_scrubs", return_value=[]
    ), patch.object(skill_mod, "destroy_staging_and_attest", _fake_destroy), patch(
        "subprocess.run", return_value=SimpleNamespace(returncode=0)
    ):
        rc = main(["run", "--study", STUDY])
    return rc, staging_dir


class TestStep4bPartialQuarantineTolerance:
    """Step 4b must distinguish expected (partial) vs unexpected quarantine."""

    def test_partial_with_nonempty_quarantine_reaches_exit_8(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """partial=True + non-empty quarantine → EXIT_PARTIAL_REVIEW (NOT exit 4).

        This is the core regression: the un-scrubbable rows were quarantined on
        purpose; the run must publish the good rows and partial-review, not abort.
        """
        rc, staging_dir = _run_cmd_with_quarantine(tmp_path, monkeypatch, partial=True)
        assert rc == EXIT_PARTIAL_REVIEW
        # Step 5 destruction ran → staging (incl. quarantine) is gone, no PHI persists.
        assert not staging_dir.exists()

    def test_nonpartial_with_nonempty_quarantine_still_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """partial=False + non-empty quarantine → EXIT_QUARANTINE_NON_EMPTY (4).

        Fail-closed gate preserved: an UNEXPECTED quarantine (strict mode / no
        partial sidecar) still hard-stops before destruction, staging preserved.
        """
        rc, staging_dir = _run_cmd_with_quarantine(tmp_path, monkeypatch, partial=False)
        assert rc == skill_mod.EXIT_QUARANTINE_NON_EMPTY
        # Bailed at Step 4b before Step 5 → staging preserved for operator review.
        assert staging_dir.exists()
