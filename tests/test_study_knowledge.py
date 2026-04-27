"""Tests for scripts.ai_assistant.study_knowledge — YAML-driven variable lookup."""

from __future__ import annotations

import pytest

from scripts.ai_assistant.study_knowledge import StudyKnowledge


class TestStudyKnowledgeLoad:
    """Test loading and basic properties."""

    def test_load_default_yaml(self, study_knowledge_fixture: StudyKnowledge) -> None:
        sk = study_knowledge_fixture
        assert sk.study_name == "Indo-VAP"
        assert "cohort" in sk.study_description.lower() or "TB" in sk.study_description

    def test_load_missing_yaml_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            StudyKnowledge(tmp_path / "nonexistent.yaml")


class TestListCohorts:
    def test_list_cohorts(self, study_knowledge_fixture: StudyKnowledge) -> None:
        cohorts = study_knowledge_fixture.list_cohorts()
        assert "cohort_a" in cohorts
        assert "cohort_b" in cohorts

    def test_invalid_cohort_raises(self, study_knowledge_fixture: StudyKnowledge) -> None:
        with pytest.raises(ValueError, match="Unknown cohort"):
            study_knowledge_fixture.get_cohort("cohort_z")


class TestResolveConcept:
    def test_smoking_cohort_a(self, study_knowledge_fixture: StudyKnowledge) -> None:
        info = study_knowledge_fixture.resolve_concept("smoking", "cohort_a")
        assert info["column"] == "IC_SMOKHX"
        assert info["dataset"] == "2A_ICBaseline.jsonl"
        assert "binary_map" in info

    def test_smoking_cohort_b(self, study_knowledge_fixture: StudyKnowledge) -> None:
        info = study_knowledge_fixture.resolve_concept("smoking", "cohort_b")
        assert info["column"] == "HC_SMOKHX"
        assert info["dataset"] == "2B_HCBaseline.jsonl"

    def test_sex_is_demographic(self, study_knowledge_fixture: StudyKnowledge) -> None:
        info = study_knowledge_fixture.resolve_concept("sex", "cohort_a")
        assert info["section"] == "demographics"
        assert info["column"] == "IS_SEX"

    def test_unknown_concept_raises(self, study_knowledge_fixture: StudyKnowledge) -> None:
        with pytest.raises(KeyError, match="Unknown concept"):
            study_knowledge_fixture.resolve_concept("nonexistent", "cohort_a")


class TestListConcepts:
    def test_all_concepts_present(self, study_knowledge_fixture: StudyKnowledge) -> None:
        concepts = study_knowledge_fixture.list_concepts("cohort_a")
        assert "sex" in concepts
        assert "age" in concepts
        assert "smoking" in concepts
        assert "diabetes" in concepts
        assert "alcohol" in concepts


class TestGetOutcome:
    def test_recurrence_cohort_a(self, study_knowledge_fixture: StudyKnowledge) -> None:
        outcome = study_knowledge_fixture.get_outcome("cohort_a", "recurrence")
        assert outcome["column"] == "FOA_COHAOUT"
        assert outcome["dataset"] == "98A_FOA.jsonl"
        assert "Bacteriologic relapse" in outcome["positive_labels"]

    def test_incident_tb_cohort_b(self, study_knowledge_fixture: StudyKnowledge) -> None:
        outcome = study_knowledge_fixture.get_outcome("cohort_b", "incident_tb")
        assert outcome["column"] == "FOB_COHBOUT"
        assert "additional_sources" in outcome

    def test_unknown_outcome_raises(self, study_knowledge_fixture: StudyKnowledge) -> None:
        with pytest.raises(KeyError, match="Unknown outcome"):
            study_knowledge_fixture.get_outcome("cohort_a", "nonexistent")


class TestDefaultOutcome:
    def test_cohort_a_default(self, study_knowledge_fixture: StudyKnowledge) -> None:
        name, _info = study_knowledge_fixture.get_default_outcome("cohort_a")
        assert name == "recurrence"

    def test_cohort_b_default(self, study_knowledge_fixture: StudyKnowledge) -> None:
        name, _info = study_knowledge_fixture.get_default_outcome("cohort_b")
        assert name == "incident_tb"


class TestGetValueEncoding:
    def test_smoking_column(self, study_knowledge_fixture: StudyKnowledge) -> None:
        enc = study_knowledge_fixture.get_value_encoding("IC_SMOKHX", "cohort_a")
        assert "binary_map" in enc
        assert enc["type"] == "categorical"

    def test_sex_column(self, study_knowledge_fixture: StudyKnowledge) -> None:
        enc = study_knowledge_fixture.get_value_encoding("IS_SEX", "cohort_a")
        assert "encoding" in enc
        assert enc["encoding"]["Male"] == 1

    def test_unknown_column_raises(self, study_knowledge_fixture: StudyKnowledge) -> None:
        with pytest.raises(KeyError, match="not found"):
            study_knowledge_fixture.get_value_encoding("NONEXISTENT", "cohort_a")


class TestGetJoinPlan:
    def test_smoking_diabetes_plan(self, study_knowledge_fixture: StudyKnowledge) -> None:
        plan = study_knowledge_fixture.get_join_plan("cohort_a", ["smoking", "diabetes"])
        datasets = [p["dataset"] for p in plan]
        # Should include screening (always) + baseline (where smoking/diabetes live)
        assert "1A_ICScreening.jsonl" in datasets
        assert "2A_ICBaseline.jsonl" in datasets

    def test_bmi_includes_source_datasets(self, study_knowledge_fixture: StudyKnowledge) -> None:
        plan = study_knowledge_fixture.get_join_plan("cohort_a", ["bmi"])
        datasets = [p["dataset"] for p in plan]
        assert "2A_ICBaseline.jsonl" in datasets  # height, weight, knee_height

    def test_join_key_is_subjid(self, study_knowledge_fixture: StudyKnowledge) -> None:
        plan = study_knowledge_fixture.get_join_plan("cohort_a", ["smoking"])
        for entry in plan:
            assert entry["join_key"] == "SUBJID"


class TestGetDerivedVariable:
    def test_bmi_definition(self, study_knowledge_fixture: StudyKnowledge) -> None:
        bmi = study_knowledge_fixture.get_derived_variable("bmi", "cohort_a")
        assert "formula" in bmi
        assert "sources" in bmi
        assert "weight" in bmi["sources"]
        assert "height" in bmi["sources"]

    def test_unknown_derived_raises(self, study_knowledge_fixture: StudyKnowledge) -> None:
        with pytest.raises(KeyError, match="Unknown derived"):
            study_knowledge_fixture.get_derived_variable("nonexistent", "cohort_a")
