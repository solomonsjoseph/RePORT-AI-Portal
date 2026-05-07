# scripts/source_truth/concepts.py
"""Concept-index validator and dataset-schema enricher.

The concept index is now STRUCTURALLY DERIVED from the SoT policy
files — see ``scripts.source_truth.concept_derivation``. This module
covers the two wrapper passes the build coordinator runs against the
derived index:

* :func:`build_concept_index` — cross-checks every ``member_variable``
  against the policy artifacts to catch derivation bugs and renders
  the artifact body with ``analysis_queryable: null`` placeholders.
* :func:`enrich_concept_index_with_schema` — Stage-2 pass that patches
  ``analysis_queryable`` for every member by looking up the dataset
  schema once it has been built.

Hand-authored ``study_concepts.yaml`` files were removed in Phase 2 of
the SoT reorg; ``load_study_concepts`` is no longer exposed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "ConceptIndexError",
    "build_concept_index",
    "enrich_concept_index_with_schema",
]

_REQUIRED_SECTIONS = ("cohorts", "outcomes", "exposures", "schedules", "definitions")


class ConceptIndexError(ValueError):
    """Raised when concept index cannot be built or validated."""


def build_concept_index(
    concepts: Mapping[str, Any],
    *,
    policy_artifacts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Render the concept_index artifact body.

    Validates that every ``member_variables`` entry's (form, variable_id)
    pair exists in the supplied policy artifacts. Initial
    ``analysis_queryable`` is null for every member; the value is patched
    later by :func:`enrich_concept_index_with_schema` once the dataset
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
    """Patch ``analysis_queryable`` on every member by looking up the dataset schema.

    Stage 2 enrichment of the build pipeline. Members not present in the
    dataset schema retain ``analysis_queryable: false``.
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
