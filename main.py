#!/usr/bin/env python3
"""Clinical-data pipeline entry point for the RePORT AI Portal (single-study mode).

Orchestrates dictionary loading, dataset extraction, PHI scrub, cleanup, and
publication to ``output/{STUDY}/llm_source/``. See ``docs/sphinx/`` for the
full step-by-step description; this file is just the CLI wrapper that wires
the legs together via ``run_step``.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shutil
import socket
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import config
from __version__ import __version__
from scripts.extraction.cleanup_propagation import run_propagation
from scripts.extraction.dataset_cleanup import clean_trio_datasets
from scripts.extraction.dataset_pipeline import process_datasets
from scripts.extraction.load_dictionary import load_study_dictionary
from scripts.security.llm_source_gate import scan_tree_for_phi
from scripts.security.phi_scrub import (
    PHIKeyMissingError,
    PHIKeyPermissionError,
    PHIScrubError,
)
from scripts.security.phi_scrub import load_key as _load_phi_key
from scripts.security.phi_scrub import run_scrub as run_phi_scrub
from scripts.utils import logging_system as log
from scripts.utils.errors import format_for_log, wrap
from scripts.utils.lineage import emit_lineage_manifest
from scripts.utils.log_hygiene import install_phi_redactor
from scripts.utils.run_context import resolve_run_id
from scripts.utils.secure_staging import (
    prepare_staging,
    resolve_staging_root,
    secure_remove_tree,
)
from scripts.utils.step_cache import hash_directory, hash_file, is_step_fresh, save_step_manifest

__all__ = [
    "main",
    "run_step",
]

_PIPELINE_LOCK_FILE: Any | None = None
_STREAMLIT_DEFAULT_PORT = 8501
_STREAMLIT_MAX_LOCAL_PORT = 8599


def _acquire_pipeline_lock(study: str | None = None) -> None:
    """Hold an exclusive per-study process lock for the lifetime of this run.

    Args:
        study: Study name to use for the lock-file name.  When ``None`` (the
            default, used by all existing callers inside main.py), falls back
            to ``config.STUDY_NAME`` so behaviour is unchanged.  Pass an
            explicit value when the caller controls the study name
            independently of the config (e.g. the skill wrapper with
            ``--study``).
    """
    global _PIPELINE_LOCK_FILE

    # When the skill wrapper invokes main.py --pipeline as a subprocess, the
    # wrapper already holds the fcntl flock on the study lockfile. Re-acquiring
    # it here would deadlock against ourselves on POSIX, so we skip cleanly
    # when the parent process signals it owns the lock. Direct `python main.py`
    # invocations do not set this env var and acquire the lock normally.
    if os.environ.get("REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT") == "1":
        return

    study_name = study if study is not None else config.STUDY_NAME
    lock_dir = Path(config.TMP_DIR)
    lock_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        lock_dir.chmod(0o700)
    lock_path = lock_dir / f".{study_name}.pipeline.lock"
    if _PIPELINE_LOCK_FILE is not None:
        if Path(str(_PIPELINE_LOCK_FILE.name)) == lock_path:
            return
        _PIPELINE_LOCK_FILE.close()
        _PIPELINE_LOCK_FILE = None

    fh = lock_path.open("a+", encoding="utf-8")
    with contextlib.suppress(OSError):
        lock_path.chmod(0o600)

    try:
        fh.seek(0)
        if os.name == "posix":
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif os.name == "nt":
            import msvcrt

            fh.write("\0")
            fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()}\nstudy={study_name}\n")
        fh.flush()
        os.fsync(fh.fileno())
    except OSError as exc:
        fh.close()
        raise RuntimeError(
            f"Another pipeline run already holds the study lock: {lock_path}"
        ) from exc

    _PIPELINE_LOCK_FILE = fh


def _release_pipeline_lock(study: str | None = None) -> None:
    """Release the process-local pipeline lock handle.

    Args:
        study: Accepted for API symmetry with ``_acquire_pipeline_lock`` but
            not used — the lock handle is a module-level singleton regardless
            of which study it was acquired for.
    """
    global _PIPELINE_LOCK_FILE
    # Symmetric to the acquire-side env-var guard: when the parent holds the
    # lock, this process never opened the file handle and has nothing to close.
    if os.environ.get("REPORTAL_PIPELINE_LOCK_HELD_BY_PARENT") == "1":
        return
    if _PIPELINE_LOCK_FILE is None:
        return
    _PIPELINE_LOCK_FILE.close()
    _PIPELINE_LOCK_FILE = None


def _streamlit_port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError:
        return False
    return True


def _local_streamlit_port(host: str) -> int:
    for port in range(_STREAMLIT_DEFAULT_PORT, _STREAMLIT_MAX_LOCAL_PORT + 1):
        if _streamlit_port_available(host, port):
            return port
    raise RuntimeError(
        f"No free Streamlit port found in {_STREAMLIT_DEFAULT_PORT}-{_STREAMLIT_MAX_LOCAL_PORT}."
    )


def _streamlit_launch_command() -> list[str]:
    cmd = [sys.executable, "-m", "streamlit", "run", "scripts/ai_assistant/web_ui.py"]
    if config.production_mode_enabled() or os.environ.get("STREAMLIT_SERVER_PORT"):
        return cmd

    host = os.environ.get("STREAMLIT_SERVER_ADDRESS", "127.0.0.1").strip() or "127.0.0.1"
    port = _local_streamlit_port(host)
    cmd.extend(["--server.port", str(port)])
    if port != _STREAMLIT_DEFAULT_PORT:
        log.warning(
            "Streamlit port %s is busy; launching local web UI on %s:%s.",
            _STREAMLIT_DEFAULT_PORT,
            host,
            port,
        )
    return cmd


def _install_log_redactor_best_effort() -> None:
    """Attach the PHI log-redaction filter, failing closed in production.

    **What.** Loads the sidecar HMAC key and installs the filter. Local/dev
    runs warn and continue when the key is absent; production runs stop.

    **Why.** The redactor needs the same 32-byte secret that keys pseudonym
    generation, so subject-id HMAC tags in logs stay joinable with the
    on-disk pseudonyms for operators who hold the key. Entry points that
    run before the PHI key exists (fresh checkout / first chat session)
    should still produce logs rather than hard-fail on a missing key. Production
    services must not run with PHI-capable logging unredacted.

    **How.** Calls :func:`scripts.security.phi_scrub.load_key`; on
    :class:`PHIKeyMissingError` / :class:`PHIKeyPermissionError` /
    :class:`PHIScrubError`, logs a one-line warning and returns without
    installing unless production controls are enabled. Successful installs are
    idempotent (the install helper no-ops when a filter is already present).
    """
    try:
        key = _load_phi_key()
    except (PHIKeyMissingError, PHIKeyPermissionError, PHIScrubError) as exc:
        if config.production_mode_enabled():
            raise RuntimeError(
                "Production startup refused: PHI log redactor could not be installed."
            ) from exc
        log.warning(
            "PHI log redactor NOT installed (%s). "
            "Use the web UI Load Study flow, or ask a developer/operator "
            "to provision the sidecar PHI key.",
            type(exc).__name__,
        )
        return
    # Seed the per-subject HMAC pass with the canonical SUBJECT_ID_PATTERNS
    # so SUBJ_*, SC_*, FID_* identifiers in log messages get tagged
    # ``<SUBJ_*>``. Without this, only the generic catalog redactions run.
    from scripts.security.phi_patterns import SUBJECT_ID_PATTERNS

    install_phi_redactor(hmac_key=key, subject_id_patterns=list(SUBJECT_ID_PATTERNS))


_OUTPUT_SIGNPOST_TEMPLATE = """\
# RePORT AI Portal — output/{study}/

Generated by the pipeline. This tree is per-machine processed output; never
treat the directory as a whole as an LLM input — only `llm_source/` (the
scrubbed artifacts) and `agent/` (the agent's own state) are, enforced by
`scripts.ai_assistant.file_access.validate_agent_read`. `audit/` is
off-limits to the agent.

## Layout

- `llm_source/` — GREEN zone. PHI-scrubbed datasets (JSONL), dictionary
  mapping JSONL, and verified lean Source Truth YAMLs. Part of the LLM
  agent's read surface (the other part is
  `agent/`, below). Each agent tool resolves every path through
  `scripts.ai_assistant.file_access.validate_agent_read`, which layers on
  top of the pipeline-side `assert_output_zone` directory early-reject.
- `audit/` — Counts-only IRB / maintainer evidence. `lineage_manifest.json`
  pairs every raw-input SHA-256 with every published llm_source SHA-256 and
  is the single evidence artifact an IRB / IEC reviewer inspects. No raw values
  anywhere in this subtree. Telemetry lives here too — so the LLM cannot
  read its own prior events structurally, by directory. Hard-rejected by
  `validate_agent_read`.
- `agent/` — Per-session state. `analysis/` holds deterministic epidemiology
  outputs, and `conversations/` holds chat transcripts. Readable parts of
  `agent/` (analysis and conversations) feed the agent's own session memory;
  `analysis/` is also its sandbox write zone (the
  narrower `validate_sandbox_write` — other `agent/` subdirs are read-only
  to LLM-generated code).

## Before you browse

- Do not commit this directory — it is gitignored for a reason.
- Do not share `agent/conversations/` externally. Responses are PHI-gated but
  free-form operator queries are preserved verbatim.
- Do not re-ingest `output/` as if it were raw study data. Raw data lives
  under `data/raw/{study}/`.

For the full honest-broker architecture see the repository-root README and
`docs/sphinx/irb_auditor/conformance.rst`.

---
Pipeline version: {version}
Emitted at: {timestamp}
This file is re-written on every successful pipeline run — edits will not
survive the next publish.
"""


# ── Staging workspace helpers ───────────────────────────────────────────────


def _prepare_staging() -> None:
    """Ensure the staging workspace is present, empty, and hardened at run start.

    Purges any residue from a prior failed run (securely — each file is
    overwritten with random bytes and fsynced before unlink), then creates
    the three per-leg subdirectories with mode ``0700`` under umask
    ``0077``. Called once at the top of ``main()`` so the extraction legs
    have a clean, access-restricted canvas.

    AMBER-zone hardening applied by this helper:
        * mode 0700 on every staging directory
        * umask 0077 during dir creation (so later writes inherit 0600 files)
        * secure_remove_tree on any prior residue (overwrite + fsync + unlink)
        * zone guard via assert_write_zone — rejects mis-configured roots

    Staging root is resolved by :func:`scripts.utils.secure_staging.resolve_staging_root`.
    When ``REPORTALIN_TMPFS_STAGING=1`` is set and ``/dev/shm`` is writable,
    staging redirects to a tmpfs mount under
    ``/dev/shm/report_ai_portal/{STUDY}/`` so raw extracted data never
    hits physical disk; otherwise the default on-disk ``tmp/{STUDY}/``
    path is used. The four ``config.STAGING_*`` path constants are
    rewritten in place to the resolved root so every downstream module
    that reads ``config.STAGING_DATASETS_DIR`` etc. sees the same value.
    """
    _acquire_pipeline_lock()
    staging = resolve_staging_root(
        Path(config.STUDY_STAGING_DIR),
        study_name=config.STUDY_NAME,
    )
    # Rewrite the four config path constants to the resolved root so
    # subsequent modules (extraction legs, phi_scrub, cleanup,
    # publish) read consistent paths regardless of the tmpfs opt-in.
    datasets_dir = staging / "datasets"
    dictionary_dir = staging / "dictionary"
    config.STUDY_STAGING_DIR = staging  # type: ignore[attr-defined]
    config.STAGING_DATASETS_DIR = datasets_dir  # type: ignore[attr-defined]
    config.STAGING_DICTIONARY_DIR = dictionary_dir  # type: ignore[attr-defined]

    prepare_staging(
        staging,
        subdirs=(datasets_dir, dictionary_dir),
    )
    log.info("Staging workspace prepared at %s", staging)


def _publish_leg(staging_dir: Path, trio_dir: Path, leg_name: str) -> bool:
    """Publish a single leg from staging → published leg under ``llm_source/``.

    Atomicity contract
    ------------------
    A mid-publish crash leaves ``trio_dir`` either fully absent or fully
    populated — never half-populated.

    Happy path (staging and output on the same filesystem):
        ``staging_dir.rename(trio_dir)`` is a single atomic OS syscall.

    Cross-filesystem fallback (staging on a different device than output):
        Files are first copied into a sibling temp directory
        ``trio_dir.parent / ".llm_source.publishing"`` which lives on the
        *same* filesystem as ``trio_dir``.  Only after the copy completes does
        ``os.rename`` promote the sibling temp dir to ``trio_dir`` (a single
        atomic syscall) and the parent directory is fsynced for durability.
        A crash before the rename leaves ``trio_dir`` absent; a crash after
        leaves it fully populated.  Option A is used for pre-existing
        ``trio_dir``: the old tree is securely removed *before* the rename,
        so the worst-case window is "old removed, new not yet in place".
        See main.py:_publish_leg for the rename site.

    Idempotent: if ``staging_dir`` has no content, leaves ``trio_dir``
    untouched so a 'leg was fresh and skipped' run keeps its prior
    published output.

    The destination is asserted to be under the output zone. The
    staging source is intentionally NOT asserted — staging lives under
    ``tmp/`` which is outside the output zone by design.

    Returns:
        True if the leg was published, False if skipped (empty staging).
    """
    from scripts.security.secure_env import assert_output_zone

    staging_dir = Path(staging_dir)
    trio_dir = Path(trio_dir)
    assert_output_zone(trio_dir)

    if not staging_dir.is_dir() or not any(staging_dir.iterdir()):
        log.info("Skipping publish for %s — staging dir empty", leg_name)
        return False

    trio_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Happy path: staging and output on the same filesystem — a single
        # atomic rename syscall.  Pre-existing trio_dir is handled implicitly
        # because rename replaces an existing directory on the same device
        # only when it is empty; we rely on secure_remove_tree clearing it
        # first when the directory already exists.
        if trio_dir.exists():
            # Use secure_remove_tree (zero-fill + symlink-aware) instead of
            # plain shutil.rmtree so a republish doesn't leave PHI-adjacent
            # forensic blocks recoverable from the disk.
            secure_remove_tree(trio_dir)
        staging_dir.rename(trio_dir)  # atomic: line referenced by atomicity contract
        log.info("Published %s: %s → %s", leg_name, staging_dir, trio_dir)
    except OSError as exc:
        # Cross-filesystem rename (EXDEV) — fall back to sibling-tmp + rename.
        # The sibling lives under trio_dir.parent so it is guaranteed to be on
        # the same filesystem as trio_dir; the final rename is therefore atomic.
        log.warning(
            "Rename failed for %s (%s) — falling back to sibling-tmp + rename",
            leg_name,
            exc,
        )
        sibling_tmp = trio_dir.parent / ".llm_source.publishing"
        try:
            # 1. Populate sibling tmp (still not visible at trio_dir).
            shutil.copytree(staging_dir, sibling_tmp)

            # 2. Option A: remove prior trio_dir before the rename.
            #    Weakens guarantee to "after old removed, before rename" but the
            #    only partial-state window is the rename syscall itself.
            if trio_dir.exists():
                secure_remove_tree(trio_dir)

            # 3. Atomic promotion: sibling_tmp → trio_dir (same filesystem).
            os.rename(sibling_tmp, trio_dir)  # atomic: rename site (cross-fs path)

            # 4. fsync the parent directory so the rename is durable on crash.
            parent_fd = os.open(str(trio_dir.parent), os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)

            log.info(
                "Published %s via sibling-tmp + rename: %s → %s",
                leg_name,
                staging_dir,
                trio_dir,
            )
        except Exception:
            # Best-effort cleanup of the sibling tmp on any error; do not
            # silently swallow the original exception.
            if sibling_tmp.exists():
                shutil.rmtree(sibling_tmp, ignore_errors=True)
            raise
        finally:
            # Remove staging source regardless (matches happy-path behaviour).
            shutil.rmtree(staging_dir, ignore_errors=True)
    return True


def _publish_staging() -> dict[str, bool]:
    """Publish staging legs into their ``llm_source/`` destinations.

    Each leg is publish-or-skip independently: an empty staging leg
    leaves its published counterpart untouched.

    Atomicity: each leg delegates to :func:`_publish_leg` which guarantees
    that ``llm_source/{leg}/`` is either absent or fully populated after any
    crash — never half-populated.  See ``_publish_leg`` docstring for the
    full contract.
    """
    return {
        "datasets": _publish_leg(
            Path(config.STAGING_DATASETS_DIR),
            Path(config.TRIO_DATASETS_DIR),
            "datasets",
        ),
        "dictionary": _publish_leg(
            Path(config.STAGING_DICTIONARY_DIR),
            Path(config.DICTIONARY_JSON_OUTPUT_DIR),
            "dictionary",
        ),
    }


def _emit_output_signpost() -> None:
    """Write a README.md at ``output/{STUDY}/`` explaining the subtree layout.

    The signpost is a plain-text orientation aid for anyone who opens the
    per-study output directory on a shared machine without prior context —
    IRB reviewers browsing evidence, sysadmins auditing disk usage, future
    maintainers. It names every top-level subdirectory (``llm_source/`` =
    LLM read zone, ``audit/`` = IRB evidence, ``agent/`` = session state)
    and points readers at the repo-root README and IRB conformance matrix
    for the full architecture.

    The file is re-written idempotently on every successful pipeline run so
    its content cannot drift behind the code. It is NOT consumed by any
    runtime module — think of it as a filesystem-level breadcrumb.
    """
    study_root = Path(config.STUDY_OUTPUT_DIR)
    study_root.mkdir(parents=True, exist_ok=True)
    signpost = study_root / "README.md"
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    signpost.write_text(
        _OUTPUT_SIGNPOST_TEMPLATE.format(
            study=config.STUDY_NAME,
            version=__version__,
            timestamp=timestamp,
        ),
        encoding="utf-8",
    )
    log.info("Output signpost written: %s", signpost)


def _cleanup_staging() -> None:
    """Securely remove the staging workspace on successful pipeline completion.

    Each staging file is overwritten with random bytes and fsynced before
    being unlinked, reducing the window during which deleted PHI could be
    recovered via filesystem forensics. This is the AMBER → GREEN transition
    — staging is PHI-carrying by design, so teardown must not leave
    recoverable remnants.

    On failure, the caller deliberately skips this to preserve residue
    for operator inspection.
    """
    staging = Path(config.STUDY_STAGING_DIR)
    if staging.exists():
        secure_remove_tree(staging)
        log.info("Staging workspace securely removed: %s", staging)
    _release_pipeline_lock()


def _run_dict_leg(*, skip: bool) -> dict[str, Any]:
    """Extract data dictionary into ``STAGING_DICTIONARY_DIR``.

    Independent of the dataset leg, so safe to run in parallel with it.
    No PHI in the dictionary itself, so no scrub needed here — the
    cleanup-propagation step still prunes dropped variables later.
    """
    if skip:
        log.info("--- Skipping Step 0: Data Dictionary Loading ---")
        return {"leg": "dictionary", "skipped": True}
    log.info("Leg [dictionary]: starting extraction → %s", config.STAGING_DICTIONARY_DIR)
    load_study_dictionary(dictionary_dir=str(config.DATA_DICTIONARY_DIR))
    log.info("Leg [dictionary]: complete")
    return {"leg": "dictionary", "skipped": False}


def _prefix_hashes(prefix: str, hashes: dict[str, str]) -> dict[str, str]:
    """Namespace content hashes from independent input roots.

    The step-cache manifest compares a single flat mapping. Prefixing avoids
    collisions between same-named files in raw datasets, SoT policies, and
    PHI scrub configuration.
    """
    return {f"{prefix}/{rel}": digest for rel, digest in hashes.items()}


def _hash_file_input(label: str, path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return {label: hash_file(path)}


def _dataset_processing_input_hashes(raw_datasets_dir: Path) -> dict[str, str]:
    """Hash every input that can change published scrubbed datasets.

    Dataset extraction is not only a function of raw spreadsheets/CSVs. The
    PHI scrub config and Source Truth policies change which columns are kept,
    dropped, or transformed, so they must invalidate the same cache manifest.
    """
    hashes: dict[str, str] = {}
    hashes.update(
        _prefix_hashes(
            "raw_datasets",
            hash_directory(raw_datasets_dir, extensions=frozenset({".xlsx", ".xls", ".csv"})),
        )
    )
    hashes.update(
        _prefix_hashes(
            "sot",
            hash_directory(Path(config.SOT_DIR), extensions=frozenset({".yaml", ".yml"})),
        )
    )
    hashes.update(_hash_file_input("phi_scrub_config", Path(config.PHI_SCRUB_CONFIG_PATH)))
    hashes.update(
        _hash_file_input(
            "phi_scrub_code", Path(config.BASE_DIR) / "scripts" / "security" / "phi_scrub.py"
        )
    )
    return dict(sorted(hashes.items()))


def _run_dataset_leg(*, force: bool, run_extraction: bool) -> dict[str, Any]:
    """Extract datasets into ``STAGING_DATASETS_DIR`` with input-hash cache.

    Returns ``dropped_events`` so the cleanup chain can mirror the
    extraction-time drops into the dictionary leg. Safe to run in parallel
    with the dictionary leg (different input dir, different staging subdir,
    no shared mutable state).
    """
    if not run_extraction:
        return {"leg": "datasets", "skipped": True, "dropped_events": []}

    raw_datasets_dir = Path(config.DATASETS_DIR)
    datasets_input_hashes = _dataset_processing_input_hashes(raw_datasets_dir)
    audit_dir = Path(config.STUDY_AUDIT_DIR)
    trio_datasets_dir = Path(config.TRIO_DATASETS_DIR)

    llm_source_has_jsonl = trio_datasets_dir.is_dir() and any(trio_datasets_dir.glob("*.jsonl"))

    if (
        not force
        and llm_source_has_jsonl
        and is_step_fresh("dataset_processing", audit_dir, datasets_input_hashes)
    ):
        log.info("Leg [datasets]: skipped (inputs unchanged)")
        print("  ⏭  Step 1+3: Dataset Processing — skipped (inputs unchanged)")
        return {"leg": "datasets", "skipped": True, "dropped_events": []}

    log.info("Leg [datasets]: starting extraction → %s", config.STAGING_DATASETS_DIR)
    datasets_result = process_datasets()
    dropped_events: list[dict[str, Any]] = []
    if isinstance(datasets_result, dict):
        extraction_payload = datasets_result.get("extraction") or {}
        raw_events = extraction_payload.get("dropped_events") or []
        if isinstance(raw_events, list):
            dropped_events = raw_events
    save_step_manifest("dataset_processing", audit_dir, datasets_input_hashes)
    log.info("Leg [datasets]: complete (%d drop events)", len(dropped_events))
    return {"leg": "datasets", "skipped": False, "dropped_events": dropped_events}


def run_step(step_name: str, func: Callable[[], Any]) -> Any:
    """Execute a pipeline step with comprehensive error handling and logging.

    This function wraps individual pipeline steps to provide consistent error
    handling, logging, and exit behavior. It acts as the pipeline's safety net,
    ensuring that any failure in a step is caught, logged, and results in a
    clean exit with a non-zero status code.

    The function supports multiple failure modes:
    - Boolean `False` return values indicate step failure
    - Dict results with an 'errors' key indicate partial failure
    - Uncaught exceptions are logged with full stack traces

    All steps are logged with clear start/success/failure messages to both
    console and log files (see `config.LOG_NAME` for log file location).

    Args:
        step_name (str): Human-readable name of the pipeline step (e.g.,
            "Step 1: Extracting Raw Data to JSONL"). Used in log messages
            and error reporting.
        func (Callable[[], Any]): Zero-argument callable that executes the
            actual step logic. This should be a lambda or function reference
            that performs the work and returns a result or raises an exception.

    Returns:
        Any: The return value from `func()` if successful. Return type depends
            on the specific step being executed (e.g., dict with statistics,
            bool for success/failure, or None).

    Raises:
        SystemExit: Always raised on failure (exit code 1). This terminates
            the entire pipeline to prevent cascading errors from invalid data.
            Reasons for exit:
            - `func()` returns `False`
            - `func()` returns a dict with non-empty 'errors' list
            - `func()` raises any exception

    Example:
        >>> import logging
        >>> from scripts.utils import logging_system as log
        >>> log.setup_logger(name='test', log_level=logging.INFO, simple_mode=True)
        >>> # Successful step
        >>> def successful_task():
        ...     return {'processed': 100, 'errors': []}
        >>> result = run_step("Test Task", successful_task)
        >>> result['processed']
        100
        >>> # Failing step (returns False)
        >>> def failing_task():
        ...     return False
        >>> try:
        ...     run_step("Failing Task", failing_task)
        ... except SystemExit as e:
        ...     print(f"Exit code: {e.code}")
        Exit code: 1

    Notes:
        - This function uses `sys.exit(1)` rather than raising exceptions to
          ensure clean termination visible to shell scripts and CI/CD systems.
        - Stack traces are logged via `exc_info=True` for debugging.
        - Success messages use `log.success()` for visual distinction in logs.

    See Also:
        main: Orchestrates all pipeline steps using this wrapper
        config.LOG_NAME: Configures the log file name
    """
    try:
        log.info(f"--- {step_name} ---")
        result: Any = func()

        # Check if result indicates failure
        if isinstance(result, bool) and not result:
            log.error(f"{step_name} failed.")
            sys.exit(1)
        elif isinstance(result, dict):
            result_dict = cast(dict[str, Any], result)
            if result_dict.get("errors"):
                log.error(f"{step_name} completed with {len(result_dict['errors'])} errors.")
                sys.exit(1)

        log.success(f"{step_name} completed successfully.")
        return cast(Any, result)
    except Exception as e:
        log.error("Fatal: %s", format_for_log(wrap(e, stage="pipeline", operation=step_name, include_traceback=False)))
        sys.exit(1)


def main() -> None:
    """Orchestrate the complete clinical data processing pipeline.

    This is the main entry point for the RePORT AI Portal pipeline. It parses command-
    line arguments, configures logging, validates the environment, and executes
    the multi-step workflow to process clinical study data from raw Excel files
    to clean, structured JSONL records.

    The function implements a sequential pipeline with optional step skipping:

    **Step 0 - Dictionary Loading:**
        Parses the data dictionary Excel file to extract field definitions,
        data types, validation rules, and metadata. Outputs structured JSON
        for downstream validation. (Skip with `--skip-dictionary`)

    **Step 1+3 - Dataset Processing:**
        Unified extract → promote → cleanup via ``process_datasets()``.
        Extracts raw datasets to secure temp workspace, promotes clean JSONL
        to llm_source/dataset_schema/files/, and removes temp workspace.
        (Enable with `--process-datasets`)

    Configuration and Validation:
        - All paths, study names, and settings are loaded from `config.py`
        - Configuration validation runs before any processing starts
        - Required directories are created automatically if missing
        - Logging is configured based on `--verbose` flag (default: simple mode)

    Command-Line Interface:
        The function accepts numerous CLI arguments for fine-grained control:

        **Workflow Control:**
            --skip-dictionary           Skip Step 0
            --process-datasets          Extract and promote datasets (Step 1+3)
            --pipeline                  Run full pipeline: Extract → Promote → Registry → Index

        **Logging:**
            -v, --verbose               Enable DEBUG logging with full output
            --version                   Show version and exit

    Returns:
        None: This function orchestrates the pipeline but does not return a
            value. It exits with code 0 on success or code 1 on failure.

    Raises:
        SystemExit: Always raised on failure (exit code 1). Reasons include:
            - Configuration validation failure (missing directories)
            - Any step failure (logged via `run_step()`)
            - Uncaught exceptions in argument parsing or setup
        FileNotFoundError: Caught and converted to SystemExit if required
            directories are missing (data/raw/<study>/datasets/, etc.)

    Example:
        >>> # Simulate command-line execution (conceptual - requires data setup)
        >>> import sys
        >>> # Show version
        >>> sys.argv = ['main.py', '--version']
        >>> # Would display version and exit
        >>>
        >>> # Run with verbose logging (requires actual data files)
        >>> # sys.argv = ['main.py', '--verbose']
        >>> # main()  # Would execute full pipeline with DEBUG logging

    Notes:
        - Default logging: Simple mode (INFO level, minimal console output)
        - Verbose mode (`-v`): DEBUG level with full context and stack traces
        - All operations are logged to `.logs/<LOG_NAME>.log`
        - Shell completion available if `argcomplete` package is installed

    See Also:
        run_step: Wrapper for individual pipeline steps with error handling
        config.validate_config: Validates directory structure and settings
        scripts.extraction.load_dictionary.load_study_dictionary: Step 0 implementation
        scripts.extraction.dataset_pipeline.process_datasets: Unified dataset pipeline
    """
    parser = argparse.ArgumentParser(
        prog="RePORT AI Portal",
        description="Clinical data processing pipeline for structured clinical research data.",
        epilog="""
Usage:
  %(prog)s                              # Run complete pipeline
  %(prog)s --skip-dictionary            # Skip dictionary, run extraction
  %(prog)s --pipeline                   # Full pipeline: Extract → Promote → llm_source

For detailed documentation, see the Sphinx docs or README.md
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program version and exit",
    )
    parser.add_argument(
        "--skip-dictionary", action="store_true", help="Skip data dictionary loading (Step 0)"
    )
    parser.add_argument(
        "--skip-datasets",
        action="store_true",
        help="Skip dataset processing (extract + promote + cleanup)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging with detailed context. "
        "Default: Simple mode (INFO level, minimal console output)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-run all steps even if outputs exist and inputs are unchanged. "
        "Bypasses the incremental cache.",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Start the interactive AI Assistant chat REPL",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch the Streamlit web UI for the AI Assistant",
    )

    # LLM provider / model overrides (apply to chat, web, pipeline)
    parser.add_argument(
        "--provider",
        type=str,
        metavar="NAME",
        help="LLM provider: ollama, anthropic, openai, google-genai",
    )
    parser.add_argument(
        "--model",
        type=str,
        metavar="NAME",
        help="LLM model name (e.g. qwen3:8b, claude-opus-4-7, gpt-5.5, gemini-3.1-pro-preview)",
    )

    # Dataset processing + llm_source publication
    parser.add_argument(
        "--build-bundle",
        action="store_true",
        help="Prepare the llm_source dictionary leg (legacy compatibility alias)",
    )
    parser.add_argument(
        "--process-datasets",
        action="store_true",
        help="Extract raw datasets and promote to llm_source/dataset_schema/files/ (unified pipeline)",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="Run full pipeline: Extract → Promote → llm_source",
    )

    args = parser.parse_args()

    # Apply LLM overrides early — affects all paths (chat, web, pipeline).
    if getattr(args, "provider", None):
        config.LLM_PROVIDER = args.provider  # type: ignore[attr-defined]
        os.environ["LLM_PROVIDER"] = args.provider
    if getattr(args, "model", None):
        config.LLM_MODEL = args.model  # type: ignore[attr-defined]
        os.environ["LLM_MODEL"] = args.model

    if getattr(args, "web", False):
        import subprocess

        log.setup_logger(
            name=config.LOG_NAME,
            log_level=logging.DEBUG if args.verbose else logging.INFO,
            simple_mode=not args.verbose,
            verbose=args.verbose,
        )
        _install_log_redactor_best_effort()
        log.info("Launching Streamlit web UI…")
        try:
            subprocess.run(_streamlit_launch_command(), check=True)  # noqa: S603
        except KeyboardInterrupt:
            log.info("Streamlit web UI stopped by operator.")
        return

    if getattr(args, "chat", False):
        from scripts.ai_assistant.cli import run_repl

        log.setup_logger(
            name=config.LOG_NAME,
            log_level=logging.DEBUG if args.verbose else logging.INFO,
            simple_mode=not args.verbose,
            verbose=args.verbose,
        )
        _install_log_redactor_best_effort()
        run_repl()
        return

    # --build-bundle: legacy compatibility flag for dictionary preparation,
    # NO dataset processing and no legacy bundle publication.
    if args.build_bundle:
        args.skip_dictionary = False

    # --pipeline expands to the full chain:
    #   Dict(0) → ProcessDatasets(1+3) → llm_source publication
    if args.pipeline:
        args.skip_dictionary = False
        args.process_datasets = True
        args.build_bundle = True

    # Set log level and mode: Default = simple mode (level from config, minimal console)
    # Only --verbose flag enables DEBUG mode with full console output
    if args.verbose:
        log_level = logging.DEBUG
        simple_mode = False
        verbose = True
    else:
        # DEFAULT: Simple mode (level from config.yaml / env, minimal console output)
        log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
        simple_mode = True
        verbose = False

    log.setup_logger(
        name=config.LOG_NAME, log_level=log_level, simple_mode=simple_mode, verbose=verbose
    )
    _install_log_redactor_best_effort()
    log.info("Starting RePORT AI Portal pipeline...")

    # Validate configuration (raises exceptions on errors)
    try:
        config.validate_config()
        log.info("Configuration validated successfully")
    except FileNotFoundError as e:
        log.error(f"Configuration validation failed: {e}")
        print(f"\n❌ Configuration Error: {e}")
        print("\nPlease ensure your data directory structure is correct:")
        print(f"  data/raw/{config.STUDY_NAME}/datasets/")
        print(f"  data/raw/{config.STUDY_NAME}/data_dictionary/")
        sys.exit(1)

    # Ensure required directories exist
    config.ensure_directories()

    # Purge + prepare the per-run staging workspace. Extraction legs write here
    # first; publish step atomically promotes them into llm_source/.
    _prepare_staging()

    # Display startup banner
    print("\n" + "=" * 70)
    print("RePORT AI Portal - Report India Clinical Study Data Pipeline")
    print("=" * 70 + "\n")

    force = args.force

    # ── Steps 0 + 1: PARALLEL EXTRACTION PHASE ──
    # Dictionary and datasets each read different RED inputs and write to
    # different AMBER staging subdirs — they are fully decoupled, so we run
    # them concurrently to amortise Excel parsing CPU against dataset I/O.
    # Cleanup chain (PHI scrub / dataset cleanup / propagation) and Publish
    # are sequential AFTER the join because they have hard data dependencies
    # on the extraction results.
    print("\n--- Parallel extraction phase: Dictionary | Datasets ---")
    log.info("Starting parallel extraction phase (max_workers=2)")

    dropped_events: list[dict[str, Any]] = []
    extraction_failures: list[tuple[str, BaseException]] = []
    extraction_start = time.time()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures: dict[Any, str] = {
            executor.submit(_run_dict_leg, skip=args.skip_dictionary): "dictionary",
            executor.submit(
                _run_dataset_leg,
                force=force,
                run_extraction=args.process_datasets and not args.skip_datasets,
            ): "datasets",
        }
        for fut in as_completed(futures):
            leg_name = futures[fut]
            try:
                result = fut.result()
            except BaseException as exc:
                extraction_failures.append((leg_name, exc))
                log.error(
                    "Fatal: %s",
                    format_for_log(wrap(exc, stage="pipeline.extract", operation=leg_name, include_traceback=False)),
                )
                continue
            if leg_name == "datasets":
                events = result.get("dropped_events", [])
                if isinstance(events, list):
                    dropped_events = events

    extraction_elapsed = time.time() - extraction_start
    log.info(
        "Parallel extraction phase complete in %.1fs (dataset drops: %d)",
        extraction_elapsed,
        len(dropped_events),
    )

    # Hard fail if any extraction leg crashed: the cleanup chain has no input
    # and we'd rather surface the failure here than corrupt llm_source/.
    if extraction_failures:
        for leg, err in extraction_failures:
            print(f"\n❌ Extraction leg [{leg}] failed: {err}")
        sys.exit(1)

    # ── Step 1.6: PHI Scrub (date jitter + ID pseudonymization) ──
    # Operates on the STAGING datasets tree BEFORE Step 1.7 cleanup. Running
    # scrub first keeps the dataset audit + propagation events free of raw
    # subject IDs and raw dates — no PHI ever lands in output/{STUDY}/audit/.
    #
    # When scripts/security/phi_scrub.yaml is absent, the module no-ops and
    # emits a single "disabled" audit file so downstream tooling always finds
    # a fourth audit entry.
    if args.process_datasets and not args.skip_datasets:
        staging_ds = Path(config.STAGING_DATASETS_DIR)
        if staging_ds.is_dir() and any(staging_ds.glob("*.jsonl")):
            run_step(
                "Step 1.6: PHI Scrub",
                lambda: run_phi_scrub(
                    config.STUDY_NAME,
                    run_id=resolve_run_id(),
                    runs_dir=Path(config.STUDY_OUTPUT_DIR) / "runs",
                ),
            )

    # ── Step 1.7: Dataset Cleanup (remove junk, merge duplicates) ──
    # Runs against the STAGING datasets tree before publish. The staging
    # layout ensures the audit envelope + propagation inputs are complete
    # before llm_source/dataset_schema/files/ is re-materialised.
    if args.process_datasets and not args.skip_datasets:
        cleanup_dir = Path(config.STAGING_DATASETS_DIR)
        if cleanup_dir.is_dir() and any(cleanup_dir.glob("*.jsonl")):
            events_for_cleanup = dropped_events

            def run_cleanup() -> None:
                report = clean_trio_datasets(
                    cleanup_dir,
                    extracted_drop_events=events_for_cleanup,
                    study_name=config.STUDY_NAME,
                )
                if report.total_actions or events_for_cleanup:
                    log.info(
                        "Dataset cleanup: removed %d junk, merged %d duplicates, "
                        "passed-through %d extraction drops",
                        len(report.junk_removed),
                        len(report.duplicates_merged),
                        len(events_for_cleanup),
                    )
                else:
                    log.info("Dataset cleanup: no actions needed")

            run_step("Step 1.7: Dataset Cleanup", run_cleanup)

    # ── Step 1.8: Cleanup Propagation (dictionary pruning) ──
    # Mirrors dataset drops into the dictionary staging tree. Safe no-op
    # when nothing was dropped (emits empty-but-valid leg audits). Runs
    # AFTER Step 1.7 so dictionary staging is populated and the dataset
    # audit is on disk.
    if Path(config.STAGING_DICTIONARY_DIR).is_dir():
        run_step(
            "Step 1.8: Cleanup Propagation",
            lambda: run_propagation(),
        )

    # ── Step 2: Publish Staging → llm_source/ ──
    # Atomic-rename each staging leg into llm_source/; empty legs leave
    # their published counterpart untouched so a skipped-fresh leg keeps
    # its prior publish.
    if args.process_datasets and not args.skip_datasets:
        staging_ds = Path(config.STAGING_DATASETS_DIR)
        if staging_ds.is_dir() and any(staging_ds.glob("*.jsonl")):
            scan = scan_tree_for_phi(staging_ds)
            if not scan.ok:
                raise RuntimeError(f"Pre-publication PHI leak scan failed: {scan.detail}")

    def run_publish() -> None:
        published = _publish_staging()
        published_legs = {k: v for k, v in published.items() if v}
        if published_legs:
            log.info("Published legs: %s", sorted(published_legs))
        else:
            log.info("Publish: all legs skipped (staging empty)")

    run_step("Step 2: Publish Staging → llm_source", run_publish)

    # Removed: scripts.source_truth.build — see docs/sphinx/developer_guide/source_truth_build.rst

    # ── Step 4: Lineage Manifest (audit-ready evidence package) ──
    # Emits output/{STUDY}/audit/lineage_manifest.json pairing every raw
    # input file (SHA-256) with every published llm_source artifact (SHA-256),
    # plus per-leg audit references + compliance posture. This is the
    # single artifact an IRB/IEC reviewer inspects to verify the full
    # raw → scrub → publish chain without reading any row contents.
    def run_lineage() -> None:
        import hashlib as _hashlib

        try:
            from scripts.security.phi_scrub import load_scrub_config

            scrub_cfg = load_scrub_config()
            posture = scrub_cfg.compliance_posture if scrub_cfg is not None else "disabled"
        except Exception:
            posture = "unknown"

        # PHI key fingerprint — gives IRB reviewers a verifiable handle
        # without exposing the key itself. SHA-256 of the raw HMAC key.
        phi_key_fp: str | None = None
        try:
            phi_key_fp = _hashlib.sha256(_load_phi_key()).hexdigest()
        except (PHIKeyMissingError, PHIKeyPermissionError, PHIScrubError):
            phi_key_fp = None  # leave manifest free of the field

        audit_dir = Path(config.STUDY_AUDIT_DIR)
        audit_dir.mkdir(parents=True, exist_ok=True)
        emit_lineage_manifest(
            study_name=config.STUDY_NAME,
            raw_datasets_dir=Path(config.DATASETS_DIR),
            raw_dictionary_dir=Path(config.DATA_DICTIONARY_DIR)
            if Path(config.DATA_DICTIONARY_DIR).is_dir()
            else None,
            raw_pdfs_dir=Path(config.ANNOTATED_PDFS_DIR)
            if Path(config.ANNOTATED_PDFS_DIR).is_dir()
            else None,
            llm_source_dir=Path(config.STUDY_LLM_SOURCE_DIR),
            audit_dir=audit_dir,
            pipeline_version=__version__,
            compliance_posture=posture,
            manifest_path=audit_dir / "lineage_manifest.json",
            phi_key_fingerprint=phi_key_fp,
        )

    run_step("Step 4: Emit Lineage Manifest", run_lineage)

    # ── Step 5: Output Signpost ──
    # Plain-text README.md at output/{STUDY}/ explaining the three-tier
    # layout for anyone who opens the directory without repo context
    # (IRB reviewer, sysadmin, future maintainer). Re-written on every
    # successful run so it cannot drift.
    run_step("Step 5: Emit Output Signpost", _emit_output_signpost)

    log.info("RePORT AI Portal pipeline finished.")

    # Success-only: remove the staging workspace. If any earlier step called
    # sys.exit(1), control never reaches here and the staging tree is left
    # behind for inspection.
    _cleanup_staging()

    # ── Final summary: show all useful output locations ──
    print("\n" + "=" * 70)
    print("  Pipeline Complete — Output Locations")
    print("=" * 70)
    print(f"\n  LLM Source Root:     {config.STUDY_LLM_SOURCE_DIR}")
    print(f"    Datasets (JSONL):  {config.TRIO_DATASETS_DIR}")
    print(f"    Data Dictionary:   {config.DICTIONARY_JSON_OUTPUT_DIR}")
    print(f"    Audit Reports:     {config.STUDY_AUDIT_DIR}")
    print(f"      Dataset Audit:     {config.AUDIT_DATASET_REPORT_PATH}")
    print(f"      PHI Scrub Audit:   {config.AUDIT_SCRUB_REPORT_PATH}")
    print(f"  Agent State Root:    {config.AGENT_STATE_DIR}")
    print(f"    Analysis Output:   {config.AGENT_OUTPUT_DIR}")
    print(f"    Conversations:     {config.CONVERSATIONS_DIR}")
    print(f"    Telemetry:         {config.TELEMETRY_DIR}")
    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
