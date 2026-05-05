"""Epidemiology regression and plot planning validator (#76).

A small, validated planning path for *association* analyses. Given a
question, a cohort id, a Source-Truth-derived catalog and dataset
schema, an outcome variable id, and predictor variable ids, the planner
returns a single plan dict shaped roughly like::

    {
      "question":            <verbatim user question>,
      "normalized_question": <association-rewritten question>,
      "interpretation":      "association" | "causal",
      "cohort":              <cohort binding block>,
      "outcome":             <outcome binding>,
      "predictors":          [<predictor binding>, ...],
      "model": {
          "type":               "logistic" | "linear",
          "outcome_kind":       "binary" | "continuous",
          "formula":            "TB_RECUR ~ AGE + SMOKING + AGE:SMOKING",
          "interaction_terms":  [("AGE","SMOKING"), ...],
          "backward_selection": False,
          "exploratory":        False,
      },
      "plots":               [{"predictor": "AGE", "plot_type": "forest"}, ...],
      "narrative":           "Association of <factors> with <outcome>...",
    }

The planner is deliberately small. It reuses
:func:`scripts.source_truth.analysis_binding.resolve_analysis_bindings`
for variable resolution (so the same source-only / dropped-by-policy /
absent-from-catalog rules are inherited) and adds five extra validations
on top:

  * **role**: identifier-class variables cannot serve as outcome or
    predictor (you cannot regress against ``SUBJID``);
  * **cohort**: when ``supported_cohorts`` is provided, the cohort id
    must be in it;
  * **timepoint**: when ``timepoint`` and ``allowed_timepoints`` are
    both provided, every variable that has an entry in
    ``allowed_timepoints`` must list the timepoint;
  * **review-required**: any binding that comes back ``review_required``
    (e.g., source-only PDF narrative) blocks the model — the planner
    refuses to emit a model that references a non-source-backed binding;
  * **causal language**: by default ("influence/affect/cause" → softened
    to "association"); ``causal=True`` opts in.

Plot recommendations (driven by predictor ``field_class`` x outcome
``field_class``) follow a small fixed table:

================================  ===================================
predictor / outcome               recommended plot
================================  ===================================
categorical / binary              bar
continuous  / binary              forest
continuous  / continuous          scatter
categorical / continuous          boxplot
================================  ===================================

Anything outside the table falls back to ``"summary"``.

The planner does not run the model — it only produces a validated plan.
The downstream analysis runner (a future slice) is responsible for
execution.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.source_truth.analysis_binding import (
    AnalysisBindingError,
    resolve_analysis_bindings,
)

__all__ = [
    "EpidemiologyPlanError",
    "plan_epidemiology_analysis",
]


class EpidemiologyPlanError(ValueError):
    """Raised when an epidemiology plan cannot be produced for the given
    question/cohort/variable selection."""


# ── Constants ────────────────────────────────────────────────────────────

# field_class → high-level outcome kind. Only ``binary`` and ``continuous``
# are supported in this slice; everything else surfaces as an unsupported-
# role error so the LLM cannot silently default to logistic on, say, a
# free-text narrative variable.
_OUTCOME_KIND_BY_FIELD_CLASS: dict[str, str] = {
    "binary": "binary",
    "continuous": "continuous",
}

# field_class values that may NEVER act as outcome or predictor. Identifier
# fields are pseudonymized for linkage but are not regression candidates.
_NON_ROLE_FIELD_CLASSES: frozenset[str] = frozenset(
    {"identifier", "signature", "administrative", "narrative"}
)

# (predictor_field_class, outcome_kind) → plot type.
_PLOT_TABLE: dict[tuple[str, str], str] = {
    ("categorical", "binary"): "bar",
    ("continuous", "binary"): "forest",
    ("continuous", "continuous"): "scatter",
    ("categorical", "continuous"): "boxplot",
}

# Words we treat as causal/effect language and rewrite to association
# wording when ``causal=False``. We rewrite verbs and nouns to neutral
# alternatives so the resulting narrative reads as association language.
_CAUSAL_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\binfluences?\b", re.IGNORECASE), "are associated with"),
    (re.compile(r"\binfluencing\b", re.IGNORECASE), "associated with"),
    (re.compile(r"\bcauses?\b", re.IGNORECASE), "are associated with"),
    (re.compile(r"\bcausing\b", re.IGNORECASE), "associated with"),
    (re.compile(r"\baffects?\b", re.IGNORECASE), "are associated with"),
    (re.compile(r"\baffecting\b", re.IGNORECASE), "associated with"),
)


# ── Catalog helpers ──────────────────────────────────────────────────────


def _catalog_record(catalog: Mapping[str, Any], variable_id: str) -> Mapping[str, Any] | None:
    records = catalog.get("records")
    if not isinstance(records, list):
        return None
    target = variable_id.upper()
    for record in records:
        if isinstance(record, Mapping) and str(record.get("variable_id", "")).upper() == target:
            return record
    return None


def _evidence_pack(catalog: Mapping[str, Any], variable_id: str) -> Mapping[str, Any] | None:
    packs = catalog.get("evidence_packs")
    if not isinstance(packs, list):
        return None
    target = variable_id.upper()
    for pack in packs:
        if isinstance(pack, Mapping) and str(pack.get("variable_id", "")).upper() == target:
            return pack
    return None


def _field_class_for(catalog: Mapping[str, Any], variable_id: str) -> str | None:
    """Return the catalog/evidence-pack ``field_class`` for a variable.

    Compact catalog records carry ``field_class`` directly when the
    underlying SoT record had one; evidence packs carry it inside
    ``normalization_trace`` for source-only / non-compact records.
    """
    record = _catalog_record(catalog, variable_id)
    if isinstance(record, Mapping):
        value = record.get("field_class")
        if isinstance(value, str) and value:
            return value
    pack = _evidence_pack(catalog, variable_id)
    if isinstance(pack, Mapping):
        trace = pack.get("normalization_trace", {})
        if isinstance(trace, Mapping):
            value = trace.get("field_class")
            if isinstance(value, str) and value:
                return value
    return None


# ── Validations layered on top of resolve_analysis_bindings ─────────────


def _validate_role(variable_id: str, field_class: str | None, role: str) -> None:
    if field_class in _NON_ROLE_FIELD_CLASSES:
        raise EpidemiologyPlanError(
            f"{variable_id} has field_class={field_class!r} which is an unsupported "
            f"{role} role; identifier/signature/narrative fields are not regression "
            "candidates."
        )


def _validate_cohort(cohort_id: str, supported: Sequence[str] | None) -> None:
    if supported is None:
        return
    if cohort_id not in supported:
        raise EpidemiologyPlanError(
            f"cohort {cohort_id!r} is not in the supported cohorts "
            f"({sorted(supported)!r}); cannot plan analysis."
        )


def _validate_timepoint(
    *,
    timepoint: str | None,
    allowed_timepoints: Mapping[str, Sequence[str]] | None,
    variable_ids: Sequence[str],
) -> None:
    if timepoint is None or allowed_timepoints is None:
        return
    for variable_id in variable_ids:
        allowed = allowed_timepoints.get(variable_id)
        if allowed is None:
            continue
        if timepoint not in allowed:
            raise EpidemiologyPlanError(
                f"timepoint {timepoint!r} is not allowed for {variable_id} "
                f"(allowed: {sorted(allowed)!r}); cannot plan analysis."
            )


def _validate_no_review_required_predictors(bindings: Mapping[str, Any]) -> None:
    """Refuse to plan when any binding came back review_required.

    This catches the source-only / catalog-only-no-schema case before the
    model is emitted: a regression that quietly drops a predictor would
    be worse than failing loudly.
    """
    for predictor in bindings.get("predictors", []):
        if predictor.get("review_required") is True:
            raise EpidemiologyPlanError(
                f"predictor {predictor.get('variable_id')!r} is review_required and "
                "cannot be used in a regression; refusing to emit a model."
            )
    for derived in bindings.get("derived", []):
        if derived.get("review_required") is True:
            raise EpidemiologyPlanError(
                f"derived variable {derived.get('variable_id')!r} is review_required "
                "(catalog-only / source-only); refusing to emit a model."
            )


# ── Outcome / model selection ────────────────────────────────────────────


def _outcome_kind_or_raise(variable_id: str, field_class: str | None) -> str:
    kind = _OUTCOME_KIND_BY_FIELD_CLASS.get(field_class or "")
    if kind is None:
        raise EpidemiologyPlanError(
            f"{variable_id} has field_class={field_class!r}; the planner only "
            "supports binary or continuous outcomes in this slice."
        )
    return kind


def _model_type_for(outcome_kind: str) -> str:
    if outcome_kind == "binary":
        return "logistic"
    if outcome_kind == "continuous":
        return "linear"
    raise EpidemiologyPlanError(f"unsupported outcome_kind {outcome_kind!r}")


def _build_formula(
    outcome_variable_id: str,
    predictor_variable_ids: Sequence[str],
    interaction_terms: Sequence[tuple[str, str]],
) -> str:
    """Render an R/patsy-style formula with explicit main effects.

    Interactions are emitted as ``A:B`` (no implicit main effects) and the
    main effects are always added, satisfying "interaction models include
    main effects by default".
    """
    main_effects = list(predictor_variable_ids)
    # Ensure all interaction operands are present as main effects.
    for left, right in interaction_terms:
        if left not in main_effects:
            main_effects.append(left)
        if right not in main_effects:
            main_effects.append(right)
    rhs = main_effects + [f"{left}:{right}" for left, right in interaction_terms]
    return f"{outcome_variable_id} ~ " + " + ".join(rhs)


# ── Plot recommendations ────────────────────────────────────────────────


def _plot_for(predictor_field_class: str | None, outcome_kind: str) -> str:
    if predictor_field_class is None:
        return "summary"
    return _PLOT_TABLE.get((predictor_field_class, outcome_kind), "summary")


def _plot_recommendations(
    *,
    catalog: Mapping[str, Any],
    predictor_variable_ids: Sequence[str],
    outcome_kind: str,
) -> list[dict[str, Any]]:
    plots: list[dict[str, Any]] = []
    for predictor_id in predictor_variable_ids:
        predictor_class = _field_class_for(catalog, predictor_id)
        plots.append(
            {
                "predictor": predictor_id,
                "predictor_field_class": predictor_class,
                "outcome_kind": outcome_kind,
                "plot_type": _plot_for(predictor_class, outcome_kind),
            }
        )
    return plots


# ── Narrative composition ────────────────────────────────────────────────


def _normalize_to_association(question: str) -> str:
    rewritten = question
    for pattern, replacement in _CAUSAL_REWRITES:
        rewritten = pattern.sub(replacement, rewritten)
    return rewritten


def _association_narrative(
    *,
    outcome_variable_id: str,
    predictor_variable_ids: Sequence[str],
    cohort_id: str,
    backward_selection: bool,
) -> str:
    factors = ", ".join(predictor_variable_ids) or "the requested predictors"
    base = (
        f"Association of {outcome_variable_id} with {factors} in {cohort_id}. "
        "Results are reported as associations, not causal effects."
    )
    if backward_selection:
        base += " Backward selection used; results are exploratory."
    return base


def _causal_narrative(
    *,
    outcome_variable_id: str,
    predictor_variable_ids: Sequence[str],
    cohort_id: str,
    backward_selection: bool,
) -> str:
    factors = ", ".join(predictor_variable_ids) or "the requested predictors"
    base = (
        f"Causal effect of {factors} on {outcome_variable_id} in {cohort_id}, "
        "with the caller having explicitly opted in to causal interpretation."
    )
    if backward_selection:
        base += " Backward selection used; results are exploratory."
    return base


# ── Public API ──────────────────────────────────────────────────────────


def plan_epidemiology_analysis(
    *,
    question: str,
    cohort_id: str,
    catalog: Mapping[str, Any],
    dataset_schema: Mapping[str, Any],
    outcome_variable_id: str,
    predictor_variable_ids: Sequence[str],
    derived_variable_ids: Sequence[str] | None = None,
    interaction_terms: Sequence[tuple[str, str]] | None = None,
    backward_selection: bool = False,
    causal: bool = False,
    supported_cohorts: Sequence[str] | None = None,
    timepoint: str | None = None,
    allowed_timepoints: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Build a validated epidemiology analysis plan.

    See module docstring for the plan shape and validation rules.

    Args:
        question: The user's natural-language analysis request.
        cohort_id: Cohort identifier (e.g. ``"cohort_a"``).
        catalog: A ``study_variable_catalog`` artifact.
        dataset_schema: A ``study_dataset_schema`` sidecar built from the
            same Source Truth source.
        outcome_variable_id: The outcome variable id.
        predictor_variable_ids: One or more predictor variable ids.
        derived_variable_ids: Optional auxiliary variables; surfaced as
            review-required errors if not analysis-queryable.
        interaction_terms: Optional list of ``(left, right)`` tuples; for
            each pair the formula will include both main effects and the
            ``left:right`` interaction.
        backward_selection: When True, the model is labeled exploratory.
        causal: When True (default False), causal phrasing is preserved
            in the narrative; otherwise causal verbs are rewritten to
            association wording.
        supported_cohorts: Optional cohort allowlist. When provided,
            ``cohort_id`` must be in it.
        timepoint: Optional study timepoint label (e.g. ``"month_6"``).
        allowed_timepoints: Optional per-variable allowed-timepoints map.
            When ``timepoint`` is provided and a variable is in this map,
            ``timepoint`` must be in its allowed list.

    Returns:
        A plan dict (see module docstring).

    Raises:
        EpidemiologyPlanError: Whenever validation fails (missing outcome,
            unsupported cohort, unsupported role, timepoint mismatch,
            review-required binding, etc.).
    """
    if not isinstance(outcome_variable_id, str) or not outcome_variable_id:
        raise EpidemiologyPlanError(
            "outcome_variable_id is required; the planner refuses to pick an outcome heuristically."
        )

    _validate_cohort(cohort_id, supported_cohorts)

    # Role validation runs before binding so identifier-class outcomes
    # surface as a role error rather than as a binding success that
    # downstream model selection then rejects on field_class grounds.
    outcome_field_class = _field_class_for(catalog, outcome_variable_id)
    _validate_role(outcome_variable_id, outcome_field_class, "outcome")
    for predictor_id in predictor_variable_ids:
        predictor_class = _field_class_for(catalog, predictor_id)
        _validate_role(predictor_id, predictor_class, "predictor")

    _validate_timepoint(
        timepoint=timepoint,
        allowed_timepoints=allowed_timepoints,
        variable_ids=[outcome_variable_id, *predictor_variable_ids],
    )

    try:
        bindings = resolve_analysis_bindings(
            question=question,
            cohort_id=cohort_id,
            catalog=catalog,
            dataset_schema=dataset_schema,
            outcome_variable_id=outcome_variable_id,
            predictor_variable_ids=predictor_variable_ids,
            derived_variable_ids=derived_variable_ids,
        )
    except AnalysisBindingError as exc:
        raise EpidemiologyPlanError(str(exc)) from exc

    _validate_no_review_required_predictors(bindings)

    outcome_kind = _outcome_kind_or_raise(outcome_variable_id, outcome_field_class)
    model_type = _model_type_for(outcome_kind)

    interaction_pairs: list[tuple[str, str]] = [
        (str(left), str(right)) for left, right in (interaction_terms or [])
    ]
    formula = _build_formula(outcome_variable_id, predictor_variable_ids, interaction_pairs)

    plots = _plot_recommendations(
        catalog=catalog,
        predictor_variable_ids=predictor_variable_ids,
        outcome_kind=outcome_kind,
    )

    interpretation = "causal" if causal else "association"
    if causal:
        narrative = _causal_narrative(
            outcome_variable_id=outcome_variable_id,
            predictor_variable_ids=predictor_variable_ids,
            cohort_id=cohort_id,
            backward_selection=backward_selection,
        )
        normalized_question = question
    else:
        narrative = _association_narrative(
            outcome_variable_id=outcome_variable_id,
            predictor_variable_ids=predictor_variable_ids,
            cohort_id=cohort_id,
            backward_selection=backward_selection,
        )
        normalized_question = _normalize_to_association(question)

    return {
        "question": question,
        "normalized_question": normalized_question,
        "interpretation": interpretation,
        "cohort": bindings["cohort"],
        "outcome": bindings["outcome"],
        "predictors": bindings["predictors"],
        "derived": bindings.get("derived", []),
        "model": {
            "type": model_type,
            "outcome_kind": outcome_kind,
            "formula": formula,
            "interaction_terms": interaction_pairs,
            "backward_selection": backward_selection,
            "exploratory": backward_selection,
        },
        "plots": plots,
        "narrative": narrative,
    }
