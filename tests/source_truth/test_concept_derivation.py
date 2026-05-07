# tests/source_truth/test_concept_derivation.py
"""Tests for SoT-derived concept index.

Concept derivation is purely structural — it reads ONLY the SoT
policy artifacts and emits cohort/outcome/exposure/schedule/definition
groupings without any hand-authored definition wording.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.source_truth.concept_derivation import (
    ConceptDerivationError,
    derive_cohorts,
    derive_concept_index,
    derive_definitions,
    derive_exposures,
    derive_outcomes,
    derive_schedules,
)
from scripts.source_truth.policy_loader import load_policy_yaml

FIXTURE_DIR = Path("tests/fixtures/build_mini/data/Mini")
POLICIES_DIR = FIXTURE_DIR / "SoT"


def _mini_artifacts():
    return [
        load_policy_yaml(POLICIES_DIR / "1A_ICScreening_policy.yaml"),
        load_policy_yaml(POLICIES_DIR / "2A_ICBaseline_policy.yaml"),
        load_policy_yaml(POLICIES_DIR / "19_Smear_policy.yaml"),
    ]


def _make_synthetic_form(
    form: str,
    *,
    title: str,
    sections: dict[str, str | None] | None = None,
    records: list[dict] | None = None,
    study: str = "Synthetic",
) -> dict:
    """Construct a minimal in-memory policy artifact for tests.

    Mimics the shape produced by ``load_policy_yaml``.
    """
    pdf_sections = {}
    for sec_name, sec_label in (sections or {}).items():
        pdf_sections[sec_name] = {
            "section_label": sec_label,
            "fields": [],
            "visible_text": "",
        }
    return {
        "schema_version": 2,
        "study": study,
        "form": form,
        "records": records or [],
        "pdf_form_metadata": {"form_title": title},
        "pdf_sections": pdf_sections,
    }


def _make_record(
    variable_id: str,
    *,
    section: str | None = None,
    sensitivity_flags: list[str] | None = None,
    section_grouping: str | None = None,
) -> dict:
    """Construct a translated-record-shape dict (matches policy_loader output)."""
    normalized: dict = {
        "label": variable_id.lower(),
        "confidence": "high",
        "normalization_basis": "synthetic",
        "handling_action": "retain",
    }
    if sensitivity_flags:
        normalized["sensitivity_flags"] = sensitivity_flags
    if section:
        normalized["section"] = section
    if section_grouping:
        normalized["relationships"] = {"section_grouping": section_grouping}
    return {
        "variable_id": variable_id,
        "source_kind": "matched",
        "review_state": "reviewed",
        "presence": {
            "dataset": {"present": True, "column": variable_id},
            "pdf": {"present": True, "annotation_status": "direct"},
            "dictionary": {"present": False},
        },
        "exact_source_wording": {
            "dataset_column": variable_id,
            "pdf_question": None,
            "pdf_options": [],
            "dictionary_label": None,
        },
        "normalized": normalized,
        "source_references": {
            "dataset": {"column": variable_id},
            "pdf": {"annotation_status": "direct", "annotation_pages": []},
        },
        "derivation_targets": ["catalog"],
    }


# ---------------------------------------------------------------------------
# derive_cohorts
# ---------------------------------------------------------------------------


def test_derive_cohorts_groups_by_a_b_suffix():
    """1A and 2A go to cohort_a; 1B goes to cohort_b. 7_Culture is skipped."""
    forms = [
        _make_synthetic_form(
            "1A_ICScreening",
            title="Index Case Screening",
            records=[
                _make_record("SUBJID", sensitivity_flags=["subject_identifier"]),
            ],
        ),
        _make_synthetic_form(
            "2A_ICBaseline",
            title="Index Baseline",
            records=[_make_record("IC_WEIGHT")],
        ),
        _make_synthetic_form(
            "1B_HCScreening",
            title="HC Screening",
            records=[
                _make_record("SUBJID", sensitivity_flags=["subject_identifier"]),
            ],
        ),
        _make_synthetic_form("7_Culture", title="Culture", records=[]),
    ]
    cohorts = derive_cohorts(forms)
    assert "cohort_a" in cohorts
    assert "cohort_b" in cohorts
    assert "1A_ICScreening" in cohorts["cohort_a"]["member_forms"]
    assert "2A_ICBaseline" in cohorts["cohort_a"]["member_forms"]
    assert "1B_HCScreening" in cohorts["cohort_b"]["member_forms"]
    # 7_Culture should not appear in any cohort
    for cid, body in cohorts.items():
        assert "7_Culture" not in body["member_forms"]


def test_derive_cohorts_marks_subject_identifier_role():
    """SUBJID variables marked as subject_identifier get role=identifier."""
    forms = [
        _make_synthetic_form(
            "1A_ICScreening",
            title="Index Case Screening",
            records=[
                _make_record("SUBJID", sensitivity_flags=["subject_identifier"]),
                _make_record("IS_AGE"),
            ],
        ),
    ]
    cohorts = derive_cohorts(forms)
    members = cohorts["cohort_a"]["member_variables"]
    by_vid = {m["variable_id"]: m for m in members}
    assert by_vid["SUBJID"]["role"] == "identifier"
    # non-identifier variables present, but role != identifier
    assert by_vid["IS_AGE"]["role"] != "identifier"


def test_derive_cohorts_on_mini_fixture():
    """Mini fixture has 1A and 2A → cohort_a populated."""
    artifacts = _mini_artifacts()
    cohorts = derive_cohorts(artifacts)
    assert "cohort_a" in cohorts
    assert "1A_ICScreening" in cohorts["cohort_a"]["member_forms"]
    assert "2A_ICBaseline" in cohorts["cohort_a"]["member_forms"]


# ---------------------------------------------------------------------------
# derive_outcomes
# ---------------------------------------------------------------------------


def test_derive_outcomes_emits_adverse_event_from_95_SAE():
    forms = [
        _make_synthetic_form(
            "95_SAE",
            title="SERIOUS ADVERSE EVENT FORM",
            records=[
                _make_record("SUBJID", sensitivity_flags=["subject_identifier"]),
                _make_record("AE_EVENT", section="adverse_event"),
                _make_record("AE_SEVERITY", section="adverse_event"),
            ],
        ),
    ]
    outcomes = derive_outcomes(forms)
    # At least one outcome key derived from the SAE form
    assert outcomes, "expected at least one outcome from 95_SAE"
    # Find the entry whose member_forms include 95_SAE
    sae_outcomes = [v for v in outcomes.values() if "95_SAE" in v["member_forms"]]
    assert sae_outcomes
    out = sae_outcomes[0]
    member_vids = {m["variable_id"] for m in out["member_variables"]}
    assert "AE_EVENT" in member_vids
    assert "AE_SEVERITY" in member_vids


def test_derive_outcomes_empty_when_no_outcome_forms():
    forms = [
        _make_synthetic_form(
            "1A_ICScreening", title="Index Case Screening", records=[],
        ),
    ]
    outcomes = derive_outcomes(forms)
    assert outcomes == {}


# ---------------------------------------------------------------------------
# derive_exposures
# ---------------------------------------------------------------------------


def test_derive_exposures_picks_up_alcohol_and_smoking_from_baseline():
    """2A baseline has 'alcohol' and 'smoking_history' sections → 2 exposures."""
    artifacts = _mini_artifacts()
    exposures = derive_exposures(artifacts)
    # Each entry's name field should not be empty
    for entry in exposures.values():
        assert entry.get("name")
        assert "2A_ICBaseline" in entry["member_forms"]
    # We expect at least two exposure entries based on Mini 2A's sections
    section_keys = set(exposures.keys())
    # At least 2 keys related to alcohol/smoking/medical_history/diet
    assert len(exposures) >= 2, (
        f"expected ≥2 exposures from Mini 2A, got {section_keys}"
    )


def test_derive_exposures_skips_non_baseline_forms():
    """exposures only pulled from baseline forms; 19_Smear contributes nothing."""
    forms = [
        _make_synthetic_form(
            "19_Smear",
            title="AFB Microscopy",
            records=[_make_record("SM_RESULT", section="sputum_smear_result")],
        ),
    ]
    exposures = derive_exposures(forms)
    assert exposures == {}


# ---------------------------------------------------------------------------
# derive_schedules
# ---------------------------------------------------------------------------


def test_derive_schedules_classifies_by_form_title():
    forms = [
        _make_synthetic_form("1A_ICScreening", title="Index Case Screening"),
        _make_synthetic_form("2A_ICBaseline", title="Index- Baseline"),
        _make_synthetic_form("12A_FUA", title="Index Case Follow-up Visit Form"),
        _make_synthetic_form("12B_FUB", title="Household Contact Follow-up Visit Form"),
        _make_synthetic_form("95_SAE", title="SERIOUS ADVERSE EVENT FORM"),
    ]
    schedules = derive_schedules(forms)
    phases = {entry["phase"] for entry in schedules.values()}
    assert "screening" in phases
    assert "baseline" in phases
    assert "follow_up_a" in phases
    assert "follow_up_b" in phases
    assert "adverse_event" in phases


def test_derive_schedules_final_outcome_phase():
    forms = [
        _make_synthetic_form("98A_FOA", title="Final Outcome Determination Form"),
        _make_synthetic_form("99A_FSA", title="Off Study Form for Cohort A"),
    ]
    schedules = derive_schedules(forms)
    phases = {entry["phase"] for entry in schedules.values()}
    assert "final_outcome" in phases


# ---------------------------------------------------------------------------
# derive_definitions
# ---------------------------------------------------------------------------


def test_derive_definitions_includes_section_labels_no_definition_text():
    forms = [
        _make_synthetic_form(
            "1A_ICScreening",
            title="Index Case Screening",
            sections={
                "participant_header": None,
                "inclusion_criteria": "INCLUSION CRITERIA",
                "exclusion_criteria": "EXCLUSION CRITERIA",
            },
        ),
    ]
    defs = derive_definitions(forms)
    assert "1A_ICScreening" in defs
    entry = defs["1A_ICScreening"]
    assert entry["form"] == "1A_ICScreening"
    assert entry["form_title"] == "Index Case Screening"
    assert "INCLUSION CRITERIA" in entry["section_labels"]
    assert "EXCLUSION CRITERIA" in entry["section_labels"]
    # No human-authored wording field
    assert "definition_text" not in entry
    assert "definition" not in entry


def test_derive_definitions_filters_null_section_labels():
    forms = [
        _make_synthetic_form(
            "1A_ICScreening",
            title="Index Case Screening",
            sections={
                "participant_header": None,
                "inclusion_criteria": "INCLUSION CRITERIA",
            },
        ),
    ]
    defs = derive_definitions(forms)
    labels = defs["1A_ICScreening"]["section_labels"]
    assert None not in labels
    assert labels == ["INCLUSION CRITERIA"]


# ---------------------------------------------------------------------------
# derive_concept_index
# ---------------------------------------------------------------------------


def test_derive_concept_index_aggregates_all_passes_with_top_level_keys():
    artifacts = _mini_artifacts()
    index = derive_concept_index(artifacts)
    assert index["schema_version"] == 1
    assert index["policy_status"] == "derived_from_sot"
    assert index["study"] == "Mini"
    for section in ("cohorts", "outcomes", "exposures", "schedules", "definitions"):
        assert section in index


def test_derive_concept_index_byte_identical_repeat_run():
    """Calling derive_concept_index twice yields byte-identical canonical JSON."""
    artifacts = _mini_artifacts()
    a = derive_concept_index(artifacts)
    b = derive_concept_index(artifacts)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_derive_concept_index_rejects_empty_input():
    with pytest.raises(ConceptDerivationError):
        derive_concept_index([])


def test_derive_concept_index_definitions_cover_every_form():
    artifacts = _mini_artifacts()
    index = derive_concept_index(artifacts)
    forms_in = {a["form"] for a in artifacts}
    forms_out = set(index["definitions"].keys())
    assert forms_in == forms_out
