"""Analysis binding resolver — replaces the old StudyKnowledge path (#75).

Binds analysis roles (cohort, outcome, predictors, derived variables)
through the Source Truth catalog cards and Dataset Schema entries instead
of the manually-curated ``study_knowledge.yaml``.

The resolver returns one binding dict per role. Each binding is either:

  * ``review_required = False`` — source-backed, with ``catalog_ref`` and
    ``dataset_schema_ref`` references and ``analysis_queryable = True``;
    safe to feed to deterministic analysis runners; or

  * ``review_required = True`` — surfaced explicitly when the catalog has
    the concept but the Dataset Schema cannot bind it to an analysis-
    queryable column (e.g., source-only PDF narrative). Such bindings
    must NEVER be passed through to the analysis runner unchecked; the
    feature-flag gate at the engine entry point refuses to proceed.

Validation that finds nothing at all (the asked-for variable is not in
the catalog or is dropped-by-policy) raises :class:`AnalysisBindingError`
rather than silently falling back to the old metadata path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from scripts.source_truth.dataset_schema import (
    DatasetSchemaError,
    get_dataset_schema_status,
    resolve_analysis_binding,
)

__all__ = [
    "AnalysisBindingError",
    "resolve_analysis_bindings",
]


class AnalysisBindingError(ValueError):
    """Raised when an analysis role cannot be bound to a source-backed
    catalog card + Dataset Schema entry (and the caller did not opt in
    to a review-required derived role)."""


def _catalog_record(catalog: Mapping[str, Any], variable_id: str) -> Mapping[str, Any] | None:
    records = catalog.get("records")
    if not isinstance(records, list):
        return None
    target = variable_id.upper()
    for record in records:
        if (
            isinstance(record, Mapping)
            and str(record.get("variable_id", "")).upper() == target
        ):
            return record
    return None


def _evidence_pack(catalog: Mapping[str, Any], variable_id: str) -> Mapping[str, Any] | None:
    packs = catalog.get("evidence_packs")
    if not isinstance(packs, list):
        return None
    target = variable_id.upper()
    for pack in packs:
        if (
            isinstance(pack, Mapping)
            and str(pack.get("variable_id", "")).upper() == target
        ):
            return pack
    return None


def _bind_queryable(
    *,
    catalog: Mapping[str, Any],
    dataset_schema: Mapping[str, Any],
    variable_id: str,
    role: str,
) -> dict[str, Any]:
    """Bind a role through catalog + Dataset Schema. Source-backed only."""
    record = _catalog_record(catalog, variable_id)
    pack = _evidence_pack(catalog, variable_id)
    if record is None and pack is None:
        raise AnalysisBindingError(
            f"{role} variable {variable_id!r} is not present in the catalog; "
            "cannot bind through the Source Truth path."
        )

    try:
        binding = resolve_analysis_binding(dataset_schema, variable_id)
    except DatasetSchemaError as exc:
        # The variable exists in the catalog but the Dataset Schema cannot
        # produce an analysis-queryable binding. Surface it cleanly so the
        # caller doesn't fall back to old metadata.
        raise AnalysisBindingError(
            f"{role} variable {variable_id!r} cannot be bound for analysis: {exc}"
        ) from exc

    return {
        "role": role,
        "variable_id": binding["variable_id"],
        "dataset_column": binding["dataset_column"],
        "binding_source": "dataset_schema",
        "analysis_queryable": True,
        "review_required": False,
        "handling_status": binding.get("handling_status", {}),
        "source_references": {
            "catalog_ref": {
                "artifact_type": "study_variable_catalog",
                "variable_id": binding["variable_id"],
            },
            "dataset_schema_ref": {
                "artifact_type": "study_dataset_schema",
                "variable_id": binding["variable_id"],
            },
        },
    }


def _bind_review_required(
    *,
    catalog: Mapping[str, Any],
    dataset_schema: Mapping[str, Any],
    variable_id: str,
    role: str,
) -> dict[str, Any]:
    """Bind a role that the catalog knows about but the schema cannot
    promote to analysis-queryable. Returns a marker dict; never claims
    queryability."""
    record = _catalog_record(catalog, variable_id)
    pack = _evidence_pack(catalog, variable_id)
    if record is None and pack is None:
        raise AnalysisBindingError(
            f"{role} variable {variable_id!r} is not present in the catalog; "
            "cannot bind through the Source Truth path."
        )

    status = get_dataset_schema_status(dataset_schema, variable_id)

    return {
        "role": role,
        "variable_id": variable_id,
        "dataset_column": None,
        "binding_source": "catalog_only",
        "analysis_queryable": False,
        "review_required": True,
        "handling_status": {"action": status.get("handling_action")},
        "review_reason": (
            "variable present in catalog/source-truth but not in the dataset schema "
            "as an analysis-queryable entry"
        ),
        "source_references": {
            "catalog_ref": {
                "artifact_type": "study_variable_catalog",
                "variable_id": variable_id,
            },
        },
    }


def _cohort_block(cohort_id: str) -> dict[str, Any]:
    return {
        "cohort_id": cohort_id,
        "binding_source": "dataset_schema",
        "review_required": False,
        "source_references": {
            "catalog_ref": {"artifact_type": "study_variable_catalog"},
            "dataset_schema_ref": {"artifact_type": "study_dataset_schema"},
        },
    }


def resolve_analysis_bindings(
    *,
    question: str,
    cohort_id: str,
    catalog: Mapping[str, Any],
    dataset_schema: Mapping[str, Any],
    outcome_variable_id: str,
    predictor_variable_ids: Sequence[str],
    derived_variable_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Resolve cohort/outcome/predictor/derived bindings via catalog + schema.

    Args:
        question: The user's natural-language analysis request. Carried
            through for downstream provenance; deterministic resolution
            uses the explicit variable ids the caller passes.
        cohort_id: A cohort identifier (e.g. ``cohort_a``) — propagated
            into the cohort binding block. Cohort-level catalog cards land
            in #80; for now the cohort block records that the cohort was
            bound through the source-truth path rather than ``study_knowledge.yaml``.
        catalog: A ``study_variable_catalog`` artifact built by
            :func:`build_catalog_artifact`.
        dataset_schema: A ``study_dataset_schema`` artifact built by
            :func:`build_dataset_schema` from the same Source Truth source.
        outcome_variable_id: The variable id for the outcome — must be
            present and analysis-queryable, else :class:`AnalysisBindingError`.
        predictor_variable_ids: Variable ids for predictors — each must
            be present and analysis-queryable, else error.
        derived_variable_ids: Optional derived/auxiliary variable ids.
            Each is bound through the schema if possible; otherwise it
            comes back as a ``review_required`` marker rather than
            silently being dropped.

    Returns:
        A dict with keys ``question``, ``cohort``, ``outcome``,
        ``predictors``, and ``derived``. The ``outcome``/``predictors``
        entries are always source-backed (or the resolver raises);
        ``derived`` may contain review-required markers.

    Raises:
        AnalysisBindingError: When the outcome or any predictor cannot
            be bound through catalog + Dataset Schema.
    """
    bindings: dict[str, Any] = {
        "question": question,
        "cohort": _cohort_block(cohort_id),
    }

    bindings["outcome"] = _bind_queryable(
        catalog=catalog,
        dataset_schema=dataset_schema,
        variable_id=outcome_variable_id,
        role="outcome",
    )

    bindings["predictors"] = [
        _bind_queryable(
            catalog=catalog,
            dataset_schema=dataset_schema,
            variable_id=variable_id,
            role="predictor",
        )
        for variable_id in predictor_variable_ids
    ]

    derived: list[dict[str, Any]] = []
    for variable_id in derived_variable_ids or []:
        try:
            derived.append(
                _bind_queryable(
                    catalog=catalog,
                    dataset_schema=dataset_schema,
                    variable_id=variable_id,
                    role="derived",
                )
            )
        except AnalysisBindingError:
            derived.append(
                _bind_review_required(
                    catalog=catalog,
                    dataset_schema=dataset_schema,
                    variable_id=variable_id,
                    role="derived",
                )
            )
    bindings["derived"] = derived

    return bindings
