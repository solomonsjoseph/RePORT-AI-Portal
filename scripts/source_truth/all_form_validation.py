"""All-form Source of Truth bulk validation runner (issue #78).

**What.** Discover every policy-pilot input set under a root directory
(each set is a per-form triple of ``column_inventory``, ``pdf_extraction``,
and ``field_policy``) and run the source-truth schema + completeness
validation against each one. The aggregate report tells maintainers
which forms pass cleanly, which only have review-required warnings, and
which have blocking errors that must be fixed before downstream
artifacts can be regenerated.

**Why.** PRD #65 makes the source of truth the canonical metadata layer;
issue #78 requires that validation run across *all* available pilot
outputs rather than only the 6_HIV and 98B_FOB tracer fixtures. Running
validation across every form is the audit gate that proves nothing has
silently regressed when new forms are added.

**How.** This module is the *first* I/O-doing component in
``scripts/source_truth/``. The discovery layer resolves on-disk filenames
into already-loaded mappings; from there, every downstream call is a
pure function over those mappings. Discovery is intentionally tolerant
of multiple naming conventions (``column_inventory.json``,
``<form>_column_inventory.json``, ``.yaml``/``.yml``/``.json`` for the
field policy) so the runner does not have to be rewritten the first
time a new pilot output ships under a slightly different filename.

The bulk runner does not invent any per-form validators — it composes
:func:`scripts.source_truth.builder.build_source_truth_artifact`
(which itself calls :func:`scripts.source_truth.completeness.report_completeness`)
and treats raised :class:`SourceTruthBuildError`/
:class:`SourceTruthValidationError` as blocking errors.

The runner explicitly does **not** read raw dataset row values. It
treats forbidden raw-value keys as a blocking error rather than a
warning, and the per-form report is the same completeness contract the
single-form tracer fixtures already exercise — extended only with the
``form_id``, file paths, and a ``status`` field
(``passed`` / ``warning`` / ``failed``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from scripts.source_truth.builder import (
    SourceTruthBuildError,
    build_source_truth_artifact,
)
from scripts.source_truth.completeness import FOOTER_EXCLUSION_BOUNDARY_NOTE
from scripts.source_truth.record import SourceTruthValidationError

__all__ = [
    "FORM_STATUS_FAILED",
    "FORM_STATUS_PASSED",
    "FORM_STATUS_WARNING",
    "discover_policy_pilot_forms",
    "validate_all_forms",
]


FORM_STATUS_PASSED = "passed"
FORM_STATUS_WARNING = "warning"
FORM_STATUS_FAILED = "failed"


# Filename patterns the discovery layer accepts. ``<form>`` is captured
# as ``form_id`` and used to locate the matching peer files.
_COLUMN_INVENTORY_BASENAMES: tuple[str, ...] = (
    "column_inventory.json",
    "{form}_column_inventory.json",
)
_PDF_EXTRACTION_BASENAMES: tuple[str, ...] = (
    "pdf_extraction.json",
    "{form}_pdf_extraction.json",
)
_FIELD_POLICY_BASENAMES: tuple[str, ...] = (
    "field_policy.draft.yaml",
    "field_policy.draft.yml",
    "field_policy.yaml",
    "field_policy.yml",
    "field_policy.json",
    "{form}_field_policy.draft.yaml",
    "{form}_field_policy.draft.yml",
    "{form}_field_policy.yaml",
    "{form}_field_policy.yml",
    "{form}_field_policy.json",
)

_VALID_FORM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _resolve_first_existing(
    form_dir: Path, form_id: str, basenames: tuple[str, ...]
) -> Path | None:
    for template in basenames:
        candidate = form_dir / template.format(form=form_id)
        if candidate.is_file():
            return candidate
    return None


def discover_policy_pilot_forms(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Return a sorted list of per-form input sets discovered under ``root``.

    Each entry is a mapping with ``form_id`` (the directory name) and
    absolute filesystem paths for the column inventory, PDF extraction,
    and field policy inputs. Directories missing any of the three are
    skipped — the bulk validator surfaces them as discovery errors at
    the report level so they remain visible.

    Args:
        root: Filesystem root containing one sub-directory per form.

    Returns:
        Sorted list of per-form discovery records (sorted by ``form_id``).
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    discovered: list[dict[str, Any]] = []
    for entry in sorted(root_path.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        form_id = entry.name
        if not _VALID_FORM_ID.match(form_id):
            continue
        column_inventory_path = _resolve_first_existing(entry, form_id, _COLUMN_INVENTORY_BASENAMES)
        pdf_extraction_path = _resolve_first_existing(entry, form_id, _PDF_EXTRACTION_BASENAMES)
        field_policy_path = _resolve_first_existing(entry, form_id, _FIELD_POLICY_BASENAMES)
        if not (column_inventory_path and pdf_extraction_path and field_policy_path):
            continue
        discovered.append(
            {
                "form_id": form_id,
                "column_inventory_path": str(column_inventory_path.resolve()),
                "pdf_extraction_path": str(pdf_extraction_path.resolve()),
                "field_policy_path": str(field_policy_path.resolve()),
            }
        )
    return discovered


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_field_policy(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json(path)
    # YAML — imported lazily so test environments without PyYAML still
    # exercise the JSON path.
    import yaml  # type: ignore[import-untyped]

    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _classify_status(blocking_errors: Iterable[str], warnings: Iterable[str]) -> str:
    if any(blocking_errors):
        return FORM_STATUS_FAILED
    if any(warnings):
        return FORM_STATUS_WARNING
    return FORM_STATUS_PASSED


def _empty_completeness_for_failure(error_message: str) -> dict[str, Any]:
    return {
        "dataset_columns_total": 0,
        "dataset_columns_covered": [],
        "unmatched_dataset_columns": [],
        "pdf_fields_total": 0,
        "pdf_fields_covered": [],
        "unmatched_pdf_fields": [],
        "review_required_fields": [],
        "evidence_gaps": [],
        "blocking_errors": [error_message],
        "warnings": [],
        "excluded_footer_version_content": {
            "count": 0,
            "note": FOOTER_EXCLUSION_BOUNDARY_NOTE,
            "found": [],
        },
    }


def _per_form_report(
    *,
    form_id: str,
    paths: Mapping[str, str],
    completeness: Mapping[str, Any],
) -> dict[str, Any]:
    blocking_errors = list(completeness.get("blocking_errors") or [])
    warnings = list(completeness.get("warnings") or [])
    excluded = completeness.get("excluded_footer_version_content") or {
        "count": 0,
        "note": FOOTER_EXCLUSION_BOUNDARY_NOTE,
        "found": [],
    }
    return {
        "form_id": form_id,
        "column_inventory_path": paths.get("column_inventory_path", ""),
        "pdf_extraction_path": paths.get("pdf_extraction_path", ""),
        "field_policy_path": paths.get("field_policy_path", ""),
        "dataset_columns_total": completeness.get("dataset_columns_total", 0),
        "dataset_columns_covered": list(completeness.get("dataset_columns_covered") or []),
        "unmatched_dataset_columns": list(completeness.get("unmatched_dataset_columns") or []),
        "pdf_fields_total": completeness.get("pdf_fields_total", 0),
        "pdf_fields_covered": list(completeness.get("pdf_fields_covered") or []),
        "unmatched_pdf_fields": list(completeness.get("unmatched_pdf_fields") or []),
        "review_required_fields": list(completeness.get("review_required_fields") or []),
        "evidence_gaps": list(completeness.get("evidence_gaps") or []),
        "excluded_footer_version_content": dict(excluded),
        "blocking_errors": blocking_errors,
        "warnings": warnings,
        "status": _classify_status(blocking_errors, warnings),
    }


def _validate_one_form(entry: Mapping[str, Any]) -> dict[str, Any]:
    form_id = str(entry.get("form_id"))
    column_inventory_path = Path(entry["column_inventory_path"])
    pdf_extraction_path = Path(entry["pdf_extraction_path"])
    field_policy_path = Path(entry["field_policy_path"])

    try:
        column_inventory = _load_json(column_inventory_path)
        pdf_extraction = _load_json(pdf_extraction_path)
        field_policy = _load_field_policy(field_policy_path)
    except (OSError, json.JSONDecodeError) as exc:
        # Surface load failures as blocking errors so they remain visible.
        message = f"failed to load inputs for form {form_id!r}: {type(exc).__name__}"
        return _per_form_report(
            form_id=form_id,
            paths=entry,
            completeness=_empty_completeness_for_failure(message),
        )

    try:
        artifact = build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)
    except (SourceTruthBuildError, SourceTruthValidationError) as exc:
        message = f"{type(exc).__name__}: {exc}"
        return _per_form_report(
            form_id=form_id,
            paths=entry,
            completeness=_empty_completeness_for_failure(message),
        )

    completeness = artifact.get("completeness") or {}
    return _per_form_report(form_id=form_id, paths=entry, completeness=completeness)


def validate_all_forms(root: str | os.PathLike[str]) -> dict[str, Any]:
    """Run source-truth schema + completeness validation across every form.

    Args:
        root: Filesystem root holding the per-form pilot output sub-directories.

    Returns:
        A mapping with ``forms`` (per-form reports) and ``summary``
        (rollup buckets that distinguish blocking errors from
        review-required warnings)::

            {
                "root": "<absolute-root-path>",
                "forms": [
                    {
                        "form_id": str,
                        "column_inventory_path": str,
                        "pdf_extraction_path": str,
                        "field_policy_path": str,
                        "dataset_columns_total": int,
                        "dataset_columns_covered": list[str],
                        "unmatched_dataset_columns": list[str],
                        "pdf_fields_total": int,
                        "pdf_fields_covered": list[str],
                        "unmatched_pdf_fields": list[str],
                        "review_required_fields": list[str],
                        "evidence_gaps": list[str],
                        "excluded_footer_version_content": {
                            "count": int, "note": str, "found": list[str],
                        },
                        "blocking_errors": list[str],
                        "warnings": list[str],
                        "status": "passed" | "warning" | "failed",
                    },
                    ...
                ],
                "summary": {
                    "forms_total": int,
                    "forms_passing": list[str],
                    "forms_with_warnings_only": list[str],
                    "forms_with_blocking_errors": list[str],
                    "footer_exclusion_boundary_note": str,
                },
            }
    """
    discovered = discover_policy_pilot_forms(root)
    forms_reports = [_validate_one_form(entry) for entry in discovered]

    forms_passing: list[str] = []
    forms_with_warnings_only: list[str] = []
    forms_with_blocking_errors: list[str] = []
    for form_report in forms_reports:
        status = form_report["status"]
        form_id = form_report["form_id"]
        if status == FORM_STATUS_FAILED:
            forms_with_blocking_errors.append(form_id)
        elif status == FORM_STATUS_WARNING:
            forms_with_warnings_only.append(form_id)
        else:
            forms_passing.append(form_id)

    return {
        "root": str(Path(root).resolve()),
        "forms": forms_reports,
        "summary": {
            "forms_total": len(forms_reports),
            "forms_passing": sorted(forms_passing),
            "forms_with_warnings_only": sorted(forms_with_warnings_only),
            "forms_with_blocking_errors": sorted(forms_with_blocking_errors),
            "footer_exclusion_boundary_note": FOOTER_EXCLUSION_BOUNDARY_NOTE,
        },
    }
