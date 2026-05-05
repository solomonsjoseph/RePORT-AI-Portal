"""Tests for issue #76 — Epidemiology regression and plot planning validation.

Adds a validated epidemiology analysis planning path for *association*
questions. The planner proposes regression analyses from retrieved catalog
cards while the validator constrains:

  * model type (binary outcome → logistic; continuous outcome → linear);
  * causal language (translates "influence/affect" wording to association
    unless ``causal=True`` is explicitly enabled);
  * interaction formulas (interaction terms always include main effects);
  * backward selection (allowed only when labeled exploratory);
  * plot recommendations (drives off variable type and role);
  * source-only / absent / unsupported-role / unsupported-cohort /
    timepoint-mismatch validation failures.

These tests are deliberately written against the *contract* of
``scripts.source_truth.epidemiology.plan_epidemiology_analysis`` — not its
implementation. The shared ``_bundle()`` helper builds a small Source
Truth → Dataset Schema → Catalog bundle that the planner can resolve
bindings from via :mod:`scripts.source_truth.analysis_binding`.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.dataset_schema import build_dataset_schema


def _bundle() -> dict[str, Any]:
    """A Source Truth bundle for a TB recurrence cohort with mixed roles.

    Variables:

    * ``SUBJID`` — pseudonymized identifier (``field_class="identifier"``).
      Should fail "unsupported role" if assigned to outcome/predictor.
    * ``AGE`` — continuous predictor (``field_class="continuous"``).
    * ``SMOKING`` — categorical predictor (``field_class="categorical"``).
    * ``TB_RECUR`` — binary outcome (``field_class="binary"``).
    * ``CD4`` — continuous predictor (``field_class="continuous"``).
    * ``TB_NOTES`` — PDF-only narrative; source-only, NOT in dataset →
      review-required → planner must refuse to emit a model when this is a
      predictor.
    * ``SIGNATURE`` — drop-by-policy → already handled by the binding
      layer; never analysis-queryable.
    """
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "cohort_a.xlsx",
        "sheets": [
            {
                "sheet": "_cohortA",
                "columns": ["SUBJID", "AGE", "SMOKING", "TB_RECUR", "CD4", "SIGNATURE"],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "SUBJID",
            "AGE",
            "SMOKING",
            "TB_RECUR",
            "CD4",
            "SIGNATURE",
            "TB_NOTES",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "AGE",
                    "SMOKING",
                    "TB_RECUR",
                    "CD4",
                    "SIGNATURE",
                    "TB_NOTES",
                ],
            }
        ],
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "cohort_a.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/cohort_a v1.0.pdf",
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
                "field_class": "identifier",
            },
            "AGE": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "demographics",
                "pdf_annotation_status": "direct",
                "field_class": "continuous",
            },
            "SMOKING": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "predictors",
                "pdf_annotation_status": "direct",
                "field_class": "categorical",
            },
            "TB_RECUR": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "outcomes",
                "pdf_annotation_status": "direct",
                "field_class": "binary",
            },
            "CD4": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "labs",
                "pdf_annotation_status": "direct",
                "field_class": "continuous",
            },
            "SIGNATURE": {
                "action": "drop",
                "reason": "signature_field",
                "confidence": "high",
                "section": "completion",
                "pdf_annotation_status": "direct",
            },
            "TB_NOTES": {
                # Source-only field with a regression-friendly ``field_class``
                # so the role gate cannot short-circuit the source-only test.
                # The review_required check is the only thing that should
                # reject this variable as a predictor.
                "action": "keep",
                "reason": "pdf_only_instruction",
                "confidence": "high",
                "source_kind": "source_only",
                "dataset_present": False,
                "pdf_annotation_status": "direct",
                "field_class": "continuous",
            },
        },
    }
    source_truth = build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)
    schema = build_dataset_schema(source_truth)
    catalog = build_catalog_artifact(source_truth, dataset_schema=schema)
    return {"source_truth": source_truth, "schema": schema, "catalog": catalog}


# ── Association wording / causal language ────────────────────────────────


class TestAssociationWording:
    def test_influence_question_is_normalized_to_association(self) -> None:
        """AC: 'factors influencing TB recurrence' → association narrative,
        NOT causal narrative, when ``causal=False`` (default)."""
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors influence TB recurrence in Cohort A?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING"],
        )

        narrative = plan["narrative"].lower()
        # Association wording present
        assert "associat" in narrative
        # Causal wording softened/removed
        for word in ("cause", "causes", "influence", "influences", "affect", "affects"):
            assert word not in narrative, (
                f"narrative leaked causal wording {word!r}: {plan['narrative']!r}"
            )
        assert plan["interpretation"] == "association"
        # Normalized question records the rewrite for provenance.
        assert "associat" in plan["normalized_question"].lower()

    def test_explicit_causal_opt_in_allows_causal_language(self) -> None:
        """AC: when ``causal=True`` is explicitly enabled, causal phrasing
        is permitted in the narrative and the interpretation is recorded
        as causal."""
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What causes TB recurrence in Cohort A?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING"],
            causal=True,
        )
        assert plan["interpretation"] == "causal"
        # Narrative may carry causal phrasing; the planner must NOT silently
        # convert it to association language when the caller opted in.
        narrative = plan["narrative"].lower()
        assert "caus" in narrative or "effect" in narrative


# ── Model policy selection ───────────────────────────────────────────────


class TestModelPolicySelection:
    def test_binary_outcome_picks_logistic_regression(self) -> None:
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with TB recurrence?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING"],
        )
        assert plan["model"]["type"] == "logistic"
        assert plan["model"]["outcome_kind"] == "binary"

    def test_continuous_outcome_picks_linear_regression(self) -> None:
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with CD4 count?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="CD4",
            predictor_variable_ids=["AGE", "SMOKING"],
        )
        assert plan["model"]["type"] == "linear"
        assert plan["model"]["outcome_kind"] == "continuous"


# ── Interaction formula behavior ─────────────────────────────────────────


class TestInteractionFormula:
    def test_interaction_term_includes_both_main_effects_and_interaction(self) -> None:
        """AC: interaction models include main effects by default.

        With predictors AGE, SMOKING and an explicit interaction
        ``("AGE", "SMOKING")``, the emitted formula must reference each
        main effect *and* the interaction term ``AGE:SMOKING``."""
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with TB recurrence?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING"],
            interaction_terms=[("AGE", "SMOKING")],
        )
        formula = plan["model"]["formula"]
        # Split the right-hand side on ``+`` to pin down that AGE and
        # SMOKING appear as *standalone* main effects (not just inside
        # the ``AGE:SMOKING`` interaction term).
        assert " ~ " in formula, formula
        rhs = formula.split(" ~ ", 1)[1]
        terms = [token.strip() for token in rhs.split("+")]
        assert "AGE" in terms, terms
        assert "SMOKING" in terms, terms
        # Interaction expressed using the unambiguous ``A:B`` convention.
        assert "AGE:SMOKING" in terms, terms
        # And the planner records the interaction explicitly.
        assert ("AGE", "SMOKING") in [tuple(t) for t in plan["model"]["interaction_terms"]]


# ── Backward selection labeling ──────────────────────────────────────────


class TestBackwardSelection:
    def test_backward_selection_is_labeled_exploratory(self) -> None:
        """AC: backward selection is allowed only as exploratory and is
        labeled as such in the plan."""
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with TB recurrence?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING", "CD4"],
            backward_selection=True,
        )
        model = plan["model"]
        assert model["backward_selection"] is True
        assert model["exploratory"] is True
        # Narrative carries the exploratory label so downstream readers
        # cannot mistake the result for a confirmatory model.
        assert "exploratory" in plan["narrative"].lower()


# ── Plot recommendations ─────────────────────────────────────────────────


class TestPlotRecommendations:
    """Plot mapping (documented in epidemiology module docstring):

    * categorical predictor × binary outcome  → bar
    * continuous  predictor × binary outcome  → forest
    * continuous  predictor × continuous outcome → scatter
    * categorical predictor × continuous outcome → boxplot
    """

    def test_categorical_predictor_with_binary_outcome_recommends_bar(self) -> None:
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with TB recurrence?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["SMOKING"],
        )
        plots = {p["predictor"]: p["plot_type"] for p in plan["plots"]}
        assert plots["SMOKING"] == "bar"

    def test_continuous_predictor_with_binary_outcome_recommends_forest(self) -> None:
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with TB recurrence?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE"],
        )
        plots = {p["predictor"]: p["plot_type"] for p in plan["plots"]}
        assert plots["AGE"] == "forest"

    def test_continuous_predictor_with_continuous_outcome_recommends_scatter(self) -> None:
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with CD4 count?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="CD4",
            predictor_variable_ids=["AGE"],
        )
        plots = {p["predictor"]: p["plot_type"] for p in plan["plots"]}
        assert plots["AGE"] == "scatter"

    def test_categorical_predictor_with_continuous_outcome_recommends_boxplot(self) -> None:
        from scripts.source_truth.epidemiology import plan_epidemiology_analysis

        bundle = _bundle()
        plan = plan_epidemiology_analysis(
            question="What factors are associated with CD4?",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="CD4",
            predictor_variable_ids=["SMOKING"],
        )
        plots = {p["predictor"]: p["plot_type"] for p in plan["plots"]}
        assert plots["SMOKING"] == "boxplot"


# ── Validation rejections ────────────────────────────────────────────────


class TestValidationRejections:
    def test_source_only_predictor_blocks_model(self) -> None:
        """AC: source-only variables (catalog yes / dataset no) cannot be
        used as predictors. The planner must raise rather than silently
        emit a model that references a review-required binding."""
        from scripts.source_truth.epidemiology import (
            EpidemiologyPlanError,
            plan_epidemiology_analysis,
        )

        bundle = _bundle()
        with pytest.raises(EpidemiologyPlanError) as exc_info:
            plan_epidemiology_analysis(
                question="What factors are associated with TB recurrence?",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="TB_RECUR",
                predictor_variable_ids=["AGE", "TB_NOTES"],
            )
        assert "TB_NOTES" in str(exc_info.value)

    def test_absent_outcome_raises_clear_error(self) -> None:
        """AC: variables not in the catalog at all must be rejected, not
        silently treated as missing."""
        from scripts.source_truth.epidemiology import (
            EpidemiologyPlanError,
            plan_epidemiology_analysis,
        )

        bundle = _bundle()
        with pytest.raises(EpidemiologyPlanError) as exc_info:
            plan_epidemiology_analysis(
                question="associations with NOT_A_REAL_OUTCOME",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="NOT_A_REAL_OUTCOME",
                predictor_variable_ids=["AGE"],
            )
        assert "NOT_A_REAL_OUTCOME" in str(exc_info.value)

    def test_unsupported_role_blocks_identifier_as_outcome(self) -> None:
        """AC: identifier-class variables cannot serve as an outcome."""
        from scripts.source_truth.epidemiology import (
            EpidemiologyPlanError,
            plan_epidemiology_analysis,
        )

        bundle = _bundle()
        with pytest.raises(EpidemiologyPlanError) as exc_info:
            plan_epidemiology_analysis(
                question="model SUBJID",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="SUBJID",
                predictor_variable_ids=["AGE"],
            )
        message = str(exc_info.value).lower()
        assert "subjid" in message
        assert "role" in message or "identifier" in message

    def test_unsupported_cohort_blocks_planning(self) -> None:
        """AC: a cohort outside the supported set must be rejected."""
        from scripts.source_truth.epidemiology import (
            EpidemiologyPlanError,
            plan_epidemiology_analysis,
        )

        bundle = _bundle()
        with pytest.raises(EpidemiologyPlanError) as exc_info:
            plan_epidemiology_analysis(
                question="associations in unknown cohort",
                cohort_id="cohort_zzz",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="TB_RECUR",
                predictor_variable_ids=["AGE"],
                supported_cohorts=("cohort_a", "cohort_b"),
            )
        message = str(exc_info.value).lower()
        assert "cohort" in message
        assert "cohort_zzz" in str(exc_info.value)

    def test_timepoint_mismatch_blocks_planning(self) -> None:
        """AC: a timepoint not allowed for a variable must be rejected."""
        from scripts.source_truth.epidemiology import (
            EpidemiologyPlanError,
            plan_epidemiology_analysis,
        )

        bundle = _bundle()
        with pytest.raises(EpidemiologyPlanError) as exc_info:
            plan_epidemiology_analysis(
                question="associations at unsupported timepoint",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="TB_RECUR",
                predictor_variable_ids=["AGE"],
                timepoint="month_99",
                allowed_timepoints={"TB_RECUR": ("month_6", "month_12")},
            )
        message = str(exc_info.value).lower()
        assert "timepoint" in message
        assert "month_99" in str(exc_info.value)


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_missing_outcome_variable_id_is_required(self) -> None:
        """Edge case: a planner call without an outcome variable id fails
        loudly rather than picking one heuristically."""
        from scripts.source_truth.epidemiology import (
            EpidemiologyPlanError,
            plan_epidemiology_analysis,
        )

        bundle = _bundle()
        with pytest.raises(EpidemiologyPlanError) as exc_info:
            plan_epidemiology_analysis(
                question="associations with something",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id=None,  # type: ignore[arg-type]
                predictor_variable_ids=["AGE"],
            )
        message = str(exc_info.value).lower()
        assert "outcome" in message

    def test_conflicting_cohort_spec_is_rejected(self) -> None:
        """Edge case: a cohort id that conflicts with the supported set
        (empty supported set) must fail clearly."""
        from scripts.source_truth.epidemiology import (
            EpidemiologyPlanError,
            plan_epidemiology_analysis,
        )

        bundle = _bundle()
        with pytest.raises(EpidemiologyPlanError) as exc_info:
            plan_epidemiology_analysis(
                question="associations",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="TB_RECUR",
                predictor_variable_ids=["AGE"],
                supported_cohorts=(),  # empty: nothing supported
            )
        message = str(exc_info.value).lower()
        assert "cohort" in message
