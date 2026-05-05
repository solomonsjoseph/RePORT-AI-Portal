"""Dataset Schema sidecar derived from Study Variable Source Truth."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.source_truth.builder import DERIVATION_DATASET_SCHEMA
from scripts.source_truth.record import (
    FORBIDDEN_ARTIFACT_VERSION_KEYS,
    FORBIDDEN_RAW_VALUE_KEYS,
)

__all__ = [
    "DatasetSchemaError",
    "build_dataset_schema",
    "get_dataset_schema_status",
    "resolve_analysis_binding",
]


class DatasetSchemaError(ValueError):
    """Raised when a dataset schema cannot bind a selected variable."""


_FORBIDDEN_SCHEMA_KEYS = FORBIDDEN_RAW_VALUE_KEYS | FORBIDDEN_ARTIFACT_VERSION_KEYS


def _forbidden_key_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in _FORBIDDEN_SCHEMA_KEYS:
                found.append(child_path)
            found.extend(_forbidden_key_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_key_paths(child, f"{path}[{index}]"))
    return found


def _variable_id(record: Mapping[str, Any]) -> str:
    value = record.get("variable_id")
    if not isinstance(value, str) or not value:
        raise DatasetSchemaError("source-truth records must carry a string variable_id")
    return value


def _dataset_column(record: Mapping[str, Any]) -> str:
    variable_id = _variable_id(record)
    dataset = record.get("presence", {}).get("dataset", {})
    column = dataset.get("column") if isinstance(dataset, Mapping) else None
    if not isinstance(column, str) or not column:
        raise DatasetSchemaError(f"{variable_id} has no dataset column to bind")
    return column


def _source_truth_ref(variable_id: str) -> dict[str, str]:
    return {
        "artifact_type": "study_variable_source_truth",
        "variable_id": variable_id,
    }


def _pending_catalog_ref(variable_id: str) -> dict[str, str]:
    return {
        "status": "pending_catalog_generation",
        "variable_id": variable_id,
    }


def _entry_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    variable_id = _variable_id(record)
    normalized = record.get("normalized", {})
    if not isinstance(normalized, Mapping):
        raise DatasetSchemaError(f"{variable_id} normalized metadata must be a mapping")

    action = normalized.get("handling_action")
    reason = normalized.get("handling_reason")
    return {
        "variable_id": variable_id,
        "dataset_column": _dataset_column(record),
        "source_truth_ref": _source_truth_ref(variable_id),
        "catalog_ref": _pending_catalog_ref(variable_id),
        "handling_status": {
            "action": action if isinstance(action, str) else "unknown",
            **({"reason": reason} if isinstance(reason, str) else {}),
        },
        "clean_output": {
            "present": True,
            "basis": "source_truth.derivation_targets includes dataset_schema",
        },
        "analysis_queryable": normalized.get("analysis_queryable") is True,
        "review_state": record.get("review_state"),
        "source_kind": record.get("source_kind"),
    }


def _source_truth_dataset_present(record: Mapping[str, Any]) -> bool:
    dataset = record.get("presence", {}).get("dataset", {})
    return isinstance(dataset, Mapping) and dataset.get("present") is True


def _status_from_record(record: Mapping[str, Any], *, clean_output_present: bool) -> dict[str, Any]:
    variable_id = _variable_id(record)
    normalized = record.get("normalized", {})
    if not isinstance(normalized, Mapping):
        raise DatasetSchemaError(f"{variable_id} normalized metadata must be a mapping")
    return {
        "variable_id": variable_id,
        "source_truth_dataset_present": _source_truth_dataset_present(record),
        "clean_output_present": clean_output_present,
        "analysis_queryable": normalized.get("analysis_queryable") is True and clean_output_present,
        "handling_action": normalized.get("handling_action"),
        "review_state": record.get("review_state"),
        "source_kind": record.get("source_kind"),
    }


def build_dataset_schema(source_truth_artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Build a metadata-only Dataset Schema sidecar from Source Truth records."""
    if not isinstance(source_truth_artifact, Mapping):
        raise DatasetSchemaError("source_truth_artifact must be a mapping")
    forbidden = _forbidden_key_paths(source_truth_artifact)
    if forbidden:
        raise DatasetSchemaError(
            "source_truth_artifact contains dataset-schema-forbidden key(s): "
            + ", ".join(sorted(forbidden))
        )

    records = source_truth_artifact.get("records")
    if not isinstance(records, list):
        raise DatasetSchemaError("source_truth_artifact.records must be a list")

    entries: list[dict[str, Any]] = []
    excluded_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise DatasetSchemaError("source_truth_artifact.records entries must be mappings")
        if DERIVATION_DATASET_SCHEMA in record.get("derivation_targets", []):
            entries.append(_entry_from_record(record))
        else:
            excluded_records.append(_status_from_record(record, clean_output_present=False))
    return {
        "artifact_type": "study_dataset_schema",
        "study": source_truth_artifact.get("study"),
        "source_file": source_truth_artifact.get("source_file"),
        "source_truth_ref": {
            "artifact_type": source_truth_artifact.get("artifact_type"),
            "study": source_truth_artifact.get("study"),
            "source_file": source_truth_artifact.get("source_file"),
        },
        "entries": entries,
        "excluded_records": excluded_records,
    }


def get_dataset_schema_status(
    dataset_schema: Mapping[str, Any],
    variable_id: str,
) -> dict[str, Any]:
    """Report current-dataset presence and queryability for a selected variable."""
    entries = dataset_schema.get("entries")
    excluded_records = dataset_schema.get("excluded_records")
    if not isinstance(entries, list) or not isinstance(excluded_records, list):
        raise DatasetSchemaError("dataset_schema must carry entries and excluded_records lists")

    target = variable_id.upper()
    for entry in entries:
        if isinstance(entry, Mapping) and str(entry.get("variable_id", "")).upper() == target:
            handling = entry.get("handling_status", {})
            clean_output = entry.get("clean_output", {})
            return {
                "variable_id": str(entry["variable_id"]),
                "source_truth_dataset_present": True,
                "clean_output_present": isinstance(clean_output, Mapping)
                and clean_output.get("present") is True,
                "analysis_queryable": entry.get("analysis_queryable") is True,
                "handling_action": (
                    handling.get("action") if isinstance(handling, Mapping) else None
                ),
                "review_state": entry.get("review_state"),
                "source_kind": entry.get("source_kind"),
            }
    for status in excluded_records:
        if isinstance(status, Mapping) and str(status.get("variable_id", "")).upper() == target:
            return dict(status)
    return {
        "variable_id": variable_id,
        "source_truth_dataset_present": False,
        "clean_output_present": False,
        "analysis_queryable": False,
        "handling_action": None,
        "review_state": "missing",
        "source_kind": "missing",
    }


def resolve_analysis_binding(
    dataset_schema: Mapping[str, Any],
    variable_id: str,
) -> dict[str, Any]:
    """Return the analysis binding for a dataset-schema variable."""
    entries = dataset_schema.get("entries")
    if not isinstance(entries, list):
        raise DatasetSchemaError("dataset_schema.entries must be a list")

    target = variable_id.upper()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("variable_id", "")).upper() != target:
            continue
        if entry.get("analysis_queryable") is not True:
            raise DatasetSchemaError(f"{variable_id} is present but not analysis-queryable")
        return dict(entry)
    raise DatasetSchemaError(f"{variable_id} is not present in the dataset schema")
