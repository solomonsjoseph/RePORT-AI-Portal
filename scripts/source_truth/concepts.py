# scripts/source_truth/concepts.py
"""Loader/validator for `study_concepts.yaml` and renderer for
`concept_index.json`.

`study_concepts.yaml` is the cross-form concept SoT introduced in
`CONTEXT.md` §"Build Pipeline — May 2026". The 28 per-form policy
YAMLs are NOT modified; this module reads them only for cross-reference
validation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ConceptIndexError",
    "build_concept_index",
    "enrich_concept_index_with_schema",
    "load_study_concepts",
]

_REQUIRED_SECTIONS = ("cohorts", "outcomes", "exposures", "schedules", "definitions")


class ConceptIndexError(ValueError):
    """Raised when concept index cannot be built or validated."""


def load_study_concepts(path: str | Path) -> dict[str, Any]:
    """Load `study_concepts.yaml` and ensure required top-level sections exist."""
    path = Path(path)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConceptIndexError(f"{path}: failed to parse YAML — {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise ConceptIndexError(f"{path}: top-level must be a mapping")

    if "study" not in loaded:
        raise ConceptIndexError(f"{path}: missing 'study'")

    result: dict[str, Any] = {
        "schema_version": loaded.get("schema_version", 1),
        "policy_status": loaded.get("policy_status", "draft_for_human_review"),
        "study": loaded["study"],
    }
    for section in _REQUIRED_SECTIONS:
        result[section] = dict(loaded.get(section) or {})
    return result


def build_concept_index(
    concepts: Mapping[str, Any],
    *,
    policy_artifacts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Render the concept_index artifact body.

    Validates that every `member_variables` entry's (form, variable_id)
    pair exists in the supplied policy artifacts. Initial
    `analysis_queryable` is null for every member; the value is patched
    later by `enrich_concept_index_with_schema()` once the dataset
    schema is built.

    Raises:
        ConceptIndexError: on any unresolved member variable reference.
    """
    by_form_var: dict[tuple[str, str], dict[str, Any]] = {}
    for art in policy_artifacts:
        form = art["form"]
        for record in art["records"]:
            vid = record.get("variable_id")
            if vid:
                by_form_var[(form, vid)] = record

    out: dict[str, Any] = {
        "artifact_type": "study_concept_index",
        "schema_version": concepts["schema_version"],
        "policy_status": concepts["policy_status"],
        "study": concepts["study"],
    }
    for section in _REQUIRED_SECTIONS:
        out[section] = {}
        for concept_id, body in (concepts.get(section) or {}).items():
            rendered = dict(body)
            members_raw = rendered.get("member_variables") or []
            members_out: list[dict[str, Any]] = []
            for member in members_raw:
                form = member["form"]
                vid = member["variable_id"]
                if (form, vid) not in by_form_var:
                    raise ConceptIndexError(
                        f"{section}.{concept_id}: member ({form}, {vid}) "
                        f"is not declared in any policy YAML"
                    )
                members_out.append(
                    {
                        "form": form,
                        "variable_id": vid,
                        "role": member.get("role"),
                        "analysis_queryable": None,
                    }
                )
            rendered["member_variables"] = members_out
            out[section][concept_id] = rendered
    return out


def enrich_concept_index_with_schema(
    concept_index: Mapping[str, Any],
    *,
    dataset_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Patch `analysis_queryable` on every member by looking up the dataset schema.

    Stage 2 enrichment of the build pipeline. Members not present in the
    dataset schema retain `analysis_queryable: false`.
    """
    entries = dataset_schema.get("entries") or []
    by_vid: dict[str, Mapping[str, Any]] = {
        e["variable_id"]: e for e in entries if isinstance(e, Mapping) and "variable_id" in e
    }

    out: dict[str, Any] = dict(concept_index)
    for section in _REQUIRED_SECTIONS:
        section_body = dict(concept_index.get(section) or {})
        new_section: dict[str, Any] = {}
        for concept_id, body in section_body.items():
            rendered = dict(body)
            if "member_variables" in rendered:
                rendered["member_variables"] = [
                    {
                        **member,
                        "analysis_queryable": bool(
                            by_vid.get(member["variable_id"], {}).get("analysis_queryable")
                        ),
                    }
                    for member in rendered["member_variables"]
                ]
            new_section[concept_id] = rendered
        out[section] = new_section
    return out
