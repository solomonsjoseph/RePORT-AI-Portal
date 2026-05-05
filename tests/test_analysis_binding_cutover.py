"""Tests for issue #75 — Analysis binding cutover.

Replace the old manually curated study knowledge analysis binding with
catalog and Dataset Schema binding for cohort, outcome, predictor, and
derived-variable selection.

The new path is hidden behind ``REPORTALIN_USE_CATALOG_BINDING=1`` so the
old behavior remains the default until the next slice; this test file
proves:

  * the new resolver returns source-backed bindings (or explicit
    ``review_required`` markers) sourced from catalog + Dataset Schema —
    NOT from ``study_knowledge.yaml``;
  * a missing/non-queryable binding raises a clear validation error;
  * with the flag enabled, the analysis runner does NOT instantiate the
    old ``StudyKnowledge`` loader;
  * with the flag disabled (default), the old path is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.dataset_schema import build_dataset_schema

_FLAG = "REPORTALIN_USE_CATALOG_BINDING"
_RUNTIME_FLAG = "REPORTALIN_USE_CATALOG_RUNTIME"
_LEGACY_FLAG = "REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE"


def _bundle() -> dict[str, Any]:
    """A minimal source-truth artifact pretending to be a TB recurrence cohort.

    Includes:
    - SUBJID (pseudonymized identifier — present in dataset)
    - AGE (continuous predictor — keep, present)
    - SMOKING (categorical predictor — keep, present)
    - TB_RECUR (outcome — keep, present)
    - TB_NOTES (PDF-only narrative; source-only, NOT in dataset → review-required)
    - SIGNATURE (drop-by-policy — not analysis-queryable)
    """
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "cohort_a.xlsx",
        "sheets": [
            {
                "sheet": "_cohortA",
                "columns": ["SUBJID", "AGE", "SMOKING", "TB_RECUR", "SIGNATURE"],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "SUBJID",
            "AGE",
            "SMOKING",
            "TB_RECUR",
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
            },
            "AGE": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "demographics",
                "pdf_annotation_status": "direct",
            },
            "SMOKING": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "predictors",
                "pdf_annotation_status": "direct",
            },
            "TB_RECUR": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "outcomes",
                "pdf_annotation_status": "direct",
            },
            "SIGNATURE": {
                "action": "drop",
                "reason": "signature_field",
                "confidence": "high",
                "section": "completion",
                "pdf_annotation_status": "direct",
            },
            "TB_NOTES": {
                "action": "keep",
                "reason": "pdf_only_instruction",
                "confidence": "high",
                "source_kind": "source_only",
                "dataset_present": False,
                "pdf_annotation_status": "direct",
            },
        },
    }
    source_truth = build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)
    schema = build_dataset_schema(source_truth)
    catalog = build_catalog_artifact(source_truth, dataset_schema=schema)
    return {"source_truth": source_truth, "schema": schema, "catalog": catalog}


# ── Resolver behaviour ──────────────────────────────────────────────────


class TestResolveAnalysisBindings:
    """The new ``resolve_analysis_bindings`` lives in ``scripts.source_truth``
    and binds analysis roles (cohort/outcome/predictors/derived) through
    catalog cards + Dataset Schema entries — NOT through study_knowledge.yaml.
    """

    def test_outcome_binding_is_source_backed_and_carries_schema_refs(self) -> None:
        from scripts.source_truth.analysis_binding import resolve_analysis_bindings

        bundle = _bundle()
        result = resolve_analysis_bindings(
            question="model TB recurrence by age and smoking",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING"],
        )

        outcome = result["outcome"]
        assert outcome["variable_id"] == "TB_RECUR"
        assert outcome["dataset_column"] == "TB_RECUR"
        assert outcome["binding_source"] == "dataset_schema"
        assert outcome["analysis_queryable"] is True
        assert outcome["review_required"] is False
        # Source-backed: must reference both the catalog card and the
        # dataset-schema entry that produced the binding.
        refs = outcome["source_references"]
        assert refs["catalog_ref"]["variable_id"] == "TB_RECUR"
        assert refs["dataset_schema_ref"]["variable_id"] == "TB_RECUR"

    def test_predictor_bindings_resolve_through_catalog_and_schema(self) -> None:
        from scripts.source_truth.analysis_binding import resolve_analysis_bindings

        bundle = _bundle()
        result = resolve_analysis_bindings(
            question="model TB recurrence by age and smoking",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING"],
        )

        predictors = result["predictors"]
        assert isinstance(predictors, list)
        ids = [p["variable_id"] for p in predictors]
        assert ids == ["AGE", "SMOKING"]
        for binding in predictors:
            assert binding["binding_source"] == "dataset_schema"
            assert binding["analysis_queryable"] is True
            assert binding["review_required"] is False
            assert (
                binding["source_references"]["catalog_ref"]["variable_id"] == binding["variable_id"]
            )

    def test_cohort_block_carries_source_references(self) -> None:
        from scripts.source_truth.analysis_binding import resolve_analysis_bindings

        bundle = _bundle()
        result = resolve_analysis_bindings(
            question="recurrence in cohort A",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE"],
        )

        cohort = result["cohort"]
        assert cohort["cohort_id"] == "cohort_a"
        # Cohort always references the artifacts it was bound from.
        assert (
            cohort["source_references"]["catalog_ref"]["artifact_type"] == "study_variable_catalog"
        )
        assert (
            cohort["source_references"]["dataset_schema_ref"]["artifact_type"]
            == "study_dataset_schema"
        )
        assert cohort["binding_source"] == "dataset_schema"

    def test_pdf_only_concept_is_marked_review_required_not_silently_dropped(self) -> None:
        """AC2: bindings are source-backed OR explicitly review-required.

        ``TB_NOTES`` exists in the catalog but has no dataset column, so it
        cannot be analysis-queryable. The resolver must not silently fall
        back to old metadata; it must emit an explicit ``review_required``
        binding when the caller asks for it as a derived/predictor role.
        """
        from scripts.source_truth.analysis_binding import resolve_analysis_bindings

        bundle = _bundle()
        result = resolve_analysis_bindings(
            question="describe TB notes alongside age",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE"],
            derived_variable_ids=["TB_NOTES"],
        )

        derived = result["derived"]
        assert isinstance(derived, list) and len(derived) == 1
        notes_binding = derived[0]
        assert notes_binding["variable_id"] == "TB_NOTES"
        assert notes_binding["review_required"] is True
        # Review-required bindings must NEVER claim queryability against
        # the dataset — that would let a stale binding silently feed the
        # analysis runner.
        assert notes_binding["analysis_queryable"] is False

    def test_missing_outcome_raises_clear_validation_error(self) -> None:
        """AC5: validation blocks missing bindings instead of falling back."""
        from scripts.source_truth.analysis_binding import (
            AnalysisBindingError,
            resolve_analysis_bindings,
        )

        bundle = _bundle()
        with pytest.raises(AnalysisBindingError) as exc_info:
            resolve_analysis_bindings(
                question="recurrence",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="NOT_A_REAL_VARIABLE",
                predictor_variable_ids=["AGE"],
            )
        assert "NOT_A_REAL_VARIABLE" in str(exc_info.value)

    def test_dropped_predictor_is_not_silently_used(self) -> None:
        """AC5: variables marked drop-by-policy must not be analysis-queryable
        and must surface as a binding error rather than fall back."""
        from scripts.source_truth.analysis_binding import (
            AnalysisBindingError,
            resolve_analysis_bindings,
        )

        bundle = _bundle()
        with pytest.raises(AnalysisBindingError) as exc_info:
            resolve_analysis_bindings(
                question="signature impact on recurrence",
                cohort_id="cohort_a",
                catalog=bundle["catalog"],
                dataset_schema=bundle["schema"],
                outcome_variable_id="TB_RECUR",
                predictor_variable_ids=["SIGNATURE"],
            )
        assert "SIGNATURE" in str(exc_info.value)


# ── Feature flag gate around analytical_engine.run_full_analysis ──────


class TestFeatureFlagGate:
    """The flag controls whether the new path is the source of bindings."""

    def test_flag_default_on_after_hard_cutover(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After issue #81 the catalog binding is the default.

        Pre-cutover (#75) this asserted that with no env var set the
        legacy path was used. After the hard cutover the catalog
        binding is on by default; the legacy override env var is the
        only way back to the ``StudyKnowledge`` runner.
        """
        monkeypatch.delenv(_FLAG, raising=False)
        monkeypatch.delenv(_RUNTIME_FLAG, raising=False)
        monkeypatch.delenv(_LEGACY_FLAG, raising=False)
        import scripts.ai_assistant.analytical_engine as engine

        assert engine.is_catalog_binding_enabled() is True

    def test_legacy_override_keeps_old_study_knowledge_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE=1`` is the post-cutover
        rollback: with it set, the catalog binding flag returns False
        and the legacy ``StudyKnowledge`` runner is reachable again.
        """
        monkeypatch.delenv(_FLAG, raising=False)
        monkeypatch.delenv(_RUNTIME_FLAG, raising=False)
        monkeypatch.setenv(_LEGACY_FLAG, "1")
        import scripts.ai_assistant.analytical_engine as engine

        assert engine.is_catalog_binding_enabled() is False

    def test_flag_enabled_routes_to_catalog_binding(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(_LEGACY_FLAG, raising=False)
        monkeypatch.setenv(_FLAG, "1")
        import scripts.ai_assistant.analytical_engine as engine

        assert engine.is_catalog_binding_enabled() is True

    def test_flag_enabled_does_not_load_old_study_knowledge(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC6: prove the new path does not use the old manual loader.

        With the flag on, calling the analysis-engine binding entry point
        with pre-resolved catalog bindings must NOT instantiate
        ``StudyKnowledge``. We make ``StudyKnowledge.__init__`` raise so any
        accidental instantiation surfaces as a hard failure.
        """
        monkeypatch.setenv(_FLAG, "1")

        import scripts.ai_assistant.analytical_engine as engine

        def _boom(self: Any, *args: Any, **kwargs: Any) -> None:
            raise AssertionError(
                "StudyKnowledge was instantiated even though catalog binding flag is on"
            )

        monkeypatch.setattr(engine.StudyKnowledge, "__init__", _boom)

        bundle = _bundle()
        from scripts.source_truth.analysis_binding import resolve_analysis_bindings

        bindings = resolve_analysis_bindings(
            question="recurrence",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE", "SMOKING"],
        )

        # The engine entry point that the production runner will call when
        # the flag is on. It must accept pre-resolved bindings and prove,
        # by NOT raising the AssertionError above, that StudyKnowledge is
        # never instantiated.
        validated = engine.validate_catalog_bindings(bindings)
        assert validated["outcome"]["variable_id"] == "TB_RECUR"
        assert {p["variable_id"] for p in validated["predictors"]} == {"AGE", "SMOKING"}

    def test_flag_enabled_run_full_analysis_refuses_to_use_old_loader(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC3 + AC6: with the flag on, ``run_full_analysis`` (the legacy
        StudyKnowledge-driven entry point) must refuse to run at all.

        This is the genuine bypass: callers using the old path are routed
        into a hard error directing them to the catalog-binding path,
        and ``StudyKnowledge`` is never instantiated for the old call.
        """
        monkeypatch.setenv(_FLAG, "1")

        import scripts.ai_assistant.analytical_engine as engine

        instantiated: list[bool] = []
        original_init = engine.StudyKnowledge.__init__

        def _spy(self: Any, *args: Any, **kwargs: Any) -> None:
            instantiated.append(True)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(engine.StudyKnowledge, "__init__", _spy)

        from scripts.source_truth.analysis_binding import AnalysisBindingError

        with pytest.raises((AnalysisBindingError, NotImplementedError)):
            engine.run_full_analysis(
                knowledge=None,  # type: ignore[arg-type]
                data_dir=Path("/dev/null"),
                output_dir=Path("/dev/null"),
                cohort_id="cohort_a",
            )

        # And the old loader was never spun up for the legacy call.
        assert instantiated == []

    def test_flag_enabled_blocks_when_bindings_are_review_required(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC5: validation must block missing bindings rather than falling back.

        ``validate_catalog_bindings`` is the gate at the analysis-engine
        entry point. When a predictor binding is review-required (not
        source-backed), it must refuse to proceed.
        """
        monkeypatch.setenv(_FLAG, "1")
        import scripts.ai_assistant.analytical_engine as engine
        from scripts.source_truth.analysis_binding import (
            AnalysisBindingError,
            resolve_analysis_bindings,
        )

        bundle = _bundle()
        bindings = resolve_analysis_bindings(
            question="describe TB notes alongside age",
            cohort_id="cohort_a",
            catalog=bundle["catalog"],
            dataset_schema=bundle["schema"],
            outcome_variable_id="TB_RECUR",
            predictor_variable_ids=["AGE"],
            derived_variable_ids=["TB_NOTES"],
        )
        # The resolver returns review_required for TB_NOTES; the engine
        # gate must refuse to run analysis on review-required bindings.
        with pytest.raises(AnalysisBindingError):
            engine.validate_catalog_bindings(bindings, require_source_backed=True)
