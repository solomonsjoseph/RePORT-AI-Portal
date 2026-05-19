"""raw-Excel → PHI-clean llm_source/ skill — CLI + destruction helper.

This module serves two roles:

1. **Destruction helper** (:func:`destroy_staging_and_attest`) — the
   foundational helper from P0.6 that securely removes the per-study AMBER-zone
   staging directory after a successful publish and emits a destruction-
   attestation JSON.

2. **Cross-LLM canonical CLI** — the ``run / verify / status`` argparse
   surface added in P3.1.  This is the single entry point that drives the
   full raw-Excel → PHI-clean ``llm_source/`` pipeline for any LLM agent
   (Claude Code, ChatGPT, Gemini, Cursor …) via a plain subprocess call.

Atomicity dependency
--------------------
``destroy_staging_and_attest`` is only called after the publish step has
completed.  The publish step relies on :func:`main._publish_leg`
(``main.py:_publish_leg``) being atomic: that function uses a sibling temp
directory under ``trio_dir.parent / ".llm_source.publishing"`` and a single
``os.rename`` syscall to promote the populated tree to its final
``llm_source/`` location, ensuring that ``llm_source/`` is either absent or
fully populated after any crash — never half.  The rename site is at the line
labelled ``atomic: rename site (cross-fs path)`` in ``_publish_leg``.  This
wrapper must not be invoked unless that rename has already been confirmed
durable.

IRB-grade context
-----------------
* HIPAA §164.310(c) — device + media controls: staged PHI is overwritten
  with random bytes and fsynced before unlink, then the tree is verified gone.
* DPDPA 2023 §8(7) — erasure: the attestation record provides evidence of
  deletion.
* APFS copy-on-write caveat: filesystem-level overwrite is performed; prior
  APFS snapshots or unreferenced blocks may persist until TRIM.  Skill scope
  is operational untraceability, not forensic erasure.

Exit codes (single source of truth)
------------------------------------
EXIT_OK                  = 0   — success
EXIT_MANIFEST_MISMATCH   = 2   — missing required / unknown / reject form
EXIT_LEDGER_HASH_NULL    = 3   — audit ledger hash is null or sentinel missing
EXIT_QUARANTINE_NON_EMPTY = 4  — quarantine directory non-empty
EXIT_VERIFIER_FAIL       = 5   — verifier assertion failed
EXIT_NEEDS_ADVICE        = 6   — paused — operator inspection required
EXIT_DESTRUCTION_INCOMPLETE = 7 — destruction incomplete

Code 1 (generic error) is reserved for unexpected exceptions.

Public API
----------
* :data:`EXIT_OK`
* :data:`EXIT_MANIFEST_MISMATCH`
* :data:`EXIT_LEDGER_HASH_NULL`
* :data:`EXIT_QUARANTINE_NON_EMPTY`
* :data:`EXIT_VERIFIER_FAIL`
* :data:`EXIT_NEEDS_ADVICE`
* :data:`EXIT_DESTRUCTION_INCOMPLETE`
* :class:`DestructionIncompleteError`
* :func:`destroy_staging_and_attest`
* :func:`main` (argparse entry point)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.extraction.dataset_pipeline import (
    ManifestMismatchError,
    check_forms_manifest,
)
from scripts.utils.secure_staging import secure_remove_tree

__all__ = [
    "EXIT_DESTRUCTION_INCOMPLETE",
    "EXIT_LEDGER_HASH_NULL",
    "EXIT_MANIFEST_MISMATCH",
    "EXIT_NEEDS_ADVICE",
    "EXIT_OK",
    "EXIT_QUARANTINE_NON_EMPTY",
    "EXIT_VERIFIER_FAIL",
    "DestructionIncompleteError",
    "ManifestMismatchError",
    "check_forms_manifest",
    "destroy_staging_and_attest",
    "main",
]

# ---------------------------------------------------------------------------
# Exit code constants — single source of truth
# ---------------------------------------------------------------------------

EXIT_OK: int = 0
EXIT_MANIFEST_MISMATCH: int = 2
EXIT_LEDGER_HASH_NULL: int = 3
EXIT_QUARANTINE_NON_EMPTY: int = 4
EXIT_VERIFIER_FAIL: int = 5
EXIT_NEEDS_ADVICE: int = 6
EXIT_DESTRUCTION_INCOMPLETE: int = 7

# ---------------------------------------------------------------------------
# Destruction helper (P0.6) — kept verbatim
# ---------------------------------------------------------------------------

_APFS_COW_DISCLAIMER = (
    "Filesystem-level overwrite was performed via secrets.token_bytes + fsync; "
    "APFS copy-on-write means prior blocks may persist until trimmed. "
    "Skill scope is operational untraceability, not forensic erasure."
)

UTC = timezone.utc


class DestructionIncompleteError(Exception):
    """Raised when staging_dir still exists after secure_remove_tree.

    The CLI wrapper (P3.1) translates this to exit code 7.
    """


def destroy_staging_and_attest(
    *,
    study: str,
    run_id: str,
    staging_dir: Path,
    output_dir: Path,
) -> Path:
    """Securely remove staging_dir and emit a destruction-attestation JSON.

    Returns the path of the attestation file.

    Raises DestructionIncompleteError if staging_dir still exists after
    secure_remove_tree (exit code 7 for the wrapper that calls this).

    Args:
        study: Study name (e.g. "Indo-VAP"); used for paths and the
            attestation ``study`` field.
        run_id: Opaque run identifier provided by the caller; will be
            generated by the CLI wrapper in P3.1.
        staging_dir: The ``config.STUDY_STAGING_DIR / STUDY`` path to destroy.
        output_dir: The ``output/{STUDY}/`` root where
            ``runs/{run_id}/destruction_attestation.json`` should land.

    This function is ONLY invoked on a successful publish.  On any earlier
    pipeline failure the caller skips it so staging is preserved for
    inspection.  No "should I run?" guard lives here — that is the caller's
    contract.
    """
    staging_dir = Path(staging_dir)
    output_dir = Path(output_dir)

    # ------------------------------------------------------------------
    # a. Snapshot what's about to be destroyed BEFORE deletion.
    # ------------------------------------------------------------------
    removed_paths: list[str] = []
    if staging_dir.exists():
        for p in sorted(staging_dir.rglob("*")):
            if p.is_file():
                # Store relative path so the attestation is portable.
                try:
                    rel = p.relative_to(staging_dir)
                except ValueError:
                    rel = p
                removed_paths.append(str(rel))

    files_destroyed = len(removed_paths)

    # ------------------------------------------------------------------
    # b. Record destruction start timestamp.
    # ------------------------------------------------------------------
    started_utc = datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # c. Securely remove the tree.
    # ------------------------------------------------------------------
    secure_remove_tree(staging_dir)

    # ------------------------------------------------------------------
    # d. Verify the tree is gone.
    # ------------------------------------------------------------------
    if staging_dir.exists():
        raise DestructionIncompleteError(
            f"staging_dir still exists after secure_remove_tree: {staging_dir}"
        )

    # ------------------------------------------------------------------
    # e. Record destruction completion timestamp.
    # ------------------------------------------------------------------
    completed_utc = datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # f. Write attestation atomically (write to .tmp sibling, then rename).
    # ------------------------------------------------------------------
    attest_dir = output_dir / "runs" / run_id
    attest_dir.mkdir(parents=True, exist_ok=True)
    attest_path = attest_dir / "destruction_attestation.json"

    payload = {
        "apfs_cow_disclaimer": _APFS_COW_DISCLAIMER,
        "completed_utc": completed_utc,
        "cryptographic_erasure": False,
        "files_destroyed": files_destroyed,
        "removed_paths": removed_paths,
        "run_id": run_id,
        "staging_path": str(staging_dir),
        "started_utc": started_utc,
        "study": study,
    }
    serialised = json.dumps(payload, indent=2, sort_keys=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=attest_dir,
        prefix=".destruction_attestation_",
        suffix=".tmp",
    )
    try:
        try:
            os.write(tmp_fd, serialised.encode("utf-8"))
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        Path(tmp_name).replace(attest_path)
    except Exception:
        # Best-effort cleanup of the .tmp file on error.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    # ------------------------------------------------------------------
    # g. Return the attestation file path.
    # ------------------------------------------------------------------
    return attest_path


# ---------------------------------------------------------------------------
# Status banner text
# ---------------------------------------------------------------------------

_STATUS_BANNER = """\
extract_to_llm_source — skill scope and contract
=================================================

Pipeline: raw .xlsx → PHI-scrubbed llm_source/ (one study)

PHI coverage: HIPAA Safe Harbor identifiers per scripts/security/phi_scrub.yaml
              + project-specific patterns in scripts/security/phi_patterns.py
Out of scope (operator responsibility): DPDPA §16 cross-border egress,
                                        §12 right-to-erase, §8(6) breach
                                        notification, ICMR l-diversity gate.

Temp removal: operational untraceability after successful publish (APFS COW
              acknowledged in destruction attestation; not forensic erasure).

Exit codes:
  0 — ok
  2 — manifest mismatch (missing required / unknown / reject)
  3 — audit ledger hash is null or sentinel missing
  4 — quarantine directory non-empty
  5 — verifier assertion failed
  6 — needs-advice (paused — operator inspection required)
  7 — destruction incomplete
"""

# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def _cmd_status(_args: argparse.Namespace) -> int:
    """Print scope banner and exit 0."""
    print(_STATUS_BANNER, end="")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Subcommand: verify (P3.1 scaffold — P4.1 fills in remaining assertions)
# ---------------------------------------------------------------------------


def _cmd_verify(args: argparse.Namespace) -> int:
    """Run verifier assertions for the given study.

    Scaffold (P3.1): implements ONE assertion — staging-dir-must-be-absent.
    P4.1 will implement the remaining 11 assertions listed in the TODO block
    below.

    Exit codes:
        EXIT_OK (0)                    — all assertions passed
        EXIT_DESTRUCTION_INCOMPLETE (7) — staging dir unexpectedly present
    """
    import config  # imported lazily so the module is testable without full config bootstrap

    study = args.study
    staging_dir = Path(config.TMP_DIR) / study  # type: ignore[arg-type]

    # ── Assertion 1: staging-dir-must-be-absent ──────────────────────────
    if staging_dir.exists():
        print(
            f"FAIL [staging-dir-must-be-absent]: {staging_dir} exists — "
            "destruction may be incomplete.",
            file=sys.stderr,
        )
        return EXIT_DESTRUCTION_INCOMPLETE

    print(f"PASS [staging-dir-must-be-absent]: {staging_dir} is absent.")

    # TODO(P4.1): assertion 2  — phi_handling_ledger.as_written.json present
    # TODO(P4.1): assertion 3  — scrub_config_hash non-null in ledger
    # TODO(P4.1): assertion 4  — input_dataset_hash non-null in ledger
    # TODO(P4.1): assertion 5  — quarantine dir absent or empty
    # TODO(P4.1): assertion 6  — lineage_manifest.json present
    # TODO(P4.1): assertion 7  — every llm_source/ file is in lineage_manifest
    # TODO(P4.1): assertion 8  — no raw PHI columns present in published JSONL
    # TODO(P4.1): assertion 9  — destruction_attestation.json present for run
    # TODO(P4.1): assertion 10 — status.json exit_code == 0
    # TODO(P4.1): assertion 11 — status.json verifier_passed == true (set here)
    # TODO(P4.1): assertion 12 — scrub.in_progress token absent under runs/

    return EXIT_OK


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------


class _SkillInterrupted(BaseException):
    """Raised by SIGINT/SIGTERM handler to unwind the run subcommand cleanly."""


def _install_signal_handlers() -> None:
    """Install SIGINT and SIGTERM handlers that raise _SkillInterrupted.

    Restores the default SIGINT handler before raising so a subsequent
    Ctrl-C during cleanup cannot be silently swallowed by a nested handler.
    """

    def _handler(signum: int, frame: object) -> None:  # noqa: ANN001
        # Restore default so a second interrupt during cleanup propagates.
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        raise _SkillInterrupted(f"interrupted by signal {signum}")

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _restore_default_signal_handlers() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write *payload* to *path* atomically using a sibling .tmp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(payload, indent=2, sort_keys=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}_",
        suffix=".tmp",
    )
    try:
        try:
            os.write(tmp_fd, serialised.encode("utf-8"))
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        Path(tmp_name).replace(path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _acquire_pipeline_lock_for_skill(study: str) -> None:
    """Acquire the pipeline lock by delegating to main._acquire_pipeline_lock.

    Imports main lazily to avoid circular imports and to allow the test suite
    to mock ``main._acquire_pipeline_lock``.

    Raises RuntimeError (from main._acquire_pipeline_lock) if the lock is
    already held by another process.
    """
    import main as _main  # noqa: PLC0415 — lazy import intentional

    _main._acquire_pipeline_lock()  # noqa: SLF001


def _release_pipeline_lock_for_skill() -> None:
    """Release the pipeline lock via main._release_pipeline_lock."""
    import main as _main  # noqa: PLC0415

    _main._release_pipeline_lock()  # noqa: SLF001


def _cmd_run(args: argparse.Namespace) -> int:  # noqa: PLR0911, PLR0912, PLR0915
    """Drive the full raw-Excel → PHI-clean llm_source/ pipeline.

    Steps
    -----
    1. Pre-flight checks (run_id, in-progress token, lock, manifest).
    2. Install SIGINT/SIGTERM handlers.
    3. Invoke ``main.py --pipeline --study STUDY`` in a subprocess.
    4. Post-run gates (ledger hashes, quarantine).
    5. Destruction (destroy_staging_and_attest).
    6. Write status.json.
    7. Exit EXIT_OK.
    """
    import config  # lazy — avoids import at module level for testability

    from scripts.utils.run_context import (
        SCRUB_RECOVERY_MESSAGE,
        resolve_run_id,
        scan_for_in_progress_scrubs,
    )

    study = args.study
    started_utc = datetime.now(UTC).isoformat()

    # ── Step 1a: resolve run_id ────────────────────────────────────────────
    run_id = resolve_run_id()

    # ── Step 1b: scan for in-progress scrubs ──────────────────────────────
    study_runs_dir = Path(config.OUTPUT_DIR) / study / "runs"
    in_progress = scan_for_in_progress_scrubs(study_runs_dir)
    if in_progress:
        print(
            SCRUB_RECOVERY_MESSAGE.format(path=in_progress[0]),
            file=sys.stderr,
        )
        return EXIT_NEEDS_ADVICE

    # ── Step 1c: acquire pipeline lock ────────────────────────────────────
    try:
        _acquire_pipeline_lock_for_skill(study)
    except RuntimeError as exc:
        print(f"Lock error: {exc}", file=sys.stderr)
        return EXIT_NEEDS_ADVICE

    lock_held = True

    # ── Step 1d: validate forms manifest ──────────────────────────────────
    datasets_dir = Path(config.DATASETS_DIR)
    try:
        check_forms_manifest(datasets_dir)
    except ManifestMismatchError as exc:
        print(f"Manifest mismatch: {exc}", file=sys.stderr)
        _release_pipeline_lock_for_skill()
        return EXIT_MANIFEST_MISMATCH

    # ── Step 2: install signal handlers ───────────────────────────────────
    _install_signal_handlers()

    try:
        # ── Step 3: subprocess invocation of main.py --pipeline ───────────
        env = dict(os.environ)
        # Security: never propagate scrub-bypass env var to child process.
        env.pop("REPORTALIN_ALLOW_DISABLED_SCRUB", None)
        # Propagate run_id so all sub-processes share the same run identifier.
        env["REPORTAL_RUN_ID"] = run_id

        # main.py resolves study from STUDY_NAME env var (it has no --study flag).
        env["STUDY_NAME"] = study
        repo_root = Path(__file__).parent.parent.parent
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(repo_root / "main.py"), "--pipeline"],
            env=env,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"Pipeline subprocess exited with code {result.returncode}; "
                "staging preserved for inspection.",
                file=sys.stderr,
            )
            return EXIT_NEEDS_ADVICE

        # ── Step 4a: assert ledger hashes are non-null ────────────────────
        output_dir = Path(config.OUTPUT_DIR) / study
        ledger_path = output_dir / "audit" / "phi_handling_ledger.as_written.json"
        try:
            ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Ledger read error: {exc}", file=sys.stderr)
            return EXIT_LEDGER_HASH_NULL

        if not ledger_data.get("scrub_config_hash") or not ledger_data.get(
            "input_dataset_hash"
        ):
            print(
                "Ledger hash null: scrub_config_hash or input_dataset_hash is absent/null.",
                file=sys.stderr,
            )
            return EXIT_LEDGER_HASH_NULL

        # ── Step 4b: assert quarantine is empty or absent ─────────────────
        quarantine_dir = Path(config.TMP_DIR) / study / "quarantine"  # type: ignore[arg-type]
        if quarantine_dir.is_dir() and any(quarantine_dir.iterdir()):
            print(
                f"Quarantine non-empty: {quarantine_dir}",
                file=sys.stderr,
            )
            return EXIT_QUARANTINE_NON_EMPTY

        # ── Step 5: destruction ───────────────────────────────────────────
        staging_dir = Path(config.TMP_DIR) / study  # type: ignore[arg-type]
        try:
            attest_path = destroy_staging_and_attest(
                study=study,
                run_id=run_id,
                staging_dir=staging_dir,
                output_dir=output_dir,
            )
        except DestructionIncompleteError as exc:
            print(f"Destruction incomplete: {exc}", file=sys.stderr)
            return EXIT_DESTRUCTION_INCOMPLETE

        # ── Step 6: write status.json ─────────────────────────────────────
        completed_utc = datetime.now(UTC).isoformat()
        run_dir = output_dir / "runs" / run_id
        status_path = run_dir / "status.json"
        status_payload: dict[str, Any] = {
            "run_id": run_id,
            "study": study,
            "exit_code": EXIT_OK,
            "started_utc": started_utc,
            "completed_utc": completed_utc,
            "scope": "HIPAA Safe Harbor (per phi_scrub.yaml)",
            "ledger_hash_present": True,
            "destruction_attestation_path": str(attest_path),
            "verifier_passed": None,
        }
        _atomic_write_json(status_path, status_payload)

    except _SkillInterrupted:
        # SIGINT/SIGTERM — clean up lock; do NOT invoke destruction.
        print(
            "Run interrupted by signal; staging preserved. "
            "Exiting with EXIT_DESTRUCTION_INCOMPLETE.",
            file=sys.stderr,
        )
        lock_held = False
        _restore_default_signal_handlers()
        _release_pipeline_lock_for_skill()
        return EXIT_DESTRUCTION_INCOMPLETE

    finally:
        _restore_default_signal_handlers()
        if lock_held:
            _release_pipeline_lock_for_skill()
            lock_held = False

    return EXIT_OK


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract_to_llm_source",
        description=(
            "Cross-LLM canonical entry point: raw .xlsx → PHI-clean llm_source/ pipeline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ── run ────────────────────────────────────────────────────────────────
    run_p = sub.add_parser(
        "run",
        help="Drive the end-to-end pipeline for a single study.",
        description="Run raw-Excel → PHI-scrub → llm_source/ for one study.",
    )
    run_p.add_argument(
        "--study",
        required=True,
        metavar="STUDY",
        help="Study name matching data/raw/{STUDY}/ (e.g. Indo-VAP)",
    )

    # ── verify ─────────────────────────────────────────────────────────────
    verify_p = sub.add_parser(
        "verify",
        help="Run post-run verifier assertions for a study.",
        description="Verify that a completed run is clean and destruction is attestable.",
    )
    verify_p.add_argument(
        "--study",
        required=True,
        metavar="STUDY",
        help="Study name to verify.",
    )
    verify_p.add_argument(
        "--run",
        dest="run_id",
        default=None,
        metavar="RUN_ID",
        help="Specific run_id to verify (defaults to most recent).",
    )

    # ── status ─────────────────────────────────────────────────────────────
    sub.add_parser(
        "status",
        help="Print skill scope and exit-code contract.",
        description="Print the HIPAA Safe Harbor scope banner and exit 0.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Argparse entry point.  Returns an integer exit code (does not call sys.exit)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.subcommand == "run":
            return _cmd_run(args)
        if args.subcommand == "verify":
            return _cmd_verify(args)
        if args.subcommand == "status":
            return _cmd_status(args)
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    return 1  # unreachable; satisfies mypy


if __name__ == "__main__":
    sys.exit(main())
