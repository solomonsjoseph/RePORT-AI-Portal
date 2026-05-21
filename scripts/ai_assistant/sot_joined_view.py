"""Derived PDF Source Truth + dataset schema query views.

The joined view is intentionally derived at query time. Policy Source Truth YAML
remains the PDF-authoritative source, while per-form dataset schema JSON remains
the dataset/runtime-binding source.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

DATASET_FIELDS_TO_DROP = {"llm_status", "published_in_jsonl", "source_order"}

LLM_TEXT_REPLACEMENTS = {
    "\u2192": "->",
    "\u2014": "-",
    "\u2013": "-",
    "\u00b3": "3",
    "\u00b2": "2",
}

PDF_FIELD_NAMES = {
    "section": "section",
    "pdf_question": "question",
    "pdf_label": "label",
    "pdf_subsection": "subsection",
    "type": "type",
    "description": "description",
    "options": "options",
    "relationships": "relationships",
    "format": "format",
    "units": "units",
    "precision": "precision",
    "notes": "notes",
    "phi": "phi",
}


def _normalise_llm_text(value: Any) -> Any:
    """Return a query-view value with common symbolic noise made plain."""

    if isinstance(value, str):
        normalised = value
        for source, replacement in LLM_TEXT_REPLACEMENTS.items():
            normalised = normalised.replace(source, replacement)
        return normalised
    if isinstance(value, list):
        return [_normalise_llm_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalise_llm_text(item) for key, item in value.items()}
    return value


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected YAML mapping at {path}")
    return payload


def _load_json_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def _form_id_from_policy_path(path: Path) -> str:
    name = path.name
    for suffix in ("_policy.yaml", "_policy.lean.yaml"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    for suffix in (".lean.yaml", ".yaml"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _form_id_from_lean_path(path: Path) -> str:
    """Backward-compatible alias for older callers."""

    return _form_id_from_policy_path(path)


def find_dataset_schema_for_policy(policy_path: Path) -> Path | None:
    """Return the matching per-form dataset schema path when present."""

    form_id = _form_id_from_policy_path(policy_path)
    pair_dir = policy_path.parent.parent
    llm_source_dir = pair_dir
    if policy_path.parent.name == "source_truth":
        llm_source_dir = policy_path.parent.parent
    candidates = [
        # New skill layout: llm_source/SoT/<sot-pair-name>/pdf/<form>_policy.yaml
        # with the dataset authority next to it under dataset/.
        pair_dir / "dataset" / f"{form_id}_schema.json",
        pair_dir / f"{form_id}_schema.json",
        policy_path.with_name(f"{form_id}_schema.json"),
        # Compatibility with the existing runtime llm_source layout.
        llm_source_dir / "dataset_schema" / f"{form_id}_schema.json",
        llm_source_dir / "dataset_schema" / "schemas" / f"{form_id}_schema.json",
        llm_source_dir / "dataset_schema" / form_id / f"{form_id}_schema.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


def find_dataset_schema_for_lean(lean_path: Path) -> Path | None:
    """Backward-compatible alias for older callers."""

    return find_dataset_schema_for_policy(lean_path)


def _dataset_columns_by_name(schema: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    columns = schema.get("columns")
    if not isinstance(columns, list):
        raise ValueError("dataset schema must include a columns list")

    names = [column.get("name") for column in columns if isinstance(column, Mapping)]
    duplicates = sorted(name for name, count in Counter(names).items() if name and count > 1)
    if duplicates:
        joined = ", ".join(str(name) for name in duplicates)
        raise ValueError(f"duplicate dataset schema column name(s): {joined}")

    out: dict[str, Mapping[str, Any]] = {}
    for column in columns:
        if not isinstance(column, Mapping):
            continue
        name = column.get("name")
        if isinstance(name, str) and name:
            out[name] = column
    return out


def _clean_dataset_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _normalise_llm_text(value)
        for key, value in entry.items()
        if key not in DATASET_FIELDS_TO_DROP and key != "name"
    }


def _pdf_entry(meta: Mapping[str, Any]) -> dict[str, Any]:
    return {
        output_key: _normalise_llm_text(meta[input_key])
        for input_key, output_key in PDF_FIELD_NAMES.items()
        if input_key in meta
    }


def _runtime_fields(schema: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    fields = schema.get("runtime_fields")
    if not isinstance(fields, list):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for field in fields:
        if not isinstance(field, Mapping):
            continue
        name = field.get("name")
        if not isinstance(name, str) or not name:
            continue
        out[name] = _clean_dataset_entry(field)
    return out


def build_joined_query_view(policy_path: Path, schema_path: Path) -> dict[str, Any]:
    """Build a derived LLM query view from one policy YAML and one dataset schema."""

    policy = _load_yaml_mapping(policy_path)
    schema = _load_json_mapping(schema_path)
    dataset_columns = _dataset_columns_by_name(schema)

    policy_variables = policy.get("variables")
    if not isinstance(policy_variables, Mapping):
        raise ValueError("policy YAML must include a variables mapping")

    variables: dict[str, dict[str, Any]] = {}
    for variable_id, meta in policy_variables.items():
        if not isinstance(variable_id, str):
            continue
        joined: dict[str, Any] = {}
        if isinstance(meta, Mapping):
            joined["pdf"] = _pdf_entry(meta)
        dataset_meta = dataset_columns.get(variable_id)
        if dataset_meta is not None:
            joined["dataset"] = _clean_dataset_entry(dataset_meta)
        variables[variable_id] = joined

    for variable_id, dataset_meta in dataset_columns.items():
        variables.setdefault(variable_id, {})["dataset"] = _clean_dataset_entry(dataset_meta)

    dataset = {
        key: schema[key]
        for key in ("source_dataset", "jsonl_file", "record_count")
        if key in schema
    }

    return {
        "study": _normalise_llm_text(schema.get("study", policy.get("study"))),
        "form": _normalise_llm_text(schema.get("form", _form_id_from_policy_path(policy_path))),
        "dataset": _normalise_llm_text(dataset),
        "variables": variables,
        "runtime_fields": _runtime_fields(schema),
    }


def write_joined_query_view_yaml(path: Path, view: Mapping[str, Any]) -> None:
    """Write a joined query view as readable YAML for LLM consumption."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            _normalise_llm_text(dict(view)),
            sort_keys=False,
            allow_unicode=True,
            width=1000,
        ),
        encoding="utf-8",
    )
