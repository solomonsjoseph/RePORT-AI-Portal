"""Tests for the run / verify / status CLI in scripts.skills.extract_to_llm_source.

Coverage
--------
A. run happy path — synthetic study + manifest + staging; mock main.py --pipeline
   subprocess to succeed; assert exit 0, status.json written, attestation present,
   staging removed.
B. run exit-code paths:
     - in-progress token           → EXIT_NEEDS_ADVICE (6)
     - manifest mismatch           → EXIT_MANIFEST_MISMATCH (2)
     - null ledger hash            → EXIT_LEDGER_HASH_NULL (3)
     - quarantine non-empty        → EXIT_QUARANTINE_NON_EMPTY (4)
     - subprocess non-zero         → EXIT_NEEDS_ADVICE (6)
     - destruction failure         → EXIT_DESTRUCTION_INCOMPLETE (7)
C. run env-pop — REPORTALIN_ALLOW_DISABLED_SCRUB is NOT in the env passed to
   the subprocess.
D. status — returns EXIT_OK and prints the scope banner.
E. verify stub:
     - staging dir present         → EXIT_DESTRUCTION_INCOMPLETE (7)
     - staging dir absent          → EXIT_OK (0)
F. SIGINT handler — exits EXIT_DESTRUCTION_INCOMPLETE (7) without invoking
   destruction.
"""

from __future__ import annotations

import json
import os
import signal
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import scripts.skills.extract_to_llm_source as skill_mod
from scripts.skills.extract_to_llm_source import (
    EXIT_DESTRUCTION_INCOMPLETE,
    EXIT_LEDGER_HASH_NULL,
    EXIT_MANIFEST_MISMATCH,
    EXIT_NEEDS_ADVICE,
    EXIT_OK,
    EXIT_QUARANTINE_NON_EMPTY,
    main,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

STUDY = "Test-Study"


def _make_args(subcommand: str, study: str = STUDY, run_id: str | None = None) -> Any:
    """Build a minimal argparse.Namespace for a subcommand."""
    ns: dict[str, Any] = {"subcommand": subcommand}
    if subcommand in {"run", "verify"}:
        ns["study"] = study
    if subcommand == "verify":
        ns["run_id"] = run_id
    return SimpleNamespace(**ns)


def _patch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect config path constants to tmp_path so tests are hermetic."""
    import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
    monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
    monkeypatch.setattr(config, "DATASETS_DIR", tmp_path / f"data/raw/{STUDY}/datasets", raising=False)


def _write_valid_ledger(output_dir: Path) -> None:
    """Write a ledger with non-null hashes."""
    audit_dir = output_dir / STUDY / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    ledger = {
        "scrub_config_hash": "abc123",
        "input_dataset_hash": "def456",
    }
    ledger_path = audit_dir / "phi_handling_ledger.as_written.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")


def _make_staging(staging_dir: Path) -> None:
    """Create a non-empty staging directory."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "dummy.jsonl").write_bytes(b"data")


def _make_datasets_dir(datasets_dir: Path) -> None:
    """Create the datasets directory (no files — manifest absent → no raise)."""
    datasets_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Shared subprocess mock that succeeds
# ---------------------------------------------------------------------------


def _subprocess_ok(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(returncode=0)


# ---------------------------------------------------------------------------
# D. status subcommand
# ---------------------------------------------------------------------------


class TestStatusSubcommand:
    def test_exits_ok(self) -> None:
        rc = main(["status"])
        assert rc == EXIT_OK

    def test_banner_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["status"])
        out = capsys.readouterr().out
        assert "extract_to_llm_source" in out
        assert "HIPAA Safe Harbor" in out
        assert "Exit codes:" in out

    def test_all_exit_codes_listed_in_banner(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["status"])
        out = capsys.readouterr().out
        for code in (0, 2, 3, 4, 5, 6, 7):
            assert str(code) in out, f"Exit code {code} missing from status banner"


# ---------------------------------------------------------------------------
# E. verify stub
# ---------------------------------------------------------------------------


class TestVerifyStub:
    def test_staging_absent_exits_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        # staging dir does NOT exist
        rc = main(["verify", "--study", STUDY])
        assert rc == EXIT_OK

    def test_staging_present_exits_destruction_incomplete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        staging_dir = tmp_path / "tmp" / STUDY
        staging_dir.mkdir(parents=True)
        rc = main(["verify", "--study", STUDY])
        assert rc == EXIT_DESTRUCTION_INCOMPLETE


# ---------------------------------------------------------------------------
# A. run happy path
# ---------------------------------------------------------------------------


class TestRunHappyPath:
    def test_exits_ok_and_writes_status_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _write_valid_ledger(tmp_path / "output")
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        # Staging must be present before subprocess, absent after destruction.
        # We mock destruction too so we don't need zone markers.
        staging_dir = tmp_path / "tmp" / STUDY
        _make_staging(staging_dir)

        run_id = "run_test001"

        monkeypatch.setenv("REPORTAL_RUN_ID", run_id)

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        def _fake_destroy(**kwargs: Any) -> Path:
            # Remove staging so post-destruction checks pass.
            import shutil
            shutil.rmtree(str(kwargs["staging_dir"]), ignore_errors=True)
            attest_path = kwargs["output_dir"] / "runs" / kwargs["run_id"] / "destruction_attestation.json"
            attest_path.parent.mkdir(parents=True, exist_ok=True)
            attest_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
            return attest_path

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "destroy_staging_and_attest", _fake_destroy),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_OK
        status_path = tmp_path / "output" / STUDY / "runs" / run_id / "status.json"
        assert status_path.exists(), "status.json must be written on success"
        status_data = json.loads(status_path.read_text())
        assert status_data["exit_code"] == EXIT_OK
        assert status_data["run_id"] == run_id
        assert status_data["study"] == STUDY
        assert status_data["verifier_passed"] is None
        assert status_data["ledger_hash_present"] is True

    def test_staging_removed_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _write_valid_ledger(tmp_path / "output")
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        staging_dir = tmp_path / "tmp" / STUDY
        _make_staging(staging_dir)
        run_id = "run_test002"
        monkeypatch.setenv("REPORTAL_RUN_ID", run_id)

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        import shutil

        def _fake_destroy(**kwargs: Any) -> Path:
            shutil.rmtree(str(kwargs["staging_dir"]), ignore_errors=True)
            attest_path = kwargs["output_dir"] / "runs" / kwargs["run_id"] / "destruction_attestation.json"
            attest_path.parent.mkdir(parents=True, exist_ok=True)
            attest_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
            return attest_path

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "destroy_staging_and_attest", _fake_destroy),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        ):
            main(["run", "--study", STUDY])

        assert not staging_dir.exists(), "staging dir should be removed after successful run"

    def test_attestation_present_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _write_valid_ledger(tmp_path / "output")
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        staging_dir = tmp_path / "tmp" / STUDY
        _make_staging(staging_dir)
        run_id = "run_test003"
        monkeypatch.setenv("REPORTAL_RUN_ID", run_id)

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        import shutil

        def _fake_destroy(**kwargs: Any) -> Path:
            shutil.rmtree(str(kwargs["staging_dir"]), ignore_errors=True)
            attest_path = kwargs["output_dir"] / "runs" / kwargs["run_id"] / "destruction_attestation.json"
            attest_path.parent.mkdir(parents=True, exist_ok=True)
            attest_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
            return attest_path

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "destroy_staging_and_attest", _fake_destroy),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        ):
            main(["run", "--study", STUDY])

        attest_path = (
            tmp_path / "output" / STUDY / "runs" / run_id / "destruction_attestation.json"
        )
        assert attest_path.exists(), "destruction attestation must exist after success"


# ---------------------------------------------------------------------------
# B. run exit-code paths
# ---------------------------------------------------------------------------


class TestRunExitCodes:
    def _base_patches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> tuple[Any, Any]:
        """Return context managers for lock acquire/release mocks."""
        _patch_config(monkeypatch, tmp_path)

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        return _fake_acquire, _fake_release

    def test_in_progress_token_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_acquire, _fake_release = self._base_patches(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        # Plant a scrub.in_progress token.
        runs_dir = tmp_path / "output" / STUDY / "runs"
        token_dir = runs_dir / "run_prior"
        token_dir.mkdir(parents=True)
        (token_dir / "scrub.in_progress").write_bytes(b"")

        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_NEEDS_ADVICE

    def test_manifest_mismatch_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_acquire, _fake_release = self._base_patches(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        from scripts.extraction.dataset_pipeline import ManifestMismatchError

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(
                skill_mod,
                "check_forms_manifest",
                side_effect=ManifestMismatchError("rejected form found: bad.xlsx"),
            ),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_MANIFEST_MISMATCH

    def test_subprocess_nonzero_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_acquire, _fake_release = self._base_patches(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=1)),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_NEEDS_ADVICE

    def test_null_ledger_hash_exits_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_acquire, _fake_release = self._base_patches(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        # Write a ledger with null hashes.
        audit_dir = tmp_path / "output" / STUDY / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "phi_handling_ledger.as_written.json").write_text(
            json.dumps({"scrub_config_hash": None, "input_dataset_hash": None}),
            encoding="utf-8",
        )
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_LEDGER_HASH_NULL

    def test_missing_ledger_exits_3(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_acquire, _fake_release = self._base_patches(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        # Do NOT write ledger file — it's absent.
        (tmp_path / "output" / STUDY / "audit").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_LEDGER_HASH_NULL

    def test_quarantine_non_empty_exits_4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_acquire, _fake_release = self._base_patches(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        _write_valid_ledger(tmp_path / "output")
        # Plant a file in quarantine.
        quarantine_dir = tmp_path / "tmp" / STUDY / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        (quarantine_dir / "leaked.jsonl").write_bytes(b"phi")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_QUARANTINE_NON_EMPTY

    def test_destruction_failure_exits_7(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_acquire, _fake_release = self._base_patches(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        _write_valid_ledger(tmp_path / "output")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        from scripts.skills.extract_to_llm_source import DestructionIncompleteError

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
            patch.object(
                skill_mod,
                "destroy_staging_and_attest",
                side_effect=DestructionIncompleteError("still there"),
            ),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_DESTRUCTION_INCOMPLETE

    def test_lock_failure_exits_needs_advice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_x")

        def _fake_release() -> None:
            pass

        with (
            patch.object(
                skill_mod,
                "_acquire_pipeline_lock_for_skill",
                side_effect=RuntimeError("lock busy"),
            ),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_NEEDS_ADVICE


# ---------------------------------------------------------------------------
# C. env-pop — REPORTALIN_ALLOW_DISABLED_SCRUB must not reach subprocess
# ---------------------------------------------------------------------------


class TestEnvPop:
    def test_scrub_bypass_env_var_removed_from_subprocess_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _write_valid_ledger(tmp_path / "output")
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_envpop")
        # Plant the bypass variable in the current process environment.
        monkeypatch.setenv("REPORTALIN_ALLOW_DISABLED_SCRUB", "1")

        captured_env: dict[str, str] = {}

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        def _capturing_subprocess_run(cmd: Any, env: dict[str, str], **kwargs: Any) -> Any:
            captured_env.update(env)
            return SimpleNamespace(returncode=0)

        import shutil

        def _fake_destroy(**kwargs: Any) -> Path:
            shutil.rmtree(str(kwargs["staging_dir"]), ignore_errors=True)
            attest_path = kwargs["output_dir"] / "runs" / kwargs["run_id"] / "destruction_attestation.json"
            attest_path.parent.mkdir(parents=True, exist_ok=True)
            attest_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
            return attest_path

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "destroy_staging_and_attest", _fake_destroy),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", side_effect=_capturing_subprocess_run),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_OK
        assert "REPORTALIN_ALLOW_DISABLED_SCRUB" not in captured_env, (
            "REPORTALIN_ALLOW_DISABLED_SCRUB must NOT be passed to the subprocess"
        )

    def test_run_id_is_propagated_to_subprocess_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_config(monkeypatch, tmp_path)
        _write_valid_ledger(tmp_path / "output")
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        run_id = "run_envcheck"
        monkeypatch.setenv("REPORTAL_RUN_ID", run_id)

        captured_env: dict[str, str] = {}

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        def _capturing_subprocess_run(cmd: Any, env: dict[str, str], **kwargs: Any) -> Any:
            captured_env.update(env)
            return SimpleNamespace(returncode=0)

        import shutil

        def _fake_destroy(**kwargs: Any) -> Path:
            shutil.rmtree(str(kwargs["staging_dir"]), ignore_errors=True)
            attest_path = kwargs["output_dir"] / "runs" / kwargs["run_id"] / "destruction_attestation.json"
            attest_path.parent.mkdir(parents=True, exist_ok=True)
            attest_path.write_text(json.dumps({"stub": True}), encoding="utf-8")
            return attest_path

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "destroy_staging_and_attest", _fake_destroy),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", side_effect=_capturing_subprocess_run),
        ):
            main(["run", "--study", STUDY])

        assert captured_env.get("REPORTAL_RUN_ID") == run_id


# ---------------------------------------------------------------------------
# F. SIGINT handler — exits 7 without invoking destruction
# ---------------------------------------------------------------------------


class TestSignalHandler:
    def test_sigint_during_subprocess_exits_destruction_incomplete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate SIGINT raised during the subprocess call by raising _SkillInterrupted
        from within the subprocess mock."""
        _patch_config(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_sig")

        destroy_called = False

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        def _fake_destroy(**kwargs: Any) -> Path:
            nonlocal destroy_called
            destroy_called = True
            raise AssertionError("destroy_staging_and_attest must NOT be called on interrupt")

        def _subprocess_raises(*_args: Any, **_kwargs: Any) -> None:
            raise skill_mod._SkillInterrupted("simulated SIGINT")  # noqa: SLF001

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "destroy_staging_and_attest", _fake_destroy),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", side_effect=_subprocess_raises),
        ):
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_DESTRUCTION_INCOMPLETE
        assert not destroy_called, "destruction must not be invoked on SIGINT"

    def test_real_sigint_via_os_kill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Send SIGINT to the current process while the skill is installing handlers.

        This test verifies that the handler is correctly registered and that the
        _SkillInterrupted exception bubbles up to the try/except in _cmd_run.
        Uses a threading timer to fire SIGINT shortly after the subprocess mock
        blocks, then unblocks subprocess to let cleanup proceed.
        """
        _patch_config(monkeypatch, tmp_path)
        _make_datasets_dir(tmp_path / f"data/raw/{STUDY}/datasets")
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_sigkill")

        event = threading.Event()

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        def _blocking_subprocess(*_args: Any, **_kwargs: Any) -> Any:
            # Signal that subprocess has been called, then wait briefly.
            event.set()
            import time
            time.sleep(0.5)
            return SimpleNamespace(returncode=0)

        # Fire SIGINT 0.1 s after subprocess starts.
        def _send_sigint() -> None:
            event.wait(timeout=2)
            os.kill(os.getpid(), signal.SIGINT)

        timer = threading.Thread(target=_send_sigint, daemon=True)

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", side_effect=_blocking_subprocess),
        ):
            timer.start()
            rc = main(["run", "--study", STUDY])

        assert rc == EXIT_DESTRUCTION_INCOMPLETE


# ---------------------------------------------------------------------------
# G. Study-mismatch regression — --study arg must win over config.STUDY_NAME
# ---------------------------------------------------------------------------


class TestStudyMismatch:
    """Regression tests: when --study differs from config.STUDY_NAME, every
    study-scoped path (lock file, manifest directory) must be keyed on the
    explicit --study value, not on config.STUDY_NAME.
    """

    def test_lockfile_uses_arg_study_not_config_study(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lock-file name must reflect --study foo, not config.STUDY_NAME bar."""
        import config

        # config.STUDY_NAME = "bar"; --study foo
        monkeypatch.setattr(config, "STUDY_NAME", "bar", raising=False)
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
        monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)

        (tmp_path / "data" / "raw" / "foo" / "datasets").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_mismatch")

        acquired_with: list[str] = []

        def _capturing_acquire(study: str) -> None:
            acquired_with.append(study)

        def _fake_release() -> None:
            pass

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _capturing_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", return_value={}),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=1)),
        ):
            main(["run", "--study", "foo"])

        assert acquired_with == ["foo"], (
            f"lock was acquired for {acquired_with!r}, expected ['foo']; "
            "config.STUDY_NAME='bar' must not pollute the lock key"
        )

    def test_manifest_path_uses_arg_study_not_config_study(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The datasets_dir passed to check_forms_manifest must be under
        data/raw/foo/datasets (the --study value), not data/raw/bar/datasets
        (config.STUDY_NAME).
        """
        import config

        monkeypatch.setattr(config, "STUDY_NAME", "bar", raising=False)
        monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output", raising=False)
        monkeypatch.setattr(config, "TMP_DIR", tmp_path / "tmp", raising=False)
        monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path / "data" / "raw", raising=False)

        (tmp_path / "data" / "raw" / "foo" / "datasets").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("REPORTAL_RUN_ID", "run_mismatch2")

        checked_paths: list[Path] = []

        def _capturing_manifest(datasets_dir: Path) -> dict:  # type: ignore[return]
            checked_paths.append(datasets_dir)

        def _fake_acquire(_study: str) -> None:
            pass

        def _fake_release() -> None:
            pass

        with (
            patch.object(skill_mod, "_acquire_pipeline_lock_for_skill", _fake_acquire),
            patch.object(skill_mod, "_release_pipeline_lock_for_skill", _fake_release),
            patch.object(skill_mod, "check_forms_manifest", side_effect=_capturing_manifest),
            patch("subprocess.run", return_value=SimpleNamespace(returncode=1)),
        ):
            main(["run", "--study", "foo"])

        assert len(checked_paths) == 1
        checked = checked_paths[0]
        assert "foo" in checked.parts, (
            f"manifest checked at {checked} — expected a path under 'foo', "
            "not 'bar' (config.STUDY_NAME)"
        )
        assert "bar" not in checked.parts, (
            f"manifest checked at {checked} — config.STUDY_NAME='bar' "
            "must not appear in the manifest path"
        )
