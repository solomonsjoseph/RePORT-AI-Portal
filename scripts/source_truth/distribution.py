"""Categorical distribution coordinator — first analysis tracer bullet (#74).

Wires the existing Source Truth pieces together for a single end-to-end slice:

    user question
        ↓ retrieval over compact catalog (SourceTruthRetriever)
        ↓ schema validation (Dataset Schema, not the old StudyKnowledge path)
        ↓ categorical analysis runner (counts + percentages)
        ↓ concise dataset response with source references

The runner is intentionally tiny. It deliberately does NOT pre-seed the
catalog's source-defined options into the result; only values that the
analysis runner actually observes appear as distribution rows. The
catalog's option list is reported separately as evidence so callers can
spot options that were defined but never observed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.source_truth.dataset_schema import (
    DatasetSchemaError,
    get_dataset_schema_status,
    resolve_analysis_binding,
)
from scripts.source_truth.retrieval import SourceTruthRetriever

__all__ = [
    "DistributionRequestError",
    "run_categorical_distribution",
]


class DistributionRequestError(ValueError):
    """Raised when a distribution request fails retrieval or schema validation."""


_DESCRIPTIVE_ALLOWED_ACTIONS = frozenset({"keep", "pseudonymize", "jitter_date", "generalize"})


def _catalog_record(catalog: Mapping[str, Any], variable_id: str) -> Mapping[str, Any] | None:
    records = catalog.get("records")
    if not isinstance(records, list):
        return None
    for record in records:
        if isinstance(record, Mapping) and record.get("variable_id") == variable_id:
            return record
    return None


def _evidence_pack(catalog: Mapping[str, Any], variable_id: str) -> Mapping[str, Any] | None:
    packs = catalog.get("evidence_packs")
    if not isinstance(packs, list):
        return None
    for pack in packs:
        if isinstance(pack, Mapping) and pack.get("variable_id") == variable_id:
            return pack
    return None


def _pdf_pages(pack: Mapping[str, Any] | None) -> list[int]:
    if not isinstance(pack, Mapping):
        return []
    refs = pack.get("source_references")
    if not isinstance(refs, Mapping):
        return []
    pdf = refs.get("pdf")
    if not isinstance(pdf, Mapping):
        return []
    pages = pdf.get("annotation_pages")
    if not isinstance(pages, list):
        return []
    return [page for page in pages if isinstance(page, int)]


def _source_defined_options(pack: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(pack, Mapping):
        return []
    exact = pack.get("exact_source_wording")
    if not isinstance(exact, Mapping):
        return []
    options = exact.get("pdf_options")
    if not isinstance(options, list):
        return []
    return [str(option) for option in options]


def _validate_for_descriptive(
    dataset_schema: Mapping[str, Any],
    variable_id: str,
    *,
    pdf_pages: Sequence[int],
) -> dict[str, Any]:
    status = get_dataset_schema_status(dataset_schema, variable_id)
    if status.get("source_truth_dataset_present") is not True:
        raise DistributionRequestError(
            f"{variable_id} is not present in the current dataset schema; "
            "cannot run a distribution against the dataset."
        )

    try:
        binding = resolve_analysis_binding(dataset_schema, variable_id)
    except DatasetSchemaError as exc:
        raise DistributionRequestError(str(exc)) from exc

    handling_status = binding.get("handling_status")
    action = (
        handling_status.get("action") if isinstance(handling_status, Mapping) else None
    ) or status.get("handling_action")
    allowed_for_descriptive = action in _DESCRIPTIVE_ALLOWED_ACTIONS

    if not allowed_for_descriptive:
        raise DistributionRequestError(
            f"{variable_id} handling action {action!r} does not allow descriptive analysis."
        )

    return {
        "variable_id": variable_id,
        "dataset_column": binding["dataset_column"],
        "present_in_dataset": True,
        "analysis_queryable": binding.get("analysis_queryable") is True,
        "allowed_for_descriptive": allowed_for_descriptive,
        "handling_action": action,
        "binding_source": "dataset_schema",
        "has_source_references": bool(pdf_pages),
    }


def _categorical_distribution(variable_id: str, observed_values: Sequence[Any]) -> dict[str, Any]:
    """Count observed (non-missing) values; percentages over valid n.

    Crucially, this only ever reports values that actually appeared in
    ``observed_values``. Source-defined option lists from the catalog are
    NOT seeded as zero-count rows — that would conflate "the source defined
    these options" with "the analysis observed these values."
    """
    n_total = len(observed_values)
    valid = [v for v in observed_values if v is not None and not _is_missing(v)]
    n_valid = len(valid)
    n_missing = n_total - n_valid

    counts = Counter(valid)
    categories = []
    for value, count in counts.most_common():
        percent = (count / n_valid * 100) if n_valid else 0.0
        categories.append({"value": value, "count": int(count), "percent": round(percent, 2)})
    return {
        "variable_id": variable_id,
        "n_total": n_total,
        "n_valid": n_valid,
        "n_missing": n_missing,
        "categories": categories,
    }


def _is_missing(value: Any) -> bool:
    if isinstance(value, float):
        # NaN check without importing math/numpy.
        return value != value
    return bool(isinstance(value, str) and not value.strip())


def _summary_text(
    variable_id: str,
    form: str | None,
    distribution: Mapping[str, Any],
    pdf_pages: Sequence[int],
) -> str:
    parts: list[str] = []
    form_clause = f" on {form}" if form else ""
    parts.append(
        f"Distribution of {variable_id}{form_clause}: "
        f"{distribution['n_valid']} valid records "
        f"({distribution['n_missing']} missing)."
    )
    rows = distribution.get("categories", [])
    if rows:
        rendered = ", ".join(
            f"{row['value']} {row['count']} ({row['percent']:.1f}%)" for row in rows
        )
        parts.append(rendered + ".")
    if pdf_pages:
        page_text = ", ".join(str(page) for page in pdf_pages)
        parts.append(f"Source: PDF page {page_text}.")
    return " ".join(parts)


def run_categorical_distribution(
    *,
    question: str,
    catalog: Mapping[str, Any],
    dataset_schema: Mapping[str, Any],
    observed_values: Sequence[Any],
    variable_id: str | None = None,
) -> dict[str, Any]:
    """Run retrieval → schema validation → categorical distribution.

    Args:
        question: The user's natural-language request.
        catalog: A study variable catalog artifact (compact records plus
            evidence packs), as built by ``build_catalog_artifact``.
        dataset_schema: A Dataset Schema sidecar built by
            ``build_dataset_schema`` from the same Source Truth artifact.
        observed_values: Values for the bound dataset column actually seen
            by the caller's data layer. The runner counts these directly;
            source-defined options are not synthesized into the output.
        variable_id: Optional explicit variable id. When the caller has
            already resolved the variable (e.g., from a clarification
            round-trip), pass it here to bypass retrieval and route the
            request straight to schema validation.

    Returns:
        A response dict with ``variable_ids``, ``validation``,
        ``distribution`` (counts + percentages), ``source_defined_options``
        (catalog evidence, separate from observed counts),
        ``source_references``, and a concise ``summary`` string. When the
        retrieval is ambiguous, returns a clarification response with
        ``needs_clarification=True`` and no ``distribution`` block.

    Raises:
        DistributionRequestError: When the resolved variable is absent from
            the dataset schema, not analysis-queryable, or its handling
            policy does not allow descriptive analysis.
    """
    if variable_id is None:
        retriever = SourceTruthRetriever.from_catalog_artifact(catalog)
        matches = retriever.retrieve_cards(question, limit=3)
        if not matches:
            raise DistributionRequestError(
                "No catalog variable matched the question; cannot bind for distribution analysis."
            )
        answer = retriever.answer_metadata_question(question)

        if answer.needs_clarification:
            return {
                "needs_clarification": True,
                "variable_ids": list(answer.variable_ids),
                "summary": answer.text,
            }

        variable_id = answer.variable_ids[0]

    record = _catalog_record(catalog, variable_id)
    pack = _evidence_pack(catalog, variable_id)
    pdf_pages = _pdf_pages(pack)

    validation = _validate_for_descriptive(dataset_schema, variable_id, pdf_pages=pdf_pages)

    distribution = _categorical_distribution(variable_id, observed_values)

    form = record.get("form") if isinstance(record, Mapping) else None
    source_references = {
        "variable_id": variable_id,
        "dataset_column": validation["dataset_column"],
        "form": form,
        "pdf_pages": pdf_pages,
        "catalog_ref": {
            "artifact_type": "study_variable_catalog",
            "variable_id": variable_id,
        },
        "dataset_schema_ref": {
            "artifact_type": "study_dataset_schema",
            "variable_id": variable_id,
        },
    }

    return {
        "needs_clarification": False,
        "variable_ids": [variable_id],
        "validation": validation,
        "distribution": distribution,
        "source_defined_options": _source_defined_options(pack),
        "source_references": source_references,
        "summary": _summary_text(variable_id, form, distribution, pdf_pages),
    }
