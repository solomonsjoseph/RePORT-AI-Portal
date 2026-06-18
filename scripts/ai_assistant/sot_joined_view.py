"""Derived PDF Source Truth + dataset schema query views.

The joined view is intentionally derived at query time. Policy Source Truth YAML
remains the PDF-authoritative source, while per-form dataset schema JSON remains
the dataset/runtime-binding source.
"""

from __future__ import annotations

import json
import re
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


_SOT_POLICY_SUFFIXES = ("_policy.yaml", "_policy.lean.yaml")
_JOINED_VIEW_SUFFIX = "_joined_query_view.yaml"


def _normalize_sot_form_key(name: str) -> str:
    """Collapse separator differences for SoT pair ↔ dataset stem matching."""

    return re.sub(r"[_\-\s]+", "", name.strip().lower())


def _joined_view_path(pair_dir: Path, form_id: str) -> Path:
    return pair_dir / "joined" / f"{form_id}{_JOINED_VIEW_SUFFIX}"


def _joined_views_in_pair(pair_dir: Path) -> list[Path]:
    joined_dir = pair_dir / "joined"
    if not joined_dir.is_dir():
        return []
    return sorted(path for path in joined_dir.glob(f"*{_JOINED_VIEW_SUFFIX}") if path.is_file())


def _schema_binding_stems(schema_path: Path) -> set[str]:
    """Return dataset stem aliases recorded in a schema sidecar (metadata only)."""

    try:
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()

    stems: set[str] = set()
    form = payload.get("form")
    if isinstance(form, str) and form.strip():
        stems.add(form.strip())
    source_dataset = payload.get("source_dataset")
    if isinstance(source_dataset, str) and source_dataset.strip():
        stems.add(Path(source_dataset).stem)
    return stems


def _resolve_sot_pair_dir(sot_root: Path, stem: str) -> Path | None:
    """Locate the SoT pair directory for a dataset stem when names diverge."""

    if not sot_root.is_dir():
        return None

    direct = sot_root / stem
    if _joined_views_in_pair(direct):
        return direct

    # Same glob shapes as sot_loader.find_policy_yaml for an explicit form id.
    for suffix in _SOT_POLICY_SUFFIXES:
        for policy_path in sorted(sot_root.glob(f"*/pdf/{stem}{suffix}")):
            pair_dir = policy_path.parent.parent
            if _joined_views_in_pair(pair_dir):
                return pair_dir

    stem_lower = stem.lower()
    stem_key = _normalize_sot_form_key(stem)

    metadata_matches: list[Path] = []
    normalized_matches: list[Path] = []
    case_matches: list[Path] = []

    for pair_dir in sorted(path for path in sot_root.iterdir() if path.is_dir()):
        if not _joined_views_in_pair(pair_dir):
            continue
        if pair_dir.name.lower() == stem_lower:
            case_matches.append(pair_dir)
            continue
        if _normalize_sot_form_key(pair_dir.name) == stem_key:
            normalized_matches.append(pair_dir)

        schema_dir = pair_dir / "dataset"
        if schema_dir.is_dir():
            for schema_path in sorted(schema_dir.glob("*_schema.json")):
                if stem in _schema_binding_stems(schema_path):
                    metadata_matches.append(pair_dir)
                    break

    for bucket in (metadata_matches, case_matches, normalized_matches):
        deduped = list(dict.fromkeys(bucket))
        if len(deduped) == 1:
            return deduped[0]
    return None


def resolve_sot_joined_view_path(sot_root: Path, form_name: str) -> Path:
    """Return the canonical joined-query-view path for a dataset form.

    Layout: ``{sot_root}/{stem}/joined/{stem}_joined_query_view.yaml``.
    *form_name* may be a workbook filename (``6_HIV.xlsx``) or a bare stem.

    When the dataset stem and SoT pair directory differ only by underscore or
    case conventions (for example ``14_Case_Control`` vs ``14_CaseControl``),
    fall back to policy globs, schema ``source_dataset`` metadata, and
    conservative normalized-name matching — header/metadata only, never row values.
    """
    stem = Path(form_name).stem
    canonical = _joined_view_path(sot_root / stem, stem)
    if canonical.is_file():
        return canonical

    pair_dir = _resolve_sot_pair_dir(sot_root, stem)
    if pair_dir is not None:
        for form_id in (stem, pair_dir.name):
            candidate = _joined_view_path(pair_dir, form_id)
            if candidate.is_file():
                return candidate
        joined_views = _joined_views_in_pair(pair_dir)
        if len(joined_views) == 1:
            return joined_views[0]

    return canonical


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
