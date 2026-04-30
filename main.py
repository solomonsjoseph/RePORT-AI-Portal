#!/usr/bin/env python3
"""Clinical data processing pipeline for RePORT AI Portal (single-study mode).

This module provides the main entry point for the RePORT AI Portal clinical study
data processing pipeline. It orchestrates a multi-step workflow that transforms
raw study data from one fixed study under ``data/raw/{STUDY_NAME}/`` into clean,
structured JSONL records suitable for analysis.

The system processes one study only. The user provides the LLM; the system
provides study-specific AI Assistant, agentic tools, grounding, and deterministic
warnings.

The pipeline consists of the following stages (executed in order):

    1. **Dictionary Loading (Step 0):** Parse and validate the data dictionary
       Excel file to understand field definitions, types, and constraints.
       Writes to staging dictionary dir.
    2. **Dataset Processing (Step 1+3):** Extract tabular study datasets via
       ``process_datasets()``, landing cleaned JSONL into the staging datasets
       dir along with a list of per-column drop events.
    3. **PDF Preparation (Step 1.5):** Copy pre-extracted PDF JSON files from
       ``--pdf-source`` or run automatic extraction from annotated PDFs into
       the staging PDFs dir. If neither is available, the PDF leg is skipped.
    4. **PHI Scrub (Step 1.6):** 8-action honest-broker catalog applied to
       the staging datasets dir — keep → birthdate → drop → cap →
       generalize → suppress_small_cell → date jitter (SANT) → id
       pseudonymize (HMAC-SHA256). ~200 Indo-VAP-calibrated rules in
       ``scripts/security/phi_scrub.yaml``. Runs BEFORE cleanup so the
       dataset audit never records raw PHI. No-op when the YAML is absent.
    5. **Dataset Cleanup (Step 1.7):** Remove known junk files and merge
       structural duplicates against the staging datasets dir; emit the
       unified dataset audit report under ``output/{STUDY}/audit/``.
    6. **Cleanup Propagation (Step 1.8):** Compute the propagation drop-set
       from the dataset audit and prune matching rows/keys from staging
       dictionary + staging PDFs; emit two more leg audits.
    7. **Publish (Step 2):** Atomically promote each staging leg into
       ``trio_bundle/``. Prior trio subtrees are replaced per-leg; cross-
       filesystem rename falls back to copy-and-remove.
    8. **Variables Reference (Step 3):** Build ``trio_bundle/variables.json``
       from the newly-published trio artefacts.

    Success only: the staging workspace (``tmp/{STUDY}/``) is deleted.
    Failure path: staging is preserved for operator inspection.

Architecture:
    The pipeline follows a fail-fast philosophy. Each step is wrapped in error
    handling via `run_step()`, which logs progress and exits immediately on
    failure. Configuration is centralized in `config.py`, and all operations
    are logged to both console and `.logs/` directory.

Security:
    - Zone guards enforce data/output separation at runtime
    - Output artifacts land under ``output/{STUDY}/`` (see trio_bundle/)

Usage:
    Run the complete pipeline:
        $ python main.py --pipeline

    Skip dictionary loading:
        $ python main.py --skip-dictionary

    Verbose logging:
        $ python main.py --pipeline --verbose

Example:
    >>> # Basic pipeline execution (requires data setup)
    >>> # This is a conceptual example - actual execution requires data files
    >>> import sys
    >>> sys.argv = ['main.py', '--version']
    >>> # Would display: RePORT AI Portal <version>

Notes:
    - Requires Python 3.11+ for compatibility with dependencies
    - All data paths are configured in `config.py`
    - Shell completion available if `argcomplete` is installed
    - See README.md and Sphinx docs for detailed setup instructions

See Also:
    config.py: Central configuration and path management
    scripts.extraction.load_dictionary: Data dictionary parsing logic
    scripts.extraction.dataset_pipeline.process_datasets: Unified dataset pipeline
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
from scripts.security.phi_scrub import (
    PHIKeyMissingError,
    PHIKeyPermissionError,
    PHIScrubError,
)
from scripts.security.phi_scrub import load_key as _load_phi_key
from scripts.security.phi_scrub import run_scrub as run_phi_scrub
from scripts.utils import logging_system as log
from scripts.utils.lineage import emit_lineage_manifest
from scripts.utils.log_hygiene import install_phi_redactor
from scripts.utils.secure_staging import (
    prepare_staging,
    resolve_staging_root,
    secure_remove_tree,
)
from scripts.utils.step_cache import hash_directory, is_step_fresh, save_step_manifest

__all__ = [
    "main",
    "run_step",
]

_PIPELINE_LOCK_FILE: Any | None = None
_STREAMLIT_DEFAULT_PORT = 8501
_STREAMLIT_MAX_LOCAL_PORT = 8599


def _acquire_pipeline_lock() -> None:
    """Hold an exclusive per-study process lock for the lifetime of this run."""
    global _PIPELINE_LOCK_FILE

    lock_dir = Path(config.TMP_DIR)
    lock_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        lock_dir.chmod(0o700)
    lock_path = lock_dir / f".{config.STUDY_NAME}.pipeline.lock"
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
        fh.write(f"pid={os.getpid()}\nstudy={config.STUDY_NAME}\n")
        fh.flush()
        os.fsync(fh.fileno())
    except OSError as exc:
        fh.close()
        raise RuntimeError(
            f"Another pipeline run already holds the study lock: {lock_path}"
        ) from exc

    _PIPELINE_LOCK_FILE = fh


def _release_pipeline_lock() -> None:
    """Release the process-local pipeline lock handle."""
    global _PIPELINE_LOCK_FILE
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
treat the directory as a whole as an LLM input — only `trio_bundle/` (the
scrubbed artifacts) and `agent/` (the agent's own state) are, enforced by
`scripts.ai_assistant.file_access.validate_agent_read`. `audit/` is
off-limits to the agent.

## Layout

- `trio_bundle/` — GREEN zone. PHI-scrubbed datasets (JSONL), data dictionary
  mappings, structured PDF-form extractions, and `variables.json`. Part of
  the LLM agent's read surface (the other part is `agent/`, below). Each
  agent tool resolves every path through
  `scripts.ai_assistant.file_access.validate_agent_read`, which layers on
  top of the pipeline-side `assert_trio_bundle_zone` directory early-reject.
- `audit/` — Counts-only IRB / maintainer evidence. `lineage_manifest.json`
  pairs every raw-input SHA-256 with every published trio SHA-256 and is the
  single evidence artifact an IRB / IEC reviewer inspects. No raw values
  anywhere in this subtree. Telemetry lives here too — so the LLM cannot
  read its own prior events structurally, by directory. Hard-rejected by
  `validate_agent_read`.
- `agent/` — Per-session state. `analysis/` holds deterministic epidemiology
  outputs, and `conversations/` holds chat transcripts. The human-reviewed
  snapshot baseline lives at `data/snapshots/{study}/` and is intentionally
  OUTSIDE `agent/` — the LLM cannot read it. Readable parts of `agent/`
  (analysis and conversations) feed the agent's own session memory;
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
    pdfs_dir = staging / "pdfs"
    config.STUDY_STAGING_DIR = staging  # type: ignore[attr-defined]
    config.STAGING_DATASETS_DIR = datasets_dir  # type: ignore[attr-defined]
    config.STAGING_DICTIONARY_DIR = dictionary_dir  # type: ignore[attr-defined]
    config.STAGING_PDFS_DIR = pdfs_dir  # type: ignore[attr-defined]

    prepare_staging(
        staging,
        subdirs=(datasets_dir, dictionary_dir, pdfs_dir),
    )
    log.info("Staging workspace prepared at %s", staging)


def _publish_leg(staging_dir: Path, trio_dir: Path, leg_name: str) -> bool:
    """Publish a single leg from staging → trio_bundle.

    Idempotent: if ``staging_dir`` has no content, leaves ``trio_dir``
    untouched so a 'leg was fresh and skipped' run keeps its prior
    published output.

    The trio destination is asserted to be under the output zone. The
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
    if trio_dir.exists():
        # Use secure_remove_tree (zero-fill + symlink-aware via assert_write_zone)
        # instead of plain shutil.rmtree so a republish doesn't leave PHI-adjacent
        # forensic blocks recoverable from the disk.
        secure_remove_tree(trio_dir)

    try:
        staging_dir.rename(trio_dir)
        log.info("Published %s: %s → %s", leg_name, staging_dir, trio_dir)
    except OSError as exc:
        # Cross-filesystem rename is not supported — fall back to copy + remove
        log.warning(
            "Rename failed for %s (%s) — falling back to copytree",
            leg_name,
            exc,
        )
        shutil.copytree(staging_dir, trio_dir)
        shutil.rmtree(staging_dir, ignore_errors=True)
        log.info(
            "Published %s via copytree: %s → %s",
            leg_name,
            staging_dir,
            trio_dir,
        )
    return True


def _publish_staging() -> dict[str, bool]:
    """Publish all three staging legs into ``trio_bundle/``.

    Each leg is publish-or-skip independently: an empty staging leg
    leaves its trio counterpart untouched.
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
        "pdfs": _publish_leg(
            Path(config.STAGING_PDFS_DIR),
            Path(config.PDF_EXTRACTIONS_DIR),
            "pdfs",
        ),
    }


def _emit_output_signpost() -> None:
    """Write a README.md at ``output/{STUDY}/`` explaining the subtree layout.

    The signpost is a plain-text orientation aid for anyone who opens the
    per-study output directory on a shared machine without prior context —
    IRB reviewers browsing evidence, sysadmins auditing disk usage, future
    maintainers. It names every top-level subdirectory (``trio_bundle/`` =
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

    Independent of the dataset and PDF legs, so safe to run in parallel
    with both. No PHI in the dictionary itself, so no scrub needed
    here — the cleanup-propagation step still prunes dropped variables
    later.
    """
    if skip:
        log.info("--- Skipping Step 0: Data Dictionary Loading ---")
        return {"leg": "dictionary", "skipped": True}
    log.info("Leg [dictionary]: starting extraction → %s", config.STAGING_DICTIONARY_DIR)
    load_study_dictionary(dictionary_dir=str(config.DATA_DICTIONARY_DIR))
    log.info("Leg [dictionary]: complete")
    return {"leg": "dictionary", "skipped": False}


def _run_dataset_leg(*, force: bool, run_extraction: bool) -> dict[str, Any]:
    """Extract datasets into ``STAGING_DATASETS_DIR`` with input-hash cache.

    Returns ``dropped_events`` so the cleanup chain can mirror the
    extraction-time drops into the dictionary + PDF legs. Safe to run in
    parallel with the dictionary + PDF legs (different input dir,
    different staging subdir, no shared mutable state).
    """
    if not run_extraction:
        return {"leg": "datasets", "skipped": True, "dropped_events": []}

    raw_datasets_dir = Path(config.DATASETS_DIR)
    datasets_input_hashes = hash_directory(
        raw_datasets_dir, extensions=frozenset({".xlsx", ".xls", ".csv"})
    )
    audit_dir = Path(config.STUDY_AUDIT_DIR)
    trio_datasets_dir = Path(config.TRIO_DATASETS_DIR)

    trio_bundle_has_jsonl = trio_datasets_dir.is_dir() and any(trio_datasets_dir.glob("*.jsonl"))

    if (
        not force
        and trio_bundle_has_jsonl
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


def _run_pdf_leg(
    *,
    pdf_source: str | None,
    run_pdf_extraction: bool,
) -> dict[str, Any]:
    """PDF preparation into ``STAGING_PDFS_DIR`` (one of three branches).

    The PDF leg is the most likely to skip because it has the most
    failure modes: no source PDFs, no LLM provider, capability gate
    fails, network unreachable. Every skip path emits a *detailed*
    diagnostic so an operator running ``--pipeline`` headlessly can
    tell exactly why their bundle has no ``pdfs/`` leg.

    Detailed skip-reason logging (PR #18 directive):
        - missing source dir → log path + how to fix
        - missing PDFs in source → log glob pattern + dir contents
        - --pdf-source mode failures → log src + dest + which file
        - extract_pdfs_to_jsonl errors → log per-file error list
        - all-empty result → log distinguishing "no PDFs" from "all failed"
    """
    if not run_pdf_extraction:
        log.info("Leg [pdfs]: skipped (--build-bundle / --pipeline not set)")
        return {"leg": "pdfs", "skipped": True, "files_created": 0, "errors": []}

    pdf_extractions_dir = Path(config.STAGING_PDFS_DIR)
    from scripts.security.secure_env import assert_write_zone

    assert_write_zone(pdf_extractions_dir)

    # Branch (a): explicit --pdf-source path
    if pdf_source is not None:
        from scripts.security.secure_env import assert_not_raw

        source_path = Path(pdf_source)
        if not source_path.is_dir():
            msg = f"--pdf-source directory not found: {source_path}"
            log.error("Leg [pdfs]: %s", msg)
            print(f"\n❌ {msg}")
            sys.exit(1)
        try:
            assert_not_raw(source_path)
        except Exception as exc:
            log.error("Leg [pdfs]: --pdf-source rejected by zone guard: %s", exc)
            print(f"\n❌ --pdf-source is in the raw data zone: {source_path}")
            print("   Pre-extracted PDF files should be in an output/ or external directory.")
            sys.exit(1)

        json_files = sorted(
            p
            for p in source_path.glob("*_variables.json")
            if p.is_file() and not p.name.startswith(".") and not p.name.startswith("~")
        )
        if not json_files:
            msg = f"--pdf-source directory contains no *_variables.json files: {source_path}"
            log.error("Leg [pdfs]: %s", msg)
            print(f"\n❌ No *_variables.json files found in: {source_path}")
            sys.exit(1)

        pdf_extractions_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for src_file in json_files:
            dest_file = pdf_extractions_dir / src_file.name
            tmp_dest = dest_file.with_suffix(".tmp")
            try:
                shutil.copy2(str(src_file), str(tmp_dest))
                tmp_dest.replace(dest_file)
                copied += 1
            except OSError as exc:
                log.error("Leg [pdfs]: copy failed %s: %s", src_file.name, exc)
                if tmp_dest.exists():
                    tmp_dest.unlink()
                raise

        log.info(
            "Leg [pdfs]: copied %d pre-extracted JSON files from %s → %s",
            copied,
            source_path,
            pdf_extractions_dir,
        )
        print(f"  ✓ Copied {copied} PDF extraction files from {source_path}")
        return {"leg": "pdfs", "skipped": False, "files_created": copied, "errors": []}

    # Branch (b): automatic extraction from annotated_pdfs/
    annotated_pdfs_dir = Path(config.ANNOTATED_PDFS_DIR)
    if not (annotated_pdfs_dir.is_dir() and list(annotated_pdfs_dir.glob("*.pdf"))):
        # Branch (c): no source PDFs at all — detailed log so a debugger
        # can tell whether the dir is missing entirely vs. dir exists but is empty.
        if not annotated_pdfs_dir.exists():
            msg = (
                f"PDF leg skipped: source directory does not exist at {annotated_pdfs_dir}. "
                "Create it and add annotated PDFs, or run with --pdf-source <path> "
                "to use pre-extracted JSON files instead."
            )
        elif not annotated_pdfs_dir.is_dir():
            msg = (
                f"PDF leg skipped: {annotated_pdfs_dir} exists but is not a directory. "
                "Inspect the path and replace with a directory of *.pdf files."
            )
        else:
            try:
                contents = sorted(p.name for p in annotated_pdfs_dir.iterdir())[:10]
            except OSError:
                contents = []
            msg = (
                f"PDF leg skipped: no *.pdf files found in {annotated_pdfs_dir}. "
                f"Directory contents (up to 10): {contents}. "
                "Add annotated PDFs to enable the PDF leg, or pass "
                "--pdf-source <path> to copy pre-extracted JSON files."
            )
        log.warning("Leg [pdfs]: %s", msg)
        print(f"  ⚠ {msg}")
        return {"leg": "pdfs", "skipped": True, "files_created": 0, "errors": []}

    try:
        from scripts.extraction.extract_pdf_data import extract_pdfs_to_jsonl

        log.info("Leg [pdfs]: starting automatic extraction from %s", annotated_pdfs_dir)
        pdf_result = extract_pdfs_to_jsonl(pdf_dir=annotated_pdfs_dir)
        files_created = int(pdf_result.get("files_created", 0))
        pdf_errors = pdf_result.get("errors", []) or []
        if files_created > 0:
            log.info(
                "Leg [pdfs]: complete (%d files created, %d errors)",
                files_created,
                len(pdf_errors),
            )
            print(f"  ✓ PDF extraction: {files_created} files created")
        elif pdf_errors:
            # All-failed case — emit the per-file error list at WARNING.
            log.warning(
                "Leg [pdfs]: extraction failed for ALL %d files. Per-file errors:",
                len(pdf_errors),
            )
            for err in pdf_errors[:20]:
                log.warning(
                    "  - %s: %s",
                    err.get("file", "<unknown>"),
                    err.get("error", "<no message>"),
                )
            if len(pdf_errors) > 20:
                log.warning("  … and %d more (full list in log)", len(pdf_errors) - 20)
            print(
                f"  ⚠ PDF extraction failed ({len(pdf_errors)} errors) "
                f"— study forms will not be included. See log for per-file detail."
            )
        else:
            log.warning(
                "Leg [pdfs]: extraction produced no files (no errors recorded — "
                "the source PDFs may be image-only without text, or the LLM "
                "returned empty responses). Inspect %s and verify PDF text "
                "is selectable. Pass --pdf-source <path> to skip the LLM tier.",
                annotated_pdfs_dir,
            )
            print("  ⚠ PDF extraction produced no files — study forms not included")
        return {
            "leg": "pdfs",
            "skipped": False,
            "files_created": files_created,
            "errors": pdf_errors,
        }
    except Exception as exc:
        log.warning(
            "Leg [pdfs]: extraction crashed: %s — study forms will not be included. "
            "Tip: provide pre-extracted files with --pdf-source <path>.",
            exc,
            exc_info=True,
        )
        print(
            f"  ⚠ PDF extraction failed: {exc}\n"
            f"    Tip: provide pre-extracted files with --pdf-source <path>"
        )
        return {
            "leg": "pdfs",
            "skipped": True,
            "files_created": 0,
            "errors": [{"file": "", "error": str(exc)}],
        }


def _pdf_needs_snapshot_restore(pdf_leg_result: dict[str, Any]) -> bool:
    """Return True when the PDF leg could not produce a clean fresh result."""

    return bool(
        pdf_leg_result.get("skipped")
        or pdf_leg_result.get("errors")
        or int(pdf_leg_result.get("files_created", 0) or 0) == 0
    )


def _restore_reviewed_snapshot_if_available(reason: str) -> bool:
    """Restore ``data/snapshots/{STUDY}/`` over the live trio bundle if present."""

    from scripts.utils.snapshots import SnapshotError, restore_snapshot, snapshot_exists

    if not snapshot_exists():
        log.warning("Reviewed snapshot unavailable; cannot restore fallback after %s", reason)
        return False
    try:
        path = restore_snapshot()
    except SnapshotError as exc:
        log.error("Reviewed snapshot restore failed after %s: %s", reason, exc)
        return False
    log.warning("Reviewed snapshot restored after %s: %s", reason, path)
    print(f"  ✓ Restored reviewed snapshot after {reason}: {path}")
    return True


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
        log.error(f"Error in {step_name}: {e}", exc_info=True)
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
        to trio_bundle/datasets/, and removes temp workspace.
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
  %(prog)s --pipeline                   # Full pipeline: Extract → Promote → Bundle

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
        "--build-variables",
        action="store_true",
        help="Build unified variables.json from all annotation sources (extractions, dictionary)",
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

    # LLM provider / model overrides (apply to chat, web, pipeline, PDF extraction)
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

    # Dataset processing + Bundle
    parser.add_argument(
        "--build-bundle",
        action="store_true",
        help="Build the trio bundle contents (dictionary + PDF extractions)",
    )
    parser.add_argument(
        "--process-datasets",
        action="store_true",
        help="Extract raw datasets and promote to trio_bundle/datasets/ (unified pipeline)",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="Run full pipeline: Extract → Promote → Bundle",
    )
    parser.add_argument(
        "--pdf-source",
        type=str,
        default=None,
        metavar="PATH",
        help="Directory of pre-extracted PDF JSON files to copy into the trio bundle. "
        "When omitted, automatic PDF extraction is attempted.",
    )

    args = parser.parse_args()

    # Apply LLM overrides early — affects all paths (chat, web, pipeline, PDF).
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

    if getattr(args, "build_variables", False):
        from scripts.extraction.build_variables_reference import build_variables_reference

        log.setup_logger(
            name=config.LOG_NAME,
            log_level=logging.DEBUG if args.verbose else logging.INFO,
            simple_mode=not args.verbose,
            verbose=args.verbose,
        )
        _install_log_redactor_best_effort()
        config.ensure_directories()
        run_step(
            "Build Variables Reference",
            lambda: build_variables_reference(
                trio_bundle_dir=config.TRIO_BUNDLE_DIR,
                output_path=config.VARIABLES_JSON_PATH,
                tmp_dir=config.TMP_DIR,
            ),
        )
        return

    # --build-bundle: dictionary + PDF preparation, NO dataset processing.
    if args.build_bundle:
        args.skip_dictionary = False

    # --pipeline expands to the full chain:
    #   Dict(0) → ProcessDatasets(1+3) → Bundle → Variables(3)
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
        print(f"  data/raw/{config.STUDY_NAME}/annotated_pdfs/  (optional)")
        sys.exit(1)

    # Ensure required directories exist
    config.ensure_directories()

    # Purge + prepare the per-run staging workspace. Extraction legs write here
    # first; publish step atomically promotes them into trio_bundle/.
    _prepare_staging()

    # Display startup banner
    print("\n" + "=" * 70)
    print("RePORT AI Portal - Report India Clinical Study Data Pipeline")
    print("=" * 70 + "\n")

    force = args.force

    # ── Steps 0 + 1 + 1.5: PARALLEL EXTRACTION PHASE ──
    # Dictionary, datasets, and PDFs each read different RED inputs and write
    # to different AMBER staging subdirs — they are fully decoupled, so we
    # run them concurrently to amortise PDF-orchestrator HTTP latency against
    # Excel parsing CPU. Cleanup chain (PHI scrub / dataset cleanup /
    # propagation) and Publish + variables.json are sequential AFTER the join
    # because they have hard data dependencies on the extraction results.
    print("\n--- Parallel extraction phase: Dictionary | Datasets | PDFs ---")
    log.info("Starting parallel extraction phase (max_workers=3)")

    dropped_events: list[dict[str, Any]] = []
    pdf_leg_result: dict[str, Any] = {"skipped": True, "files_created": 0, "errors": []}
    extraction_failures: list[tuple[str, BaseException]] = []
    extraction_start = time.time()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures: dict[Any, str] = {
            executor.submit(_run_dict_leg, skip=args.skip_dictionary): "dictionary",
            executor.submit(
                _run_dataset_leg,
                force=force,
                run_extraction=args.process_datasets and not args.skip_datasets,
            ): "datasets",
            executor.submit(
                _run_pdf_leg,
                pdf_source=args.pdf_source,
                run_pdf_extraction=bool(args.build_bundle or args.pipeline),
            ): "pdfs",
        }
        for fut in as_completed(futures):
            leg_name = futures[fut]
            try:
                result = fut.result()
            except BaseException as exc:
                extraction_failures.append((leg_name, exc))
                log.error("Leg [%s] crashed: %s", leg_name, exc, exc_info=True)
                continue
            if leg_name == "datasets":
                events = result.get("dropped_events", [])
                if isinstance(events, list):
                    dropped_events = events
            elif leg_name == "pdfs":
                pdf_leg_result = result

    extraction_elapsed = time.time() - extraction_start
    log.info(
        "Parallel extraction phase complete in %.1fs "
        "(dataset drops: %d, pdfs created: %d, pdf errors: %d)",
        extraction_elapsed,
        len(dropped_events),
        int(pdf_leg_result.get("files_created", 0) or 0),
        len(pdf_leg_result.get("errors", []) or []),
    )

    # Hard fail if a non-PDF leg crashed: the PDF leg is allowed to skip
    # gracefully (its absence is logged in detail), but a dictionary or
    # dataset crash leaves the cleanup chain with no input and we'd rather
    # surface the failure here than corrupt trio_bundle.
    blocking_failures = [(leg, err) for leg, err in extraction_failures if leg != "pdfs"]
    if blocking_failures:
        for leg, err in blocking_failures:
            print(f"\n❌ Extraction leg [{leg}] failed: {err}")
        sys.exit(1)
    # Replay any PDF leg crash now that we know the others survived.
    for leg, err in extraction_failures:
        if leg == "pdfs":
            log.warning(
                "PDF leg crashed during parallel dispatch; continuing without "
                "PDF outputs. Cause: %s",
                err,
            )

    if (
        bool(args.build_bundle or args.pipeline)
        and _pdf_needs_snapshot_restore(pdf_leg_result)
        and _restore_reviewed_snapshot_if_available("PDF extraction failure or skip")
    ):
        _cleanup_staging()
        print("\nPipeline used the reviewed snapshot baseline; fresh extraction was skipped.")
        return

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
                lambda: run_phi_scrub(config.STUDY_NAME),
            )

    # ── Step 1.7: Dataset Cleanup (remove junk, merge duplicates) ──
    # Runs against the STAGING datasets tree before publish. The staging
    # layout ensures the audit envelope + propagation inputs are complete
    # before trio_bundle/datasets/ is re-materialised.
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

    # ── Step 1.8: Cleanup Propagation (dictionary + PDF pruning) ──
    # Mirrors dataset drops into the dictionary and PDF staging trees. Safe
    # no-op when nothing was dropped (emits empty-but-valid leg audits).
    # Runs AFTER Steps 1.5/1.7 so both dict + pdf staging are populated and
    # the dataset audit is on disk.
    if Path(config.STAGING_DICTIONARY_DIR).is_dir() or Path(config.STAGING_PDFS_DIR).is_dir():
        run_step(
            "Step 1.8: Cleanup Propagation",
            lambda: run_propagation(),
        )

    # ── Step 2: Publish Staging → Trio Bundle ──
    # Atomic-rename each staging leg into trio_bundle/; empty legs leave
    # their trio counterpart untouched so a skipped-fresh leg keeps its
    # prior publish.
    def run_publish() -> None:
        published = _publish_staging()
        published_legs = {k: v for k, v in published.items() if v}
        if published_legs:
            log.info("Published legs: %s", sorted(published_legs))
        else:
            log.info("Publish: all legs skipped (staging empty)")

    run_step("Step 2: Publish Staging → Trio Bundle", run_publish)

    # ── Step 3: Variables Reference (variables.json) ──
    # Runs AFTER publish so build_variables_reference scans the populated
    # trio_bundle tree, not the now-empty staging tree.
    if args.pipeline:
        from scripts.extraction.build_variables_reference import build_variables_reference

        run_step(
            "Step 3: Build Variables Reference",
            lambda: build_variables_reference(
                trio_bundle_dir=config.TRIO_BUNDLE_DIR,
                output_path=config.VARIABLES_JSON_PATH,
                tmp_dir=config.TMP_DIR,
            ),
        )

    # ── Step 4: Lineage Manifest (audit-ready evidence package) ──
    # Emits output/{STUDY}/audit/lineage_manifest.json pairing every raw
    # input file (SHA-256) with every published trio artifact (SHA-256),
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
            trio_bundle_dir=Path(config.TRIO_BUNDLE_DIR),
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
    trio = Path(config.TRIO_BUNDLE_DIR)
    print(f"\n  Trio Bundle Root:    {trio}")
    print(f"    Datasets (JSONL):  {config.TRIO_DATASETS_DIR}")
    print(f"    Data Dictionary:   {config.DICTIONARY_JSON_OUTPUT_DIR}")
    print(f"    PDF Extractions:   {config.PDF_EXTRACTIONS_DIR}")
    print(f"    Audit Reports:     {config.STUDY_AUDIT_DIR}")
    print(f"      Dataset Audit:     {config.AUDIT_DATASET_REPORT_PATH}")
    print(f"      PHI Scrub Audit:   {config.AUDIT_SCRUB_REPORT_PATH}")
    print(f"  Agent State Root:    {config.AGENT_STATE_DIR}")
    print(f"    Analysis Output:   {config.AGENT_OUTPUT_DIR}")
    print(f"    Conversations:     {config.CONVERSATIONS_DIR}")
    print(f"    Telemetry:         {config.TELEMETRY_DIR}")
    print(f"    Reviewed Snapshot: {config.STUDY_SNAPSHOTS_DIR}")
    if args.pipeline:
        print(f"    Variables JSON:    {config.VARIABLES_JSON_PATH}")
    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    main()
