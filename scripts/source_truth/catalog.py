"""Catalog cards and evidence packs derived from Source Truth artifacts."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from scripts.source_truth.record import (
    FORBIDDEN_ARTIFACT_VERSION_KEYS,
    FORBIDDEN_RAW_VALUE_KEYS,
)

__all__ = ["SourceTruthCatalogError", "build_catalog_artifact"]


class SourceTruthCatalogError(ValueError):
    """Raised when catalog artifacts cannot be derived from Source Truth."""


DERIVATION_CATALOG = "catalog"
DERIVATION_DATASET_SCHEMA = "dataset_schema"

_FORBIDDEN_KEYS = FORBIDDEN_RAW_VALUE_KEYS | FORBIDDEN_ARTIFACT_VERSION_KEYS


def _forbidden_key_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key) in _FORBIDDEN_KEYS:
                found.append(child_path)
            found.extend(_forbidden_key_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_key_paths(child, f"{path}[{index}]"))
    return found


def _reject_forbidden_keys(name: str, value: Mapping[str, Any]) -> None:
    forbidden = _forbidden_key_paths(value)
    if forbidden:
        raise SourceTruthCatalogError(
            f"{name} contains catalog-forbidden key(s): " + ", ".join(sorted(forbidden))
        )


def _variable_id(record: Mapping[str, Any]) -> str:
    value = record.get("variable_id")
    if not isinstance(value, str) or not value:
        raise SourceTruthCatalogError("source-truth records must carry a string variable_id")
    return value


def _normalized(record: Mapping[str, Any]) -> Mapping[str, Any]:
    variable_id = _variable_id(record)
    value = record.get("normalized")
    if not isinstance(value, Mapping):
        raise SourceTruthCatalogError(f"{variable_id} normalized metadata must be a mapping")
    return value


def _dataset_column(record: Mapping[str, Any]) -> str | None:
    dataset = record.get("presence", {}).get("dataset", {})
    if not isinstance(dataset, Mapping) or dataset.get("present") is not True:
        return None
    column = dataset.get("column")
    return column if isinstance(column, str) and column else None


def _schema_refs(dataset_schema: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    if dataset_schema is None:
        return {}
    _reject_forbidden_keys("dataset_schema", dataset_schema)
    entries = dataset_schema.get("entries")
    if not isinstance(entries, list):
        raise SourceTruthCatalogError("dataset_schema.entries must be a list")
    refs: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise SourceTruthCatalogError("dataset_schema.entries entries must be mappings")
        variable_id = entry.get("variable_id")
        if not isinstance(variable_id, str) or not variable_id:
            raise SourceTruthCatalogError("dataset_schema entries must carry variable_id")
        refs[variable_id] = {
            "artifact_type": "study_dataset_schema",
            "variable_id": variable_id,
        }
    return refs


def _catalog_ref(variable_id: str) -> dict[str, str]:
    return {"artifact_type": "study_variable_catalog", "variable_id": variable_id}


def _evidence_pack_ref(variable_id: str) -> dict[str, str]:
    return {"artifact_type": "study_variable_evidence_pack", "variable_id": variable_id}


def _source_truth_ref(source_truth_artifact: Mapping[str, Any], variable_id: str) -> dict[str, Any]:
    return {
        "artifact_type": source_truth_artifact.get("artifact_type"),
        "study": source_truth_artifact.get("study"),
        "source_file": source_truth_artifact.get("source_file"),
        "variable_id": variable_id,
    }


def _is_catalog_target(record: Mapping[str, Any]) -> bool:
    targets = record.get("derivation_targets")
    return isinstance(targets, list) and DERIVATION_CATALOG in targets


def _is_compact_record(record: Mapping[str, Any]) -> bool:
    normalized = _normalized(record)
    return (
        _is_catalog_target(record)
        and _dataset_column(record) is not None
        and normalized.get("analysis_queryable") is True
    )


def _source_presence(record: Mapping[str, Any]) -> dict[str, bool]:
    presence = record.get("presence", {})
    if not isinstance(presence, Mapping):
        return {"dataset": False, "pdf": False, "dictionary": False}
    result: dict[str, bool] = {}
    for source in ("dataset", "pdf", "dictionary"):
        block = presence.get(source, {})
        result[source] = isinstance(block, Mapping) and block.get("present") is True
    return result


def _search_terms(variable_id: str, normalized: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    for value in (
        variable_id,
        normalized.get("label"),
        normalized.get("section"),
        normalized.get("field_class"),
    ):
        if not isinstance(value, str):
            continue
        for token in value.lower().replace("_", " ").replace("-", " ").split():
            if token and token not in terms:
                terms.append(token)
    return terms


def _options_summary(normalized: Mapping[str, Any]) -> dict[str, Any]:
    options = normalized.get("source_defined_options")
    option_set = normalized.get("option_set")
    return {
        "count": len(options) if isinstance(options, list) else 0,
        **({"option_set": option_set} if isinstance(option_set, str) else {}),
    }


def _relationship_summary(normalized: Mapping[str, Any]) -> dict[str, Any]:
    relationships = normalized.get("relationships")
    if not isinstance(relationships, list):
        return {"count": 0, "types": []}
    types = sorted(
        {
            item["type"]
            for item in relationships
            if isinstance(item, Mapping) and isinstance(item.get("type"), str)
        }
    )
    return {"count": len(relationships), "types": types}


def _handling_status(normalized: Mapping[str, Any]) -> dict[str, Any]:
    action = normalized.get("handling_action")
    reason = normalized.get("handling_reason")
    return {
        "action": action if isinstance(action, str) else "unknown",
        **({"reason": reason} if isinstance(reason, str) else {}),
    }


def _compact_record(
    record: Mapping[str, Any],
    source_truth_artifact: Mapping[str, Any],
    dataset_schema_refs: Mapping[str, dict[str, str]],
) -> dict[str, Any]:
    variable_id = _variable_id(record)
    normalized = _normalized(record)
    label = normalized.get("label")
    action = normalized.get("handling_action")
    if not isinstance(label, str) or not isinstance(action, str):
        raise SourceTruthCatalogError(f"{variable_id} has incomplete normalized catalog metadata")
    compact = {
        "variable_id": variable_id,
        "label": label,
        "display_label": label,
        "normalized_meaning": label,
        "search_terms": _search_terms(variable_id, normalized),
        "form": source_truth_artifact.get("source_file"),
        "dataset_column": _dataset_column(record),
        "source_presence": _source_presence(record),
        "catalog_tier": "variable",
        "source_kind": record.get("source_kind"),
        "review_state": record.get("review_state"),
        "handling_action": action,
        "handling_status": _handling_status(normalized),
        "analysis_queryable": True,
        "options_summary": _options_summary(normalized),
        "relationship_summary": _relationship_summary(normalized),
        "source_truth_ref": _source_truth_ref(source_truth_artifact, variable_id),
        **(
            {"handling_reason": normalized["handling_reason"]}
            if "handling_reason" in normalized
            else {}
        ),
        **({"field_class": normalized["field_class"]} if "field_class" in normalized else {}),
        **(
            {"sensitivity_flags": normalized["sensitivity_flags"]}
            if "sensitivity_flags" in normalized
            else {}
        ),
        **({"section": normalized["section"]} if "section" in normalized else {}),
        "evidence_pack_ref": _evidence_pack_ref(variable_id),
    }
    if variable_id in dataset_schema_refs:
        compact["dataset_schema_ref"] = dataset_schema_refs[variable_id]
    return compact


def _normalization_trace(normalized: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "label",
        "confidence",
        "normalization_basis",
        "handling_action",
        "handling_reason",
        "field_class",
        "sensitivity_flags",
        "section",
        "option_set",
    )
    return {key: copy.deepcopy(normalized[key]) for key in keys if key in normalized}


def _evidence_pack(
    record: Mapping[str, Any],
    source_truth_artifact: Mapping[str, Any],
    dataset_schema_refs: Mapping[str, dict[str, str]],
    compact_variable_ids: set[str],
) -> dict[str, Any]:
    variable_id = _variable_id(record)
    normalized = _normalized(record)
    pack = {
        "artifact_type": "study_variable_evidence_pack",
        "variable_id": variable_id,
        "source_kind": record.get("source_kind"),
        "review_state": record.get("review_state"),
        "analysis_queryable": normalized.get("analysis_queryable") is True,
        "source_truth_ref": _source_truth_ref(source_truth_artifact, variable_id),
        "catalog_ref": (
            _catalog_ref(variable_id)
            if variable_id in compact_variable_ids
            else {"status": "not_in_compact_catalog", "variable_id": variable_id}
        ),
        "exact_source_wording": copy.deepcopy(record.get("exact_source_wording", {})),
        "source_references": copy.deepcopy(record.get("source_references", {})),
        "normalization_trace": _normalization_trace(normalized),
    }
    relationships = normalized.get("relationships")
    if isinstance(relationships, list):
        pack["relationships"] = copy.deepcopy(relationships)
    if variable_id in dataset_schema_refs:
        pack["dataset_schema_ref"] = dataset_schema_refs[variable_id]
    return pack


def _excluded_record(record: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalized(record)
    return {
        "reason": "not_catalog_target"
        if not _is_catalog_target(record)
        else "not_compact_queryable",
        "handling_action": normalized.get("handling_action"),
        "source_kind": record.get("source_kind"),
        "analysis_queryable": normalized.get("analysis_queryable") is True,
    }


def build_catalog_artifact(
    source_truth_artifact: Mapping[str, Any],
    *,
    dataset_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact catalog records and lazy evidence packs from Source Truth."""
    if not isinstance(source_truth_artifact, Mapping):
        raise SourceTruthCatalogError("source_truth_artifact must be a mapping")
    _reject_forbidden_keys("source_truth_artifact", source_truth_artifact)

    records = source_truth_artifact.get("records")
    if not isinstance(records, list):
        raise SourceTruthCatalogError("source_truth_artifact.records must be a list")

    schema_refs = _schema_refs(dataset_schema)
    compact_records: list[dict[str, Any]] = []
    evidence_source_records: list[Mapping[str, Any]] = []
    excluded_records: dict[str, dict[str, Any]] = {}

    for record in records:
        if not isinstance(record, Mapping):
            raise SourceTruthCatalogError("source_truth_artifact.records entries must be mappings")
        variable_id = _variable_id(record)
        if _is_compact_record(record):
            compact_records.append(_compact_record(record, source_truth_artifact, schema_refs))
            evidence_source_records.append(record)
        elif _is_catalog_target(record):
            evidence_source_records.append(record)
            excluded_records[variable_id] = _excluded_record(record)
        else:
            excluded_records[variable_id] = _excluded_record(record)

    compact_variable_ids = {record["variable_id"] for record in compact_records}
    evidence_packs = [
        _evidence_pack(record, source_truth_artifact, schema_refs, compact_variable_ids)
        for record in evidence_source_records
    ]
    dataset_schema_links = {
        variable_id: {
            "dataset_schema_ref": schema_ref,
            "catalog_ref": _catalog_ref(variable_id),
            "evidence_pack_ref": _evidence_pack_ref(variable_id),
        }
        for variable_id, schema_ref in schema_refs.items()
        if variable_id in compact_variable_ids
    }

    return {
        "artifact_type": "study_variable_catalog",
        "study": source_truth_artifact.get("study"),
        "source_file": source_truth_artifact.get("source_file"),
        "source_truth_ref": {
            "artifact_type": source_truth_artifact.get("artifact_type"),
            "study": source_truth_artifact.get("study"),
            "source_file": source_truth_artifact.get("source_file"),
        },
        "records": compact_records,
        "evidence_packs": evidence_packs,
        "excluded_records": excluded_records,
        "dataset_schema_links": dataset_schema_links,
    }
