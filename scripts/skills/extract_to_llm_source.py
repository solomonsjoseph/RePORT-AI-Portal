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
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from scripts.extraction.dataset_pipeline import (
    ManifestMismatchError,
    check_forms_manifest,
)
from scripts.security.phi_patterns import SUBJECT_ID_PATTERNS
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

    # ------------------------------------------------------------------
    # a.5. Guard (I-5): assert no path segment leaks a subject ID into
    #      the attestation. If a filename was ever named after a subject,
    #      this would silently leak PHI into the destruction JSON.
    # ------------------------------------------------------------------
    for rp in removed_paths:
        for segment in Path(rp).parts:
            for pattern in SUBJECT_ID_PATTERNS:
                if re.search(pattern, segment):
                    raise ValueError(
                        "destroy_staging_and_attest: a removed path segment matches "
                        "SUBJECT_ID_PATTERNS — filename schema must be PHI-free. "
                        "Inspect the staging directory structure before retrying."
                    )

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
# Subcommand: verify — 12-assertion verifier (P4.1)
# ---------------------------------------------------------------------------

# Determinism-check: these keys must not appear in any llm_source/ artifact.
_FORBIDDEN_RUNTIME_KEYS: frozenset[str] = frozenset(
    {"extraction_utc", "run_id", "generated_utc"}
)

# Required fields for destruction_attestation.json (assertion 4).
_ATTESTATION_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "study",
        "started_utc",
        "completed_utc",
        "removed_paths",
        "files_destroyed",
        "cryptographic_erasure",
        "apfs_cow_disclaimer",
    }
)

# Rough ISO-8601 check (YYYY-MM-DDT or YYYY-MM-DD).
_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]")


def _resolve_run_id(
    study_output_dir: Path, run_id_arg: str | None
) -> tuple[str | None, str]:
    """Return (run_id, error_message).

    error_message is empty when resolution succeeds.  When resolution fails,
    run_id is None and error_message describes the problem.
    """
    if run_id_arg:
        return run_id_arg, ""

    runs_dir = study_output_dir / "runs"
    if not runs_dir.is_dir():
        return None, f"runs/ directory not found at {runs_dir}; no run to verify"

    candidates = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    if not candidates:
        return None, f"runs/ is empty at {runs_dir}; no run to verify"

    return candidates[0].name, ""


# ── individual assertion helpers ────────────────────────────────────────────

_AssertionResult = tuple[str, str]  # ("pass"|"fail"|"skipped", detail_str)


def _verify_assertion_1_manifest_exists_parses(
    study_raw_dir: Path,
) -> _AssertionResult:
    """Assertion 1: _forms_manifest.yaml exists and parses as a dict."""
    manifest_path = study_raw_dir / "_forms_manifest.yaml"
    if not manifest_path.exists():
        return "fail", f"_forms_manifest.yaml not found at {manifest_path}"
    try:
        with manifest_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return "fail", (
                f"_forms_manifest.yaml at {manifest_path} parsed but is not a dict "
                f"(got {type(data).__name__})"
            )
    except yaml.YAMLError as exc:
        return "fail", f"_forms_manifest.yaml at {manifest_path} failed to parse: {exc}"
    return "pass", ""


def _verify_assertion_2_manifest_reconciles(
    datasets_dir: Path,
) -> _AssertionResult:
    """Assertion 2: manifest reconciles with datasets/ actual contents."""
    try:
        check_forms_manifest(datasets_dir)
    except ManifestMismatchError as exc:
        return "fail", str(exc)
    except Exception as exc:  # noqa: BLE001
        return "fail", f"check_forms_manifest raised unexpected error: {exc}"
    return "pass", ""


def _verify_assertion_3_staging_absent(staging_dir: Path) -> _AssertionResult:
    """Assertion 3: study_staging_dir must not exist."""
    if staging_dir.exists():
        return "fail", f"staging dir still present: {staging_dir}"
    return "pass", ""


def _verify_assertion_4_attestation_valid(run_dir: Path) -> _AssertionResult:
    """Assertion 4: destruction_attestation.json exists, parses, has required fields."""
    attest_path = run_dir / "destruction_attestation.json"
    if not attest_path.exists():
        return "fail", f"destruction_attestation.json not found at {attest_path}"

    try:
        data = json.loads(attest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return "fail", f"destruction_attestation.json failed to parse: {exc}"

    missing = _ATTESTATION_REQUIRED_FIELDS - set(data.keys())
    if missing:
        return "fail", f"destruction_attestation.json missing required fields: {sorted(missing)}"

    # Timestamps look like ISO-8601
    for ts_field in ("started_utc", "completed_utc"):
        val = data.get(ts_field, "")
        if not isinstance(val, str) or not _ISO8601_RE.match(val):
            return "fail", (
                f"destruction_attestation.json field {ts_field!r} does not look "
                f"like ISO-8601: {val!r}"
            )

    return "pass", ""


def _verify_assertion_5_ledger_hashes(
    audit_dir: Path, phi_scrub_config_path: Path
) -> _AssertionResult:
    """Assertion 5: ledger has non-null run_id/scrub_config_hash/input_dataset_hash;
    scrub_config_hash matches SHA-256 of the current phi_scrub.yaml.
    """
    ledger_path = audit_dir / "phi_handling_ledger.as_written.json"
    if not ledger_path.exists():
        return "fail", f"phi_handling_ledger.as_written.json not found at {ledger_path}"

    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return "fail", f"phi_handling_ledger.as_written.json failed to parse: {exc}"

    for field in ("run_id", "scrub_config_hash", "input_dataset_hash"):
        if not data.get(field):
            return "fail", f"phi_handling_ledger.as_written.json: {field!r} is null or absent"

    # Verify scrub_config_hash matches current phi_scrub.yaml
    persisted_hash = data["scrub_config_hash"]
    if not phi_scrub_config_path.exists():
        return "fail", f"phi_scrub.yaml not found at {phi_scrub_config_path}; cannot verify hash"
    actual_hash = hashlib.sha256(phi_scrub_config_path.read_bytes()).hexdigest()
    if persisted_hash != actual_hash:
        return "fail", (
            f"scrub_config_hash mismatch: ledger has {persisted_hash!r}, "
            f"current phi_scrub.yaml hashes to {actual_hash!r}"
        )

    return "pass", ""


def _verify_assertion_6_no_llm_zone(audit_dir: Path) -> _AssertionResult:
    """Assertion 6: .NO_LLM_ZONE sentinel file exists."""
    sentinel = audit_dir / ".NO_LLM_ZONE"
    if not sentinel.exists():
        return "fail", f".NO_LLM_ZONE sentinel not found at {sentinel}"
    return "pass", ""


def _verify_assertion_7_no_quarantine(
    tmp_dir: Path, study: str, study_output_dir: Path
) -> _AssertionResult:
    """Assertion 7: no quarantine/ directory under tmp/ or output/{STUDY}/."""
    # Check tmp/<study>/quarantine/
    staging_quarantine = tmp_dir / study / "quarantine"
    if staging_quarantine.is_dir() and any(staging_quarantine.iterdir()):
        return "fail", f"non-empty quarantine dir found: {staging_quarantine}"
    # Check output/{STUDY}/quarantine/ (anywhere under output dir)
    for quarantine_dir in study_output_dir.rglob("quarantine"):
        if quarantine_dir.is_dir() and any(quarantine_dir.iterdir()):
            return "fail", f"non-empty quarantine dir found: {quarantine_dir}"
    return "pass", ""


def _verify_assertion_8_phi_absence(llm_source_dir: Path) -> _AssertionResult:
    """Assertion 8: no file under llm_source/ matches PHI patterns (blocking).

    Streams files line-by-line to avoid large memory allocation.
    Detail string names file path + line number + pattern — never the matched text.
    """
    from scripts.security.phi_patterns import BLOCKING_PATTERNS, SUBJECT_ID_PATTERNS

    # Build combined list of (name, compiled_pattern)
    all_patterns: list[tuple[str, re.Pattern[str]]] = list(BLOCKING_PATTERNS) + [
        (f"SUBJECT_ID[{i}]", p) for i, p in enumerate(SUBJECT_ID_PATTERNS)
    ]

    if not llm_source_dir.is_dir():
        # Nothing to scan — vacuously pass
        return "pass", ""

    for fpath in sorted(llm_source_dir.rglob("*")):
        if not fpath.is_file():
            continue
        try:
            with fpath.open(encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    for pat_name, pat in all_patterns:
                        if pat.search(line):
                            rel = fpath.relative_to(llm_source_dir)
                            return "fail", (
                                f"phi_patterns.{pat_name} matched in {rel} line {lineno} "
                                "(matched content omitted)"
                            )
        except OSError as exc:
            return "fail", f"could not read {fpath}: {exc}"

    return "pass", ""


def _verify_assertion_9_no_runtime_keys(llm_source_dir: Path) -> _AssertionResult:
    """Assertion 9: no extraction_utc/run_id/generated_utc in any llm_source/ artifact.

    Reads JSONL line-by-line; also checks JSON files.
    """
    if not llm_source_dir.is_dir():
        return "pass", ""

    for fpath in sorted(llm_source_dir.rglob("*")):
        if not fpath.is_file():
            continue
        suffix = fpath.suffix.lower()
        if suffix not in {".jsonl", ".json"}:
            continue
        try:
            with fpath.open(encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    found = _FORBIDDEN_RUNTIME_KEYS & set(obj.keys())
                    if found:
                        rel = fpath.relative_to(llm_source_dir)
                        return "fail", (
                            f"forbidden runtime key(s) {sorted(found)} found in "
                            f"{rel} line {lineno} (determinism violation)"
                        )
        except OSError as exc:
            return "fail", f"could not read {fpath}: {exc}"

    return "pass", ""


def _verify_assertion_10_required_jsonls_present(
    manifest_path: Path, llm_source_dir: Path
) -> _AssertionResult:
    """Assertion 10: every required form in manifest has exactly one JSONL under
    llm_source/datasets/.
    """
    try:
        with manifest_path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        required_forms: list[str] = raw.get("required") or []
    except Exception as exc:  # noqa: BLE001
        return "fail", f"could not load manifest for assertion 10: {exc}"

    datasets_out = llm_source_dir / "datasets"
    missing: list[str] = []
    for form in required_forms:
        stem = Path(form).stem
        expected = datasets_out / f"{stem}.jsonl"
        if not expected.exists():
            missing.append(form)

    if missing:
        return "fail", f"required form(s) missing from llm_source/datasets/: {missing}"

    return "pass", ""


def _verify_assertion_11_no_pipeline_lock(
    tmp_dir: Path, study: str
) -> _AssertionResult:
    """Assertion 11: pipeline lock file must be absent."""
    lock_path = tmp_dir / f".{study}.pipeline.lock"
    if lock_path.exists():
        return "fail", f"pipeline lock file still present: {lock_path}"
    return "pass", ""


def _assertion_12_update_status(
    run_dir: Path,
) -> _AssertionResult:
    """Assertion 12: status.json exists; on pass, set verifier_passed: true."""
    status_path = run_dir / "status.json"
    if not status_path.exists():
        return "fail", f"status.json not found at {status_path}"
    try:
        status_data = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return "fail", f"status.json failed to parse: {exc}"

    status_data["verifier_passed"] = True
    _atomic_write_json(status_path, status_data)
    return "pass", ""


# ── dispatcher ───────────────────────────────────────────────────────────────


def _cmd_verify(args: argparse.Namespace) -> int:  # noqa: PLR0912, PLR0914, PLR0915
    """Run 12 verifier assertions for the given study.

    Exit codes mirror the assertion failure modes:
        EXIT_OK (0)                     — all assertions passed
        EXIT_MANIFEST_MISMATCH (2)      — manifest assertions 1, 2, or 10
        EXIT_LEDGER_HASH_NULL (3)       — ledger/sentinel assertions 5, 6
        EXIT_QUARANTINE_NON_EMPTY (4)   — assertion 7
        EXIT_VERIFIER_FAIL (5)          — PHI/determinism assertions 8, 9
        EXIT_NEEDS_ADVICE (6)           — assertion 11 (lock file present)
        EXIT_DESTRUCTION_INCOMPLETE (7) — assertions 3, 4
    """
    import config  # lazy — keeps module testable without full config bootstrap

    study = args.study
    run_id_arg: str | None = getattr(args, "run_id", None)

    study_output_dir = Path(config.OUTPUT_DIR) / study
    study_raw_dir = Path(config.RAW_DATA_DIR) / study
    datasets_dir = study_raw_dir / "datasets"
    staging_dir = Path(config.TMP_DIR) / study
    tmp_dir = Path(config.TMP_DIR)
    phi_scrub_config_path = Path(config.PHI_SCRUB_CONFIG_PATH)

    # ── Resolve run_id ──────────────────────────────────────────────────────
    run_id, resolve_err = _resolve_run_id(study_output_dir, run_id_arg)
    if run_id is None:
        print(f"FAIL [resolve_run_id]: {resolve_err}", file=sys.stderr)
        return EXIT_NEEDS_ADVICE

    run_dir = study_output_dir / "runs" / run_id
    llm_source_dir = study_output_dir / "llm_source"
    audit_dir = study_output_dir / "audit"
    manifest_path = study_raw_dir / "_forms_manifest.yaml"

    checked_utc = datetime.now(timezone.utc).isoformat()

    # ── Assertion table ─────────────────────────────────────────────────────
    # Each entry: (n, name, callable, failure_exit_code)
    _assertion_table: list[
        tuple[int, str, Any, int]
    ] = [
        (
            1,
            "forms_manifest_exists_parses",
            lambda: _verify_assertion_1_manifest_exists_parses(study_raw_dir),
            EXIT_MANIFEST_MISMATCH,
        ),
        (
            2,
            "manifest_reconciles_with_dir",
            lambda: _verify_assertion_2_manifest_reconciles(datasets_dir),
            EXIT_MANIFEST_MISMATCH,
        ),
        (
            3,
            "staging_dir_absent",
            lambda: _verify_assertion_3_staging_absent(staging_dir),
            EXIT_DESTRUCTION_INCOMPLETE,
        ),
        (
            4,
            "destruction_attestation_valid",
            lambda: _verify_assertion_4_attestation_valid(run_dir),
            EXIT_DESTRUCTION_INCOMPLETE,
        ),
        (
            5,
            "ledger_hashes_valid",
            lambda: _verify_assertion_5_ledger_hashes(audit_dir, phi_scrub_config_path),
            EXIT_LEDGER_HASH_NULL,
        ),
        (
            6,
            "no_llm_zone_sentinel_present",
            lambda: _verify_assertion_6_no_llm_zone(audit_dir),
            EXIT_LEDGER_HASH_NULL,
        ),
        (
            7,
            "no_quarantine_dir",
            lambda: _verify_assertion_7_no_quarantine(tmp_dir, study, study_output_dir),
            EXIT_QUARANTINE_NON_EMPTY,
        ),
        (
            8,
            "llm_source_phi_absence",
            lambda: _verify_assertion_8_phi_absence(llm_source_dir),
            EXIT_VERIFIER_FAIL,
        ),
        (
            9,
            "llm_source_no_runtime_keys",
            lambda: _verify_assertion_9_no_runtime_keys(llm_source_dir),
            EXIT_VERIFIER_FAIL,
        ),
        (
            10,
            "required_forms_have_jsonl",
            lambda: _verify_assertion_10_required_jsonls_present(
                manifest_path, llm_source_dir
            ),
            EXIT_MANIFEST_MISMATCH,
        ),
        (
            11,
            "pipeline_lock_absent",
            lambda: _verify_assertion_11_no_pipeline_lock(tmp_dir, study),
            EXIT_NEEDS_ADVICE,
        ),
        (
            12,
            "status_json_updated",
            lambda: _assertion_12_update_status(run_dir),
            EXIT_VERIFIER_FAIL,  # exit code unused for assertion 12 (always last)
        ),
    ]

    results: list[dict[str, Any]] = []
    overall_exit_code: int = EXIT_OK
    failed_at: int | None = None

    for n, name, fn, fail_exit in _assertion_table:
        if failed_at is not None:
            results.append({"n": n, "name": name, "result": "skipped", "detail": ""})
            continue
        try:
            result, detail = fn()
        except Exception as exc:  # noqa: BLE001
            result = "fail"
            detail = f"assertion raised unexpected exception: {exc}"

        results.append({"n": n, "name": name, "result": result, "detail": detail})

        if result == "fail":
            failed_at = n
            overall_exit_code = fail_exit
            print(
                f"FAIL [assertion {n} — {name}]: {detail}",
                file=sys.stderr,
            )
        else:
            print(f"PASS [assertion {n} — {name}]")

    overall = "pass" if failed_at is None else "fail"

    report_payload: dict[str, Any] = {
        "run_id": run_id,
        "study": study,
        "checked_utc": checked_utc,
        "assertions": results,
        "overall": overall,
        "exit_code": overall_exit_code,
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(run_dir / "verifier_report.json", report_payload)

    return overall_exit_code


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

    Passes *study* explicitly so the lock-file name is keyed on the study
    supplied via ``--study``, not on ``config.STUDY_NAME`` (which may differ
    when the caller runs against a different study than the one auto-detected
    at import time).

    Raises RuntimeError (from main._acquire_pipeline_lock) if the lock is
    already held by another process.
    """
    import main as _main  # noqa: PLC0415 — lazy import intentional

    _main._acquire_pipeline_lock(study)  # noqa: SLF001


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

    # Derive all study-scoped paths from the explicit --study argument so that
    # the correct paths are used even when config.STUDY_NAME (resolved at
    # import time) refers to a different study.
    study_output_dir = Path(config.OUTPUT_DIR) / study
    study_staging_dir = Path(config.TMP_DIR) / study
    study_datasets_dir = Path(config.RAW_DATA_DIR) / study / "datasets"

    # ── Step 1a: resolve run_id ────────────────────────────────────────────
    run_id = resolve_run_id()

    # ── Step 1b: scan for in-progress scrubs ──────────────────────────────
    study_runs_dir = study_output_dir / "runs"
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

    # ── Steps 1d + 2 are inside the try so the lock is always released ────
    # (Minor-1 fix: manifest check is inside the try/finally so a raise there
    # cannot leak the lock.  Minor-2 fix: signal handlers are installed as the
    # first step inside the try so a SIGINT in that window is caught by the
    # _SkillInterrupted handler rather than bypassing the finally clause.)
    try:
        # ── Step 2: install signal handlers (first step inside try) ───────
        _install_signal_handlers()

        # ── Step 1d: validate forms manifest ──────────────────────────────
        try:
            check_forms_manifest(study_datasets_dir)
        except ManifestMismatchError as exc:
            print(f"Manifest mismatch: {exc}", file=sys.stderr)
            return EXIT_MANIFEST_MISMATCH

        # ── Step 3: subprocess invocation of main.py --pipeline ───────────
        env = dict(os.environ)
        # Security: never propagate scrub-bypass env var to child process.
        env.pop("REPORTALIN_ALLOW_DISABLED_SCRUB", None)
        # Propagate run_id so all sub-processes share the same run identifier.
        env["REPORTAL_RUN_ID"] = run_id

        # main.py resolves study from STUDY_NAME env var (it has no --study flag).
        env["STUDY_NAME"] = study
        # The wrapper already holds the pipeline lock (acquired above); signal
        # the subprocess so main.py's _acquire_pipeline_lock skips re-acquisition
        # rather than racing itself on the same fcntl flock.
        env["REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT"] = "1"
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
        ledger_path = study_output_dir / "audit" / "phi_handling_ledger.as_written.json"
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
        quarantine_dir = study_staging_dir / "quarantine"
        if quarantine_dir.is_dir() and any(quarantine_dir.iterdir()):
            print(
                f"Quarantine non-empty: {quarantine_dir}",
                file=sys.stderr,
            )
            return EXIT_QUARANTINE_NON_EMPTY

        # ── Step 5: destruction ───────────────────────────────────────────
        try:
            attest_path = destroy_staging_and_attest(
                study=study,
                run_id=run_id,
                staging_dir=study_staging_dir,
                output_dir=study_output_dir,
            )
        except DestructionIncompleteError as exc:
            print(f"Destruction incomplete: {exc}", file=sys.stderr)
            return EXIT_DESTRUCTION_INCOMPLETE

        # ── Step 6: write status.json ─────────────────────────────────────
        completed_utc = datetime.now(UTC).isoformat()
        run_dir = study_output_dir / "runs" / run_id
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
