"""Tests for analytical_engine.CohortBuilder and helper functions."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.ai_assistant.analytical_engine import (
    CohortBuilder,
    _apply_binary_map,
    _apply_encoding,
)
from scripts.ai_assistant.study_knowledge import StudyKnowledge


class TestApplyBinaryMap:
    """Test text→binary encoding helper."""

    def test_positive_values(self) -> None:
        binary_map = {
            "positive": ["Yes, current smoker (Go to 18)", "Yes, former smoker (Go to 18)"],
            "negative": ["No (Skip to 19)", "No, never"],
        }
        s = pd.Series(
            ["Yes, current smoker (Go to 18)", "No (Skip to 19)", "Yes, former smoker (Go to 18)"]
        )
        result = _apply_binary_map(s, binary_map)
        assert result.tolist() == [1.0, 0.0, 1.0]

    def test_case_insensitive(self) -> None:
        binary_map = {"positive": ["Yes"], "negative": ["No"]}
        s = pd.Series(["YES", "no", "yes", "NO"])
        result = _apply_binary_map(s, binary_map)
        assert result.tolist() == [1.0, 0.0, 1.0, 0.0]

    def test_unknown_value_returns_none(self) -> None:
        binary_map = {"positive": ["Yes"], "negative": ["No"]}
        s = pd.Series(["Maybe", "Yes"])
        result = _apply_binary_map(s, binary_map)
        assert result.iloc[0] is None or pd.isna(result.iloc[0])
        assert result.iloc[1] == 1.0

    def test_dont_know_is_negative_for_diabetes(self) -> None:
        binary_map = {
            "positive": ["Yes", "Yes (Go to 21a-b)"],
            "negative": ["No", "Don't know", "Don't know (Skip to 25)"],
        }
        s = pd.Series(["Don't know", "Yes (Go to 21a-b)", "No"])
        result = _apply_binary_map(s, binary_map)
        assert result.tolist() == [0.0, 1.0, 0.0]


class TestApplyEncoding:
    def test_sex_encoding(self) -> None:
        encoding = {"Male": 1, "Female": 0}
        s = pd.Series(["Male", "Female", "Male"])
        result = _apply_encoding(s, encoding)
        assert result.tolist() == [1, 0, 1]

    def test_case_insensitive(self) -> None:
        encoding = {"Male": 1, "Female": 0}
        s = pd.Series(["male", "FEMALE"])
        result = _apply_encoding(s, encoding)
        assert result.tolist() == [1, 0]


class TestCohortBuilderSynthetic:
    """Test CohortBuilder with synthetic JSONL data."""

    def test_build_cohort_a_shape(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["smoking", "diabetes", "bmi", "age", "sex"], "recurrence")
        assert len(df) == 50  # all subjects should be present
        assert "recurrence" in df.columns
        assert "smoking" in df.columns
        assert "sex" in df.columns
        assert "age" in df.columns

    def test_smoking_is_binary(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["smoking"], "recurrence")
        valid = df["smoking"].dropna()
        assert set(valid.unique()).issubset({0.0, 1.0})

    def test_sex_is_binary(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["age", "sex"], "recurrence")
        valid = df["sex"].dropna()
        assert set(valid.unique()).issubset({0, 1})

    def test_diabetes_encoding(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["diabetes"], "recurrence")
        valid = df["diabetes"].dropna()
        assert set(valid.unique()).issubset({0.0, 1.0})

    def test_recurrence_count(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["smoking"], "recurrence")
        assert df["recurrence"].sum() == 5  # synthetic data has 5 events

    def test_bmi_plausible_range(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["bmi"], "recurrence")
        valid_bmi = df["bmi"].dropna()
        assert all(valid_bmi.between(10, 60))

    def test_bmi_uses_chumlea_when_height_missing(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["bmi"], "recurrence")
        # Subject 0 (i=0) has height="" (missing) — BMI comes from Chumlea
        # Subject 0: knee=40.0, age=20 → H = 2.02*40 - 0.04*20 + 64.19 = 80.8-0.8+64.19=144.19
        # BMI = 50.0 / (1.4419^2) ≈ 24.04
        bmi_0 = df.loc[df["SUBJID"] == "SUBJ-0000", "bmi"]
        assert not bmi_0.isna().all(), "BMI should be calculated via Chumlea for missing height"

    def test_alcohol_ordinal_range(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_a", ["alcohol"], "recurrence")
        valid = df["alcohol_freq"].dropna()
        assert all(valid.isin([0, 1, 2, 3, 4]))

    def test_cohort_b_combines_fob_and_fub(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        df = builder.build("cohort_b", ["smoking"], "incident_tb")
        # FOB: 2 events (i=0,1) + FUB: 1 additional event (i=2) = 3 total
        assert df["incident_tb"].sum() == 3

    def test_invalid_cohort_raises(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
    ) -> None:
        builder = CohortBuilder(study_knowledge_fixture, synthetic_cohort_data)
        with pytest.raises(ValueError, match="Unknown cohort"):
            builder.build("cohort_z", ["smoking"], "recurrence")
