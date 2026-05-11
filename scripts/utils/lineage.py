"""Per-run lineage manifest for the RePORT AI Portal pipeline.

Regulators auditing a clinical de-identification pipeline want a single
artifact that ties every input file to every output file with hashes and
timestamps at each transformation step. This module produces that
artifact as ``output/{STUDY}/audit/lineage_manifest.json``.

The manifest records:

* **Run metadata** — pipeline version, extraction engine, UTC timestamp,
  compliance posture, study name.
* **Inputs** — every raw file that entered the pipeline this run, with
  SHA-256 + size + mtime.
* **Outputs** — every file in the published ``llm_source/`` + every
  audit report, with SHA-256 + size.
* **Steps** — per-leg (datasets / dictionary / pdfs) timestamps and
  rule-action counts (read from existing audit reports; this module
  does NOT re-compute scrub events).

The manifest carries only counts and hashes — never raw PHI values.
Caller must ensure :func:`emit_lineage_manifest` runs AFTER
``_publish_staging`` so the llm_source bundle exists and AFTER all audit
reports are on disk.

IRB-grade benchmark anchors:
    * NIST SP 800-188 §7 governance + audit
    * FDA 21 CFR Part 11 §11.10(e) audit record requirements
    * ICMR 2017 §11.5 audit + confidentiality
    * CDISC ODM origin/source traceability
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.extraction.io import atomic_write_json
from scripts.security.secure_env import assert_output_zone
from scripts.utils.integrity import hash_file as hash_path

logger = logging.getLogger(__name__)

__all__ = [
    "LineageManifestError",
    "emit_lineage_manifest",
    "hash_path",
]


class LineageManifestError(Exception):
    """Raised when the lineage manifest cannot be assembled."""


def _file_metadata(path: Path) -> dict[str, Any]:
    """Return ``{sha256, size, mtime_utc}`` for a regular file."""
    stat_result = path.stat()
    return {
        "sha256": hash_path(path),
        "size_bytes": stat_result.st_size,
        "mtime_utc": datetime.fromtimestamp(stat_result.st_mtime, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


def _collect_files(root: Path, *, recursive: bool = True) -> list[dict[str, Any]]:
    """Return file-metadata records for every regular file below *root*.

    Files are sorted by POSIX path for deterministic manifest output. Dot-
    files (``.*``) and temp-write artifacts (``*.tmp``) are skipped.
    """
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    iterator = root.rglob("*") if recursive else root.iterdir()
    for entry in sorted(iterator):
        if not entry.is_file():
            continue
        if entry.name.startswith("."):
            continue
        if entry.suffix == ".tmp":
            continue
        try:
            meta = _file_metadata(entry)
        except OSError as exc:
            logger.warning("lineage: could not stat %s: %s", entry, exc)
            continue
        meta["path"] = str(entry.relative_to(root))
        records.append(meta)
    return records


def _load_audit_counts(audit_path: Path) -> dict[str, Any] | None:
    """Return the audit payload at *audit_path*, or None if absent / malformed."""
    if not audit_path.is_file():
        return None
    try:
        parsed = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("lineage: could not read audit %s: %s", audit_path, exc)
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def emit_lineage_manifest(
    *,
    study_name: str,
    raw_datasets_dir: Path,
    raw_dictionary_dir: Path | None,
    raw_pdfs_dir: Path | None,
    llm_source_dir: Path,
    audit_dir: Path,
    pipeline_version: str,
    compliance_posture: str,
    manifest_path: Path,
    phi_key_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Assemble + atomically write the lineage manifest for this run.

    Returns the manifest payload (dict) so callers may log a summary.

    Zone guard: *manifest_path* is asserted against the output zone so a
    mis-configured audit dir fails fast.
    """
    assert_output_zone(manifest_path.parent)

    inputs = {
        "datasets": _collect_files(raw_datasets_dir),
    }
    if raw_dictionary_dir is not None:
        inputs["dictionary"] = _collect_files(raw_dictionary_dir)
    if raw_pdfs_dir is not None:
        inputs["pdfs"] = _collect_files(raw_pdfs_dir)

    outputs = {
        "llm_source": _collect_files(llm_source_dir),
        "audit": _collect_files(audit_dir, recursive=False),
    }

    steps: dict[str, Any] = {}
    for leg, audit_filename in (
        ("phi_scrub", "phi_scrub_report.json"),
        ("dataset_cleanup", "dataset_cleanup_report.json"),
        ("dictionary_cleanup", "dictionary_cleanup_report.json"),
        ("pdfs_cleanup", "pdfs_cleanup_report.json"),
    ):
        payload = _load_audit_counts(audit_dir / audit_filename)
        if payload is None:
            continue
        steps[leg] = {
            "audit_file": audit_filename,
            "posture": payload.get("compliance_posture"),
            "event_count": len(payload.get("scrubbed", []))
            if isinstance(payload.get("scrubbed"), list)
            else None,
            "generated_utc": payload.get("generated_utc"),
        }

    manifest: dict[str, Any] = {
        "study": study_name,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline_version": pipeline_version,
        "compliance_posture": compliance_posture,
        "inputs": inputs,
        "outputs": outputs,
        "steps": steps,
    }
    # The PHI key fingerprint (SHA-256 of the HMAC key bytes) lets an IRB
    # reviewer verify that the pseudonyms in llm_source/ were generated
    # with the claimed key — without exposing the key itself. Optional so
    # legacy callers without a key still emit a valid manifest.
    if phi_key_fingerprint is not None:
        manifest["phi_key_fingerprint"] = phi_key_fingerprint

    atomic_write_json(manifest_path, manifest)
    logger.info(
        "lineage manifest: %d input files, %d llm_source output files, %d steps",
        sum(len(v) for v in inputs.values()),
        len(outputs["llm_source"]),
        len(steps),
    )
    return manifest
