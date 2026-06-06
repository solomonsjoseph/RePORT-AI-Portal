"""Tests for the --resume-held flag on the `run` subcommand (W3).

Coverage
--------
A. Happy path — held run -> (operator resolves) -> --resume-held re-runs the
   FULL surviving set (prior_approved ∪ prior_held) -> clean pass -> snapshot
   written + status.json.snapshot_id set.

B. Negative: --resume-held under REPORTAL_PROCESS_ROLE=llm-agent exits non-zero
   (EXIT_NEEDS_ADVICE) without running anything (no status.json written).

C. Negative: --resume-held with no prior run exits EXIT_NEEDS_ADVICE.

D. Negative: --resume-held when prior run has no held_forms exits EXIT_NEEDS_ADVICE.

E. --resume-held passes the FULL surviving set (approved ∪ held) to the approval
   gate, NOT only the held forms — data-loss guard: passing only held forms would
   delete previously-approved forms from llm_source/ on the whole-leg atomic replace.

All filesystem state lives under tmp_path; real study data is never touched.
The subprocess invocation of main.py and the pipeline lock are mocked out so
these tests are deterministic and require no live AI calls.

Patch-target note
-----------------
``resolve_run_id`` and ``scan_for_in_progress_scrubs`` are LAZILY imported
inside ``_cmd_run`` (not at module level in extract_to_llm_source), so they
must be patched at their SOURCE module:
  scripts.utils.run_context.resolve_run_id
  scripts.utils.run_context.scan_for_in_progress_scrubs
Patching them as extract_to_llm_source.* would raise AttributeError.
``_run_form_approval_gate`` and ``_resolve_run_id`` (underscore) ARE real
module-level attributes of extract_to_llm_source and are patched there.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from scripts.audit.ledger import dataset_phi_ledger_path
from scripts.skills.extract_to_llm_source import (
    EXIT_NEEDS_ADVICE,
    EXIT_OK,
    main,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUDY = "Test-ResumeHeld"
HELD_FORM = "held_form.xlsx"
CLEAN_FORM = "clean_form.xlsx"

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _patch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect config path constants to tmp_path so tests are hermetic."""
    import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)
    monkeypatch.setattr(
        config,
        "PHI_SCRUB_CONFIG_PATH",
        tmp_path / "scripts" / "security" / "phi_scrub.yaml",
        raising=False,
    )
    # write_snapshot uses config.STUDY_LLM_SOURCE_DIR (not config.OUTPUT_DIR / study)
    # so we also patch that.
    monkeypatch.setattr(
        config,
        "STUDY_LLM_SOURCE_DIR",
        tmp_path / "output" / STUDY / "llm_source",
        raising=False,
    )


def _make_manifest(study_raw_dir: Path, required: list[str]) -> None:
    manifest = {"required": required, "optional": [], "reject": []}
    manifest_path = study_raw_dir / "_forms_manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.dump(manifest), encoding="utf-8")


def _make_datasets(datasets_dir: Path, forms: list[str]) -> None:
    datasets_dir.mkdir(parents=True, exist_ok=True)
    for form in forms:
        (datasets_dir / form).write_bytes(b"stub-xlsx")


def _make_phi_scrub_yaml(phi_scrub_path: Path) -> None:
    phi_scrub_path.parent.mkdir(parents=True, exist_ok=True)
    phi_scrub_path.write_bytes(b"scrub_config: test")


def _make_llm_source(llm_source_dir: Path, forms: list[str]) -> None:
    """Create a minimal llm_source tree with one JSONL per form."""
    ds = llm_source_dir / "dataset_schema" / "files"
    ds.mkdir(parents=True, exist_ok=True)
    for form in forms:
        stem = Path(form).stem
        (ds / f"{stem}.jsonl").write_text(
            json.dumps({"col_a": "val_a", "col_b": "val_b"}) + "\n", encoding="utf-8"
        )


def _make_prior_partial_run(
    study_output_dir: Path,
    *,
    run_id: str = "run_prior001",
    approved_forms: list[str],
    held_forms: list[str],
) -> Path:
    """Write a prior partial-run status.json so --resume-held has something to read."""
    run_dir = study_output_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": run_id,
        "study": STUDY,
        "exit_code": 8,  # EXIT_PARTIAL_REVIEW
        "publish_status": "partial",
        "started_utc": _iso_now(),
        "completed_utc": _iso_now(),
        "verifier_passed": False,
        "approved_forms": approved_forms,
        "held_forms": held_forms,
        "approved_forms_count": len(approved_forms),
        "held_forms_count": len(held_forms),
    }
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return run_dir


def _make_phi_ledger(audit_dir: Path, form: str) -> None:
    """Write a minimal PHI ledger for a form so post-run ledger gate passes."""
    import hashlib

    scrub_hash = hashlib.sha256(b"scrub_config: test").hexdigest()
    ledger = {
        "run_id": "run_placeholder",
        "scrub_config_hash": scrub_hash,
        "input_dataset_hash": "abc123deadbeef",
        "keep_decisions": [
            {
                "variable_id": col,
                "jurisdictions": [],
                "matched_rules": [],
                "rationale": "retained per review",
                "rule_bundle_sha256": None,
            }
            for col in ("col_a", "col_b")
        ],
        "events": [],
    }
    ledger_path = dataset_phi_ledger_path(audit_dir, form)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")


def _make_no_llm_zone(audit_dir: Path) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / ".NO_LLM_ZONE").write_text("", encoding="utf-8")


def _make_attestation(run_dir: Path, run_id: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    attest = {
        "run_id": run_id,
        "study": STUDY,
        "started_utc": _iso_now(),
        "completed_utc": _iso_now(),
        "staging_path": f"tmp/{STUDY}",
        "removed_paths": [],
        "files_destroyed": 0,
        "cryptographic_erasure": False,
        "apfs_cow_disclaimer": "test",
    }
    (run_dir / "destruction_attestation.json").write_text(json.dumps(attest), encoding="utf-8")


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _make_form_approval_result(approved: list[str], held: list[str]) -> MagicMock:
    """Build a mock FormGateResult that looks like an approved/held split."""
    from scripts.skills.extract_to_llm_source import FormGateResult

    return FormGateResult(
        approved_forms=tuple(approved),
        held_forms=tuple(held),
        approval_report_path=None,
        partial=bool(held),
    )


# ---------------------------------------------------------------------------
# A. Happy path — held run -> --resume-held -> clean pass -> snapshot committed
# ---------------------------------------------------------------------------


class TestResumeHeldHappyPath:
    """--resume-held re-runs the full surviving set; on clean pass snapshot is committed."""

    def _setup_partial_study(self, tmp_path: Path) -> dict:
        """Build a study with a prior partial run (clean_form published, held_form held)."""
        study_raw_dir = tmp_path / "data" / "raw" / STUDY
        datasets_dir = study_raw_dir / "datasets"
        study_output_dir = tmp_path / "output" / STUDY
        audit_dir = study_output_dir / "audit"

        _make_manifest(study_raw_dir, [CLEAN_FORM, HELD_FORM])
        _make_datasets(datasets_dir, [CLEAN_FORM, HELD_FORM])
        _make_phi_scrub_yaml(tmp_path / "scripts" / "security" / "phi_scrub.yaml")

        # Prior run: clean_form already published, held_form held
        prior_run_dir = _make_prior_partial_run(
            study_output_dir,
            run_id="run_prior001",
            approved_forms=[CLEAN_FORM],
            held_forms=[HELD_FORM],
        )

        # Existing llm_source from the prior run (clean_form only)
        _make_llm_source(study_output_dir / "llm_source", [CLEAN_FORM])

        # Audit artefacts for the prior-run's approved form
        _make_phi_ledger(audit_dir, CLEAN_FORM)
        _make_no_llm_zone(audit_dir)

        return {
            "study_raw_dir": study_raw_dir,
            "datasets_dir": datasets_dir,
            "study_output_dir": study_output_dir,
            "audit_dir": audit_dir,
            "prior_run_dir": prior_run_dir,
        }

    def test_resume_held_gate_receives_full_surviving_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_run_form_approval_gate is called with the UNION of prior approved + held forms.

        Data-loss regression guard: passing only the held subset would cause the
        whole-leg atomic replace in main.py to delete previously-approved forms from
        llm_source/.  The implementation must pass sorted(prior_approved | prior_held).
        """
        _patch_config(monkeypatch, tmp_path)
        paths = self._setup_partial_study(tmp_path)
        study_output_dir = paths["study_output_dir"]

        # Prior run seeded by _setup_partial_study: approved=[CLEAN_FORM], held=[HELD_FORM]
        expected_union = tuple(sorted({CLEAN_FORM, HELD_FORM}))

        captured_selected: list[tuple[str, ...]] = []

        def _fake_gate(*, study, study_raw_dir, run_dir, max_workers, selected_forms):
            captured_selected.append(selected_forms)
            return _make_form_approval_result([CLEAN_FORM, HELD_FORM], [])

        with (
            patch(
                "scripts.skills.extract_to_llm_source._run_form_approval_gate",
                side_effect=_fake_gate,
            ),
            patch(
                "scripts.skills.extract_to_llm_source._acquire_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source._release_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source.check_forms_manifest",
            ),
            patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0),
            ),
            patch(
                "scripts.skills.extract_to_llm_source.iter_dataset_phi_ledger_paths",
                return_value=[
                    dataset_phi_ledger_path(paths["audit_dir"], CLEAN_FORM),
                    dataset_phi_ledger_path(paths["audit_dir"], HELD_FORM),
                ],
            ),
            patch(
                "scripts.skills.extract_to_llm_source.destroy_staging_and_attest",
                return_value=study_output_dir / "runs" / "run_new" / "destruction_attestation.json",
            ),
            patch(
                "scripts.skills.extract_to_llm_source._cmd_verify",
                return_value=EXIT_OK,
            ),
            patch(
                "scripts.skills.extract_to_llm_source._try_commit_snapshot",
                return_value="snap_abc123",
            ),
            patch(
                "scripts.utils.run_context.resolve_run_id",
                return_value="run_new001",
            ),
            patch(
                "scripts.utils.run_context.scan_for_in_progress_scrubs",
                return_value=[],
            ),
        ):
            # Provide ledger files for both forms so post-run ledger gate passes
            import hashlib

            scrub_hash = hashlib.sha256(b"scrub_config: test").hexdigest()
            for form in (CLEAN_FORM, HELD_FORM):
                ledger = dataset_phi_ledger_path(paths["audit_dir"], form)
                ledger.parent.mkdir(parents=True, exist_ok=True)
                ledger.write_text(
                    json.dumps(
                        {
                            "run_id": "run_new001",
                            "scrub_config_hash": scrub_hash,
                            "input_dataset_hash": "deadbeef",
                        }
                    ),
                    encoding="utf-8",
                )

            rc = main(["run", "--study", STUDY, "--resume-held"])

        # Approval gate must receive the FULL surviving set (approved ∪ held), sorted
        assert captured_selected, "approval gate was not called"
        assert captured_selected[0] == expected_union, (
            f"Expected gate to receive full surviving set {expected_union!r}, "
            f"got: {captured_selected[0]!r}"
        )

    def test_resume_held_clean_pass_snapshot_committed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On a clean --resume-held pass, _try_commit_snapshot is called."""
        _patch_config(monkeypatch, tmp_path)
        paths = self._setup_partial_study(tmp_path)
        study_output_dir = paths["study_output_dir"]

        snapshot_calls: list[dict] = []

        def _fake_snapshot(*, study, run_id, run_dir):
            snapshot_calls.append({"study": study, "run_id": run_id})
            return "snap_abc123"

        import hashlib

        scrub_hash = hashlib.sha256(b"scrub_config: test").hexdigest()

        def _fake_ledger_paths(audit_dir):
            ledger = dataset_phi_ledger_path(audit_dir, HELD_FORM)
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                json.dumps(
                    {
                        "run_id": "run_new001",
                        "scrub_config_hash": scrub_hash,
                        "input_dataset_hash": "deadbeef",
                    }
                ),
                encoding="utf-8",
            )
            return [dataset_phi_ledger_path(audit_dir, CLEAN_FORM), ledger]

        with (
            patch(
                "scripts.skills.extract_to_llm_source._run_form_approval_gate",
                return_value=_make_form_approval_result([HELD_FORM], []),
            ),
            patch(
                "scripts.skills.extract_to_llm_source._acquire_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source._release_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source.check_forms_manifest",
            ),
            patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0),
            ),
            patch(
                "scripts.skills.extract_to_llm_source.iter_dataset_phi_ledger_paths",
                side_effect=_fake_ledger_paths,
            ),
            patch(
                "scripts.skills.extract_to_llm_source.destroy_staging_and_attest",
                return_value=study_output_dir / "runs" / "run_new001" / "destruction_attestation.json",
            ),
            patch(
                "scripts.skills.extract_to_llm_source._cmd_verify",
                return_value=EXIT_OK,
            ),
            patch(
                "scripts.skills.extract_to_llm_source._try_commit_snapshot",
                side_effect=_fake_snapshot,
            ),
            patch(
                "scripts.utils.run_context.resolve_run_id",
                return_value="run_new001",
            ),
            patch(
                "scripts.utils.run_context.scan_for_in_progress_scrubs",
                return_value=[],
            ),
        ):
            rc = main(["run", "--study", STUDY, "--resume-held"])

        assert rc == EXIT_OK, f"Expected exit 0, got {rc}"
        assert snapshot_calls, "_try_commit_snapshot was not called"
        assert snapshot_calls[0]["study"] == STUDY

    def test_resume_held_status_json_has_snapshot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_try_commit_snapshot must record snapshot_id in status.json."""
        _patch_config(monkeypatch, tmp_path)
        study_output_dir = tmp_path / "output" / STUDY
        run_dir = study_output_dir / "runs" / "run_snap001"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "status.json").write_text(
            json.dumps({"run_id": "run_snap001", "study": STUDY, "verifier_passed": True}),
            encoding="utf-8",
        )

        # Patch write_snapshot to return a fake snapshot path
        fake_snap_path = study_output_dir / "snapshots" / "snap_testid0001"
        fake_snap_path.mkdir(parents=True, exist_ok=True)

        from scripts.skills.extract_to_llm_source import _try_commit_snapshot

        # _try_commit_snapshot lazily imports write_snapshot from
        # scripts.utils.snapshot inside the function, so patch it at the source.
        with patch(
            "scripts.utils.snapshot.write_snapshot",
            return_value=fake_snap_path,
        ):
            result = _try_commit_snapshot(
                study=STUDY, run_id="run_snap001", run_dir=run_dir
            )

        assert result == "snap_testid0001"
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        assert status.get("snapshot_id") == "snap_testid0001"

    def test_resume_held_verifier_fail_no_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When verifier fails on --resume-held, snapshot is NOT committed."""
        _patch_config(monkeypatch, tmp_path)
        paths = self._setup_partial_study(tmp_path)
        study_output_dir = paths["study_output_dir"]
        snapshot_calls: list[str] = []

        import hashlib

        scrub_hash = hashlib.sha256(b"scrub_config: test").hexdigest()

        def _fake_ledger_paths(audit_dir):
            ledger = dataset_phi_ledger_path(audit_dir, HELD_FORM)
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                json.dumps(
                    {
                        "run_id": "run_new001",
                        "scrub_config_hash": scrub_hash,
                        "input_dataset_hash": "deadbeef",
                    }
                ),
                encoding="utf-8",
            )
            return [ledger]

        with (
            patch(
                "scripts.skills.extract_to_llm_source._run_form_approval_gate",
                return_value=_make_form_approval_result([HELD_FORM], []),
            ),
            patch(
                "scripts.skills.extract_to_llm_source._acquire_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source._release_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source.check_forms_manifest",
            ),
            patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0),
            ),
            patch(
                "scripts.skills.extract_to_llm_source.iter_dataset_phi_ledger_paths",
                side_effect=_fake_ledger_paths,
            ),
            patch(
                "scripts.skills.extract_to_llm_source.destroy_staging_and_attest",
                return_value=study_output_dir / "runs" / "run_new001" / "destruction_attestation.json",
            ),
            # Verifier returns non-zero (e.g. EXIT_VERIFIER_FAIL)
            patch(
                "scripts.skills.extract_to_llm_source._cmd_verify",
                return_value=5,  # EXIT_VERIFIER_FAIL
            ),
            patch(
                "scripts.skills.extract_to_llm_source._try_commit_snapshot",
                side_effect=lambda **kw: snapshot_calls.append(kw) or "snap_x",
            ),
            patch(
                "scripts.utils.run_context.resolve_run_id",
                return_value="run_new001",
            ),
            patch(
                "scripts.utils.run_context.scan_for_in_progress_scrubs",
                return_value=[],
            ),
        ):
            rc = main(["run", "--study", STUDY, "--resume-held"])

        assert rc == 5, f"Expected verifier exit code 5, got {rc}"
        assert not snapshot_calls, "_try_commit_snapshot must not be called on verifier fail"


# ---------------------------------------------------------------------------
# B. Negative: --resume-held under REPORTAL_PROCESS_ROLE=llm-agent exits non-zero
# ---------------------------------------------------------------------------


class TestResumeHeldLlmAgentGuard:
    """--resume-held must be refused when REPORTAL_PROCESS_ROLE=llm-agent."""

    def test_refused_under_llm_agent_role(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "llm-agent")

        # Ensure no status.json is written — no run should start.
        runs_dir = tmp_path / "output" / STUDY / "runs"

        rc = main(["run", "--study", STUDY, "--resume-held"])

        assert rc == EXIT_NEEDS_ADVICE, f"Expected EXIT_NEEDS_ADVICE, got {rc}"
        # No new run directory should have been created (nothing ran)
        if runs_dir.exists():
            new_runs = [
                d
                for d in runs_dir.iterdir()
                if d.is_dir() and d.name != "run_prior001"
            ]
            assert not new_runs, (
                f"No run should be created when llm-agent guard fires, got: {new_runs}"
            )

    def test_refused_does_not_write_status_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guard must exit before any status.json is written."""
        _patch_config(monkeypatch, tmp_path)
        monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "llm-agent")

        rc = main(["run", "--study", STUDY, "--resume-held"])

        assert rc != EXIT_OK
        # The output/STUDY/runs/ tree should not have any run dirs from this invocation
        runs_dir = tmp_path / "output" / STUDY / "runs"
        assert not runs_dir.exists() or all(
            not any(d.iterdir()) for d in runs_dir.iterdir() if d.is_dir()
        ), "No run artifacts should be written when guard fires"

    def test_normal_run_allowed_under_llm_agent_role(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REPORTAL_PROCESS_ROLE=llm-agent only blocks --resume-held, not a plain run."""
        _patch_config(monkeypatch, tmp_path)
        monkeypatch.setenv("REPORTAL_PROCESS_ROLE", "llm-agent")

        # Plain run (without --resume-held) should NOT be blocked by the guard.
        # We check that the guard condition is not triggered by verifying that
        # the function proceeds past the guard check.  We mock everything else
        # to make it fail at the first real step (manifest check) cleanly.
        study_raw_dir = tmp_path / "data" / "raw" / STUDY
        _make_manifest(study_raw_dir, [CLEAN_FORM])
        # No datasets dir — check_forms_manifest will raise ManifestMismatchError

        from scripts.extraction.dataset_pipeline import ManifestMismatchError

        with patch(
            "scripts.skills.extract_to_llm_source._acquire_pipeline_lock_for_skill",
        ), patch(
            "scripts.skills.extract_to_llm_source._release_pipeline_lock_for_skill",
        ), patch(
            "scripts.utils.run_context.scan_for_in_progress_scrubs",
            return_value=[],
        ), patch(
            "scripts.skills.extract_to_llm_source.check_forms_manifest",
            side_effect=ManifestMismatchError("missing form"),
        ), patch(
            "scripts.utils.run_context.resolve_run_id",
            return_value="run_plain001",
        ):
            rc = main(["run", "--study", STUDY])

        # Should fail at manifest check (exit 2), NOT at the llm-agent guard (exit 6)
        assert rc == 2, (
            f"Plain run should fail at manifest (exit 2), not at llm-agent guard: rc={rc}"
        )


# ---------------------------------------------------------------------------
# C. Negative: --resume-held with no prior run
# ---------------------------------------------------------------------------


class TestResumeHeldNoPriorRun:
    def test_no_prior_run_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        # No runs/ directory exists at all
        rc = main(["run", "--study", STUDY, "--resume-held"])
        assert rc == EXIT_NEEDS_ADVICE, f"Expected EXIT_NEEDS_ADVICE, got {rc}"

    def test_no_prior_run_does_not_acquire_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lock must not be acquired if the pre-flight check for prior run fails."""
        _patch_config(monkeypatch, tmp_path)
        lock_calls: list[str] = []

        with patch(
            "scripts.skills.extract_to_llm_source._acquire_pipeline_lock_for_skill",
            side_effect=lambda study: lock_calls.append(study),
        ):
            main(["run", "--study", STUDY, "--resume-held"])

        assert not lock_calls, "Pipeline lock must not be acquired when pre-flight fails"


# ---------------------------------------------------------------------------
# D. Negative: --resume-held when prior run has no held_forms
# ---------------------------------------------------------------------------


class TestResumeHeldNoHeldForms:
    def test_prior_run_with_no_held_forms_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        study_output_dir = tmp_path / "output" / STUDY

        # Write a prior run that is fully clean (no held forms)
        run_dir = study_output_dir / "runs" / "run_clean001"
        run_dir.mkdir(parents=True, exist_ok=True)
        status = {
            "run_id": "run_clean001",
            "study": STUDY,
            "exit_code": 0,
            "publish_status": "complete",
            "started_utc": _iso_now(),
            "completed_utc": _iso_now(),
            "held_forms": [],
        }
        (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")

        rc = main(["run", "--study", STUDY, "--resume-held"])
        assert rc == EXIT_NEEDS_ADVICE, (
            f"Expected EXIT_NEEDS_ADVICE when prior run has no held forms, got {rc}"
        )

    def test_prior_run_held_forms_absent_key_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        study_output_dir = tmp_path / "output" / STUDY

        # Write a prior run whose status.json lacks held_forms key entirely
        run_dir = study_output_dir / "runs" / "run_old001"
        run_dir.mkdir(parents=True, exist_ok=True)
        status = {
            "run_id": "run_old001",
            "study": STUDY,
            "exit_code": 0,
            "publish_status": "complete",
            "started_utc": _iso_now(),
            "completed_utc": _iso_now(),
            # no "held_forms" key
        }
        (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")

        rc = main(["run", "--study", STUDY, "--resume-held"])
        assert rc == EXIT_NEEDS_ADVICE


# ---------------------------------------------------------------------------
# E. --resume-held passes the FULL surviving set (approved ∪ held) to the gate
# ---------------------------------------------------------------------------


class TestResumeHeldFormScope:
    """Verify the approval gate receives the full surviving set (approved ∪ held).

    This is the data-loss regression guard: the whole-leg atomic replace in
    main.py would delete previously-approved forms from llm_source/ if only the
    held subset were passed.  The implementation must pass the UNION.
    """

    def test_resume_held_gate_receives_full_surviving_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Gate receives sorted(prior_approved | prior_held), NOT a subset.

        Setup: manifest has 3 forms; 2 were previously approved, 1 was held.
        Expected: gate selected_forms == all 3 (the full surviving set, sorted).
        """
        _patch_config(monkeypatch, tmp_path)
        study_raw_dir = tmp_path / "data" / "raw" / STUDY
        study_output_dir = tmp_path / "output" / STUDY

        # Manifest has 3 forms; 2 approved, 1 held in prior run
        all_forms = ["form_a.xlsx", "form_b.xlsx", "form_c.xlsx"]
        approved_forms = ["form_a.xlsx", "form_b.xlsx"]
        held_form = "form_c.xlsx"
        # The implementation computes: sorted(set(approved) | set(held))
        expected_union = tuple(sorted(set(approved_forms) | {held_form}))

        _make_manifest(study_raw_dir, all_forms)
        _make_datasets(study_raw_dir / "datasets", all_forms)
        _make_phi_scrub_yaml(tmp_path / "scripts" / "security" / "phi_scrub.yaml")

        _make_prior_partial_run(
            study_output_dir,
            run_id="run_prior002",
            approved_forms=approved_forms,
            held_forms=[held_form],
        )

        captured_selected: list[tuple[str, ...]] = []

        def _fake_gate(*, study, study_raw_dir, run_dir, max_workers, selected_forms):
            captured_selected.append(selected_forms)
            return _make_form_approval_result(list(all_forms), [])

        with (
            patch(
                "scripts.skills.extract_to_llm_source._run_form_approval_gate",
                side_effect=_fake_gate,
            ),
            patch(
                "scripts.skills.extract_to_llm_source._acquire_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source._release_pipeline_lock_for_skill",
            ),
            patch(
                "scripts.skills.extract_to_llm_source.check_forms_manifest",
            ),
            patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0),
            ),
            patch(
                "scripts.skills.extract_to_llm_source.iter_dataset_phi_ledger_paths",
                return_value=[],
            ),
            # iter_dataset_phi_ledger_paths returns [] → exits EXIT_LEDGER_HASH_NULL (3)
            # before the snapshot step — that's fine, we only care about the gate call.
            patch(
                "scripts.utils.run_context.resolve_run_id",
                return_value="run_resume002",
            ),
            patch(
                "scripts.utils.run_context.scan_for_in_progress_scrubs",
                return_value=[],
            ),
        ):
            main(["run", "--study", STUDY, "--resume-held"])

        assert captured_selected, "Approval gate was never called"
        selected = captured_selected[0]

        # All three forms must be present — held AND previously-approved
        assert selected == expected_union, (
            f"Gate must receive the full surviving set {expected_union!r} "
            f"(prior_approved ∪ prior_held), got: {selected!r}"
        )
        # Explicit regression guards against each component
        assert held_form in selected, (
            f"Held form {held_form!r} must be in gate selected_forms"
        )
        for prev_approved in approved_forms:
            assert prev_approved in selected, (
                f"Previously-approved form {prev_approved!r} must be included in the "
                f"surviving set passed to the gate (omitting it would cause data loss): "
                f"{selected!r}"
            )
