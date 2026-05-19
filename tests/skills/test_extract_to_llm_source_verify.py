"""Tests for the verify subcommand — 12-assertion verifier.

Coverage
--------
A. Happy path: synthetic study setup with all 12 conditions satisfied → exit 0,
   verifier_report.json has all 12 "pass", status.json updated with
   verifier_passed: true.

B. Failure-injection fixtures — one test per failure mode:
   - Assertion 1 fail: missing _forms_manifest.yaml → EXIT_MANIFEST_MISMATCH (2)
   - Assertion 2 fail: manifest+datasets mismatch → EXIT_MANIFEST_MISMATCH (2)
   - Assertion 3 fail: staging dir present → EXIT_DESTRUCTION_INCOMPLETE (7)
   - Assertion 4 fail: destruction_attestation.json missing → EXIT_DESTRUCTION_INCOMPLETE (7)
   - Assertion 5 fail: ledger scrub_config_hash is null → EXIT_LEDGER_HASH_NULL (3)
   - Assertion 5 fail: scrub_config_hash mismatch → EXIT_LEDGER_HASH_NULL (3)
   - Assertion 6 fail: .NO_LLM_ZONE sentinel missing → EXIT_LEDGER_HASH_NULL (3)
   - Assertion 7 fail: quarantine dir present under tmp/ → EXIT_QUARANTINE_NON_EMPTY (4)
   - Assertion 8 fail: llm_source/ file contains PHI-like content → EXIT_VERIFIER_FAIL (5)
   - Assertion 9 fail: llm_source/ JSONL contains extraction_utc → EXIT_VERIFIER_FAIL (5)
   - Assertion 10 fail: required form JSONL missing → EXIT_MANIFEST_MISMATCH (2)
   - Assertion 11 fail: pipeline lock file present → EXIT_NEEDS_ADVICE (6)

C. --run argument: explicit --run uses that run_id; absent defaults to most recent.

D. --run absent with empty runs/ → EXIT_NEEDS_ADVICE (6).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import yaml

import scripts.skills.extract_to_llm_source as skill_mod
from scripts.skills.extract_to_llm_source import (
    EXIT_DESTRUCTION_INCOMPLETE,
    EXIT_LEDGER_HASH_NULL,
    EXIT_MANIFEST_MISMATCH,
    EXIT_NEEDS_ADVICE,
    EXIT_OK,
    EXIT_QUARANTINE_NON_EMPTY,
    EXIT_VERIFIER_FAIL,
    main,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUDY = "Test-Verifier"
RUN_ID = "run_v001"
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
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


def _compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_phi_scrub_yaml(phi_scrub_path: Path, content: bytes = b"scrub_config: test") -> None:
    """Write a fake phi_scrub.yaml and return its SHA-256."""
    phi_scrub_path.parent.mkdir(parents=True, exist_ok=True)
    phi_scrub_path.write_bytes(content)


def _make_valid_manifest(study_dir: Path, required_forms: list[str]) -> None:
    """Write a valid _forms_manifest.yaml with the given required forms."""
    manifest = {"required": required_forms, "optional": [], "reject": []}
    manifest_path = study_dir / "_forms_manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.dump(manifest), encoding="utf-8")


def _make_datasets_dir(datasets_dir: Path, forms: list[str]) -> None:
    """Create the datasets dir with stub .xlsx files for each form."""
    datasets_dir.mkdir(parents=True, exist_ok=True)
    for form in forms:
        (datasets_dir / form).write_bytes(b"stub-xlsx")


def _make_valid_ledger(
    audit_dir: Path, scrub_config_hash: str, run_id: str = RUN_ID
) -> None:
    """Write a valid phi_handling_ledger.as_written.json."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    ledger = {
        "run_id": run_id,
        "scrub_config_hash": scrub_config_hash,
        "input_dataset_hash": "abc123deadbeef",
    }
    (audit_dir / "phi_handling_ledger.as_written.json").write_text(
        json.dumps(ledger), encoding="utf-8"
    )


def _make_no_llm_zone(audit_dir: Path) -> None:
    """Create the .NO_LLM_ZONE sentinel."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / ".NO_LLM_ZONE").write_text("", encoding="utf-8")


def _make_valid_attestation(run_dir: Path, run_id: str = RUN_ID) -> None:
    """Write a valid destruction_attestation.json."""
    run_dir.mkdir(parents=True, exist_ok=True)
    attest = {
        "run_id": run_id,
        "study": STUDY,
        "started_utc": _iso_now(),
        "completed_utc": _iso_now(),
        "removed_paths": ["file.jsonl"],
        "files_destroyed": 1,
        "cryptographic_erasure": False,
        "apfs_cow_disclaimer": "test",
    }
    (run_dir / "destruction_attestation.json").write_text(
        json.dumps(attest), encoding="utf-8"
    )


def _make_llm_source_dir(
    llm_source_dir: Path,
    forms: list[str],
    *,
    extra_keys: dict[str, Any] | None = None,
) -> None:
    """Create llm_source/datasets/ with one clean JSONL per form.

    extra_keys: if provided, inject those keys into every row.
    """
    datasets_out = llm_source_dir / "datasets"
    datasets_out.mkdir(parents=True, exist_ok=True)
    for form in forms:
        stem = form.replace(".xlsx", "")
        jsonl_name = f"{stem}.jsonl"
        row: dict[str, Any] = {"col_a": "val_a", "col_b": "val_b"}
        if extra_keys:
            row.update(extra_keys)
        (datasets_out / jsonl_name).write_text(
            json.dumps(row) + "\n", encoding="utf-8"
        )


def _make_valid_status_json(run_dir: Path, run_id: str = RUN_ID) -> None:
    """Write a minimal status.json (verifier_passed = None initially)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": run_id,
        "study": STUDY,
        "exit_code": 0,
        "started_utc": _iso_now(),
        "completed_utc": _iso_now(),
        "verifier_passed": None,
    }
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")


def _build_happy_study(
    tmp_path: Path,
    *,
    forms: list[str] | None = None,
    run_id: str = RUN_ID,
) -> dict[str, Path]:
    """Build a complete synthetic study hierarchy that satisfies all 12 assertions.

    Returns a dict of named paths for easy mutation in failure-injection tests.
    """
    if forms is None:
        forms = ["form_a.xlsx", "form_b.xlsx"]

    # Directory layout
    study_dir = tmp_path / "data" / "raw" / STUDY
    datasets_dir = study_dir / "datasets"
    phi_scrub_path = tmp_path / "scripts" / "security" / "phi_scrub.yaml"
    study_output_dir = tmp_path / "output" / STUDY
    audit_dir = study_output_dir / "audit"
    run_dir = study_output_dir / "runs" / run_id
    llm_source_dir = study_output_dir / "llm_source"

    # a. phi_scrub.yaml
    _make_phi_scrub_yaml(phi_scrub_path)
    scrub_hash = _compute_sha256(phi_scrub_path)

    # b. _forms_manifest.yaml + datasets dir
    _make_valid_manifest(study_dir, forms)
    _make_datasets_dir(datasets_dir, forms)

    # c. ledger + sentinel
    _make_valid_ledger(audit_dir, scrub_hash, run_id=run_id)
    _make_no_llm_zone(audit_dir)

    # d. destruction attestation
    _make_valid_attestation(run_dir, run_id=run_id)

    # e. llm_source/datasets/ JSONL files (one per required form)
    _make_llm_source_dir(llm_source_dir, forms)

    # f. status.json
    _make_valid_status_json(run_dir, run_id=run_id)

    # g. NO staging dir, NO quarantine, NO lock file

    return {
        "study_dir": study_dir,
        "datasets_dir": datasets_dir,
        "phi_scrub_path": phi_scrub_path,
        "audit_dir": audit_dir,
        "run_dir": run_dir,
        "llm_source_dir": llm_source_dir,
        "study_output_dir": study_output_dir,
    }


def _make_args(
    study: str = STUDY, run_id: str | None = RUN_ID
) -> SimpleNamespace:
    return SimpleNamespace(subcommand="verify", study=study, run_id=run_id)


# ---------------------------------------------------------------------------
# A. Happy path
# ---------------------------------------------------------------------------


class TestVerifyHappyPath:
    def test_exits_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_OK

    def test_report_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        assert report_path.exists()

    def test_report_has_12_assertions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        report = json.loads(report_path.read_text())
        assert len(report["assertions"]) == 12

    def test_all_assertions_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        report = json.loads(report_path.read_text())
        for a in report["assertions"]:
            assert a["result"] == "pass", f"Assertion {a['n']} ({a['name']}) should pass: {a['detail']}"

    def test_overall_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        report = json.loads(report_path.read_text())
        assert report["overall"] == "pass"
        assert report["exit_code"] == 0

    def test_status_json_updated_with_verifier_passed_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        status_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "status.json"
        )
        status = json.loads(status_path.read_text())
        assert status["verifier_passed"] is True

    def test_report_shape_is_valid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        report = json.loads(report_path.read_text())
        assert "run_id" in report
        assert "study" in report
        assert "checked_utc" in report
        assert "assertions" in report
        assert "overall" in report
        assert "exit_code" in report
        for a in report["assertions"]:
            assert "n" in a
            assert "name" in a
            assert "result" in a
            assert "detail" in a


# ---------------------------------------------------------------------------
# B. Failure-injection tests (stop at first failing assertion)
# ---------------------------------------------------------------------------


class TestVerifyFailures:
    # --- Assertion 1: _forms_manifest.yaml missing ---
    def test_assertion1_manifest_missing_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Remove the manifest
        (paths["study_dir"] / "_forms_manifest.yaml").unlink()
        # Also need manifest to fail parse — absence makes check_forms_manifest return {}
        # For assertion 1 to FAIL we need the manifest to not exist and not parse as dict.
        # Per spec: assertion 1 checks yaml.safe_load returns a dict.
        # If manifest absent, check_forms_manifest warns and returns {} — assertion 1
        # should check the manifest file EXISTS and parses.
        # Write an invalid YAML manifest to trigger a parse error:
        (paths["study_dir"] / "_forms_manifest.yaml").write_text(
            ": invalid: yaml: [", encoding="utf-8"
        )
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_MANIFEST_MISMATCH

    def test_assertion1_manifest_absent_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        (paths["study_dir"] / "_forms_manifest.yaml").unlink()
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_MANIFEST_MISMATCH

    # --- Assertion 2: manifest reconciliation fails ---
    def test_assertion2_manifest_mismatch_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Remove a required form to trigger mismatch
        (paths["datasets_dir"] / "form_a.xlsx").unlink()
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_MANIFEST_MISMATCH

    def test_assertion2_fail_detail_in_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        (paths["datasets_dir"] / "form_a.xlsx").unlink()
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        report = json.loads(report_path.read_text())
        failed = [a for a in report["assertions"] if a["result"] == "fail"]
        assert len(failed) >= 1
        assert failed[0]["detail"] != ""

    # --- Assertion 3: staging dir present ---
    def test_assertion3_staging_present_exits_7(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        staging_dir = tmp_path / "tmp" / STUDY
        staging_dir.mkdir(parents=True, exist_ok=True)
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_DESTRUCTION_INCOMPLETE

    # --- Assertion 4: destruction_attestation.json missing ---
    def test_assertion4_attestation_missing_exits_7(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        (paths["run_dir"] / "destruction_attestation.json").unlink()
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_DESTRUCTION_INCOMPLETE

    def test_assertion4_attestation_missing_required_field_exits_7(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Write attestation without a required field
        incomplete = {"run_id": RUN_ID, "study": STUDY}
        (paths["run_dir"] / "destruction_attestation.json").write_text(
            json.dumps(incomplete), encoding="utf-8"
        )
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_DESTRUCTION_INCOMPLETE

    # --- Assertion 5: ledger scrub_config_hash null ---
    def test_assertion5_null_scrub_config_hash_exits_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Overwrite ledger with null scrub_config_hash
        ledger = {
            "run_id": RUN_ID,
            "scrub_config_hash": None,
            "input_dataset_hash": "abc123",
        }
        (paths["audit_dir"] / "phi_handling_ledger.as_written.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_LEDGER_HASH_NULL

    def test_assertion5_hash_mismatch_exits_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Write ledger with a WRONG scrub_config_hash
        ledger = {
            "run_id": RUN_ID,
            "scrub_config_hash": "0" * 64,  # wrong hash
            "input_dataset_hash": "abc123",
        }
        (paths["audit_dir"] / "phi_handling_ledger.as_written.json").write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_LEDGER_HASH_NULL

    # --- Assertion 6: .NO_LLM_ZONE sentinel missing ---
    def test_assertion6_sentinel_missing_exits_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        (paths["audit_dir"] / ".NO_LLM_ZONE").unlink()
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_LEDGER_HASH_NULL

    # --- Assertion 7: quarantine dir present ---
    def test_assertion7_quarantine_under_tmp_exits_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Quarantine dir under tmp/ (but NOT under tmp/STUDY/ which is the staging dir).

        Note: tmp/STUDY/ IS the staging dir, so planting quarantine there would
        trigger assertion 3 (staging present) before assertion 7. We use
        output/{STUDY}/quarantine/ which is only scanned by assertion 7.
        """
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Use output dir to avoid triggering assertion 3 (staging dir check)
        quarantine = paths["study_output_dir"] / "sub" / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / "phi.jsonl").write_bytes(b"data")
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_QUARANTINE_NON_EMPTY

    def test_assertion7_quarantine_under_output_exits_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        quarantine = paths["study_output_dir"] / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / "phi.jsonl").write_bytes(b"data")
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_QUARANTINE_NON_EMPTY

    # --- Assertion 8: PHI pattern match in llm_source/ ---
    def test_assertion8_phi_match_in_llm_source_exits_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Plant a fake Aadhaar number in a JSONL
        phi_row = {"subject_id": "1234 5678 9012"}  # Aadhaar pattern
        llm_jsonl = paths["llm_source_dir"] / "datasets" / "form_a.jsonl"
        llm_jsonl.write_text(json.dumps(phi_row) + "\n", encoding="utf-8")
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_VERIFIER_FAIL

    def test_assertion8_report_detail_does_not_include_raw_phi(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The detail string must name file+line+pattern, NOT the matched value."""
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        aadhaar = "1234 5678 9012"
        phi_row = {"subject_id": aadhaar}
        llm_jsonl = paths["llm_source_dir"] / "datasets" / "form_a.jsonl"
        llm_jsonl.write_text(json.dumps(phi_row) + "\n", encoding="utf-8")
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        report = json.loads(report_path.read_text())
        failed = [a for a in report["assertions"] if a["result"] == "fail"]
        assert failed
        # The raw matched string must NOT appear in the detail
        detail = failed[0]["detail"]
        assert aadhaar not in detail, (
            f"Raw PHI value should not appear in detail: {detail!r}"
        )

    # --- Assertion 9: determinism (extraction_utc in llm_source/) ---
    def test_assertion9_extraction_utc_exits_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        bad_row = {"col_a": "val", "extraction_utc": "2026-01-01T00:00:00+00:00"}
        llm_jsonl = paths["llm_source_dir"] / "datasets" / "form_a.jsonl"
        llm_jsonl.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_VERIFIER_FAIL

    def test_assertion9_run_id_in_llm_source_exits_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        bad_row = {"col_a": "val", "run_id": RUN_ID}
        llm_jsonl = paths["llm_source_dir"] / "datasets" / "form_b.jsonl"
        llm_jsonl.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_VERIFIER_FAIL

    def test_assertion9_generated_utc_in_llm_source_exits_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        bad_row = {"col_a": "val", "generated_utc": "2026-01-01T00:00:00+00:00"}
        llm_jsonl = paths["llm_source_dir"] / "datasets" / "form_a.jsonl"
        llm_jsonl.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_VERIFIER_FAIL

    # --- Assertion 10: required form JSONL missing from llm_source/ ---
    def test_assertion10_missing_required_jsonl_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Remove one required form's JSONL
        (paths["llm_source_dir"] / "datasets" / "form_a.jsonl").unlink()
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_MANIFEST_MISMATCH

    # --- Assertion 11: pipeline lock file present ---
    def test_assertion11_lock_file_present_exits_6(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path)
        # Create the lock file
        tmp_dir = tmp_path / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        lock_file = tmp_dir / f".{STUDY}.pipeline.lock"
        lock_file.write_text(f"pid={os.getpid()}\nstudy={STUDY}\n", encoding="utf-8")
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_NEEDS_ADVICE

    # --- Skipped assertions in report after first failure ---
    def test_skipped_assertions_after_first_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        paths = _build_happy_study(tmp_path)
        # Trigger assertion 1 fail
        (paths["study_dir"] / "_forms_manifest.yaml").unlink()
        main(["verify", "--study", STUDY, "--run", RUN_ID])
        report_path = (
            tmp_path / "output" / STUDY / "runs" / RUN_ID / "verifier_report.json"
        )
        report = json.loads(report_path.read_text())
        results = [a["result"] for a in report["assertions"]]
        assert "fail" in results
        assert "skipped" in results
        # No assertion after the first fail should be "pass"
        first_fail_idx = results.index("fail")
        for r in results[first_fail_idx + 1 :]:
            assert r == "skipped"


# ---------------------------------------------------------------------------
# C. --run argument handling
# ---------------------------------------------------------------------------


class TestRunArgument:
    def test_explicit_run_id_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _build_happy_study(tmp_path, run_id=RUN_ID)
        rc = main(["verify", "--study", STUDY, "--run", RUN_ID])
        assert rc == EXIT_OK

    def test_no_run_uses_most_recent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        # Create two runs; most recent (by name sort desc) should be used
        run_id_old = "run_2026_01_01"
        run_id_new = "run_2026_06_01"
        _build_happy_study(tmp_path, run_id=run_id_old)
        _build_happy_study(tmp_path, run_id=run_id_new)
        # Remove --run so verifier defaults to most recent
        rc = main(["verify", "--study", STUDY])
        assert rc == EXIT_OK
        # Verify the NEW run_id's report was written
        report_path = (
            tmp_path / "output" / STUDY / "runs" / run_id_new / "verifier_report.json"
        )
        assert report_path.exists()

    def test_no_run_empty_runs_dir_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        # Create minimal required dirs but empty runs/
        runs_dir = tmp_path / "output" / STUDY / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        rc = main(["verify", "--study", STUDY])
        assert rc == EXIT_NEEDS_ADVICE

    def test_no_run_runs_dir_absent_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        # Don't create runs/ at all
        rc = main(["verify", "--study", STUDY])
        assert rc == EXIT_NEEDS_ADVICE
