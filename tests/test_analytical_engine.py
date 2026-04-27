"""Tests for analytical_engine analysis classes and run_full_analysis."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.ai_assistant.analytical_engine import (
    AnalysisResult,
    DescriptiveAnalyzer,
    InteractionAnalyzer,
    MultivariateAnalyzer,
    PlotGenerator,
    ResultInterpreter,
    UnivariateAnalyzer,
    run_full_analysis,
)
from scripts.ai_assistant.study_knowledge import StudyKnowledge

# ── Univariate Analyzer ──────────────────────────────────────────────────


class TestUnivariateAnalyzer:
    def test_significant_predictor(self) -> None:
        np.random.seed(42)
        n = 200
        x = np.random.normal(0, 1, n)
        y = (x + np.random.normal(0, 0.5, n) > 0.5).astype(int)
        df = pd.DataFrame({"outcome": y, "x": x})
        analyzer = UnivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["x"])
        assert len(result) == 1
        assert result.iloc[0]["p_value"] < 0.05
        assert result.iloc[0]["significant"]

    def test_nonsignificant_predictor(self) -> None:
        np.random.seed(42)
        n = 200
        df = pd.DataFrame(
            {
                "outcome": np.random.binomial(1, 0.5, n),
                "noise": np.random.normal(0, 1, n),
            }
        )
        analyzer = UnivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["noise"])
        # Random noise may occasionally be significant—just check it runs
        assert len(result) == 1
        assert "p_value" in result.columns

    def test_too_few_observations(self) -> None:
        df = pd.DataFrame({"outcome": [1, 0], "x": [1.0, 2.0]})
        analyzer = UnivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["x"])
        assert np.isnan(result.iloc[0]["OR"])

    def test_multiple_predictors(self) -> None:
        np.random.seed(42)
        n = 100
        df = pd.DataFrame(
            {
                "outcome": np.random.binomial(1, 0.3, n),
                "x1": np.random.normal(0, 1, n),
                "x2": np.random.choice([0, 1], n),
            }
        )
        analyzer = UnivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["x1", "x2"])
        assert len(result) == 2


# ── Multivariate Analyzer ──────────────────────────────────────────────────


class TestMultivariateAnalyzer:
    def test_backward_selection(self) -> None:
        np.random.seed(42)
        n = 300
        x1 = np.random.normal(0, 1, n)
        noise = np.random.normal(0, 1, n)
        y = (x1 + np.random.normal(0, 0.5, n) > 0.5).astype(int)
        df = pd.DataFrame({"outcome": y, "signal": x1, "noise": noise})
        analyzer = MultivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["signal", "noise"])
        assert result.get("converged", False) or "error" not in result
        # Signal should be retained
        if "retained_predictors" in result:
            assert "signal" in result["retained_predictors"]

    def test_no_variation_outcome(self) -> None:
        df = pd.DataFrame(
            {
                "outcome": [0] * 50,
                "x": np.random.normal(0, 1, 50),
            }
        )
        analyzer = MultivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["x"])
        assert "error" in result

    def test_singular_matrix_graceful(self) -> None:
        """Simulates underpowered scenario (like Cohort B with 4 events)."""
        np.random.seed(42)
        n = 50
        df = pd.DataFrame(
            {
                "outcome": [1] * 3 + [0] * (n - 3),
                "x1": np.random.choice([0, 1], n),
                "x2": np.random.choice([0, 1], n),
                "x3": np.random.normal(0, 1, n),
            }
        )
        analyzer = MultivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["x1", "x2", "x3"])
        # Should either succeed or fail gracefully
        assert isinstance(result, dict)
        if "error" in result:
            assert "recommendation" in result or result.get("converged") is False

    def test_model_none_init_no_unboundlocalerror(self) -> None:
        """Verify model=None initialization prevents UnboundLocalError."""
        df = pd.DataFrame(
            {
                "outcome": [1, 0] * 5,
                "x": [1.0] * 10,  # Zero variance → fit will fail
            }
        )
        analyzer = MultivariateAnalyzer()
        result = analyzer.run(df, "outcome", ["x"])
        # Should not raise UnboundLocalError
        assert isinstance(result, dict)


# ── Interaction Analyzer ──────────────────────────────────────────────────


class TestInteractionAnalyzer:
    def test_runs_without_error(self) -> None:
        np.random.seed(42)
        n = 100
        df = pd.DataFrame(
            {
                "outcome": np.random.binomial(1, 0.3, n),
                "factor": np.random.choice([0, 1], n),
                "moderator": np.random.normal(40, 10, n),
            }
        )
        analyzer = InteractionAnalyzer()
        result = analyzer.run(df, "outcome", ["factor"], ["moderator"])
        assert len(result) == 1
        assert "interaction_p" in result.columns

    def test_min_sample_check(self) -> None:
        """With n < 30, interaction should return NaN."""
        df = pd.DataFrame(
            {
                "outcome": [1, 0] * 10,
                "factor": [0, 1] * 10,
                "moderator": list(range(20)),
            }
        )
        analyzer = InteractionAnalyzer()
        result = analyzer.run(df, "outcome", ["factor"], ["moderator"])
        assert np.isnan(result.iloc[0]["interaction_p"])

    def test_multiple_factors_and_moderators(self) -> None:
        """Cartesian product: 2 factors x 2 moderators = 4 rows."""
        np.random.seed(42)
        n = 100
        df = pd.DataFrame(
            {
                "outcome": np.random.binomial(1, 0.3, n),
                "f1": np.random.choice([0, 1], n),
                "f2": np.random.choice([0, 1], n),
                "m1": np.random.normal(0, 1, n),
                "m2": np.random.normal(0, 1, n),
            }
        )
        analyzer = InteractionAnalyzer()
        result = analyzer.run(df, "outcome", ["f1", "f2"], ["m1", "m2"])
        assert len(result) == 4

    def test_no_outcome_variation_returns_nan(self) -> None:
        """When outcome has no variation, interaction p should be NaN."""
        n = 50
        df = pd.DataFrame(
            {
                "outcome": [0] * n,
                "factor": np.random.choice([0, 1], n),
                "moderator": np.random.normal(0, 1, n),
            }
        )
        analyzer = InteractionAnalyzer()
        result = analyzer.run(df, "outcome", ["factor"], ["moderator"])
        assert np.isnan(result.iloc[0]["interaction_p"])

    def test_perfect_separation_graceful(self) -> None:
        """Perfect separation should be handled gracefully (no crash)."""
        np.random.seed(42)
        n = 60
        factor = np.array([0] * 30 + [1] * 30)
        outcome = factor.copy()  # perfectly separable
        moderator = np.random.normal(0, 1, n)
        df = pd.DataFrame(
            {
                "outcome": outcome,
                "factor": factor,
                "moderator": moderator,
            }
        )
        analyzer = InteractionAnalyzer()
        result = analyzer.run(df, "outcome", ["factor"], ["moderator"])
        # Should either converge or return NaN gracefully
        assert len(result) == 1
        row = result.iloc[0]
        assert isinstance(row["interaction_p"], float)

    def test_missing_values_dropped(self) -> None:
        """Rows with NaN should be dropped before fitting."""
        np.random.seed(42)
        n = 60
        df = pd.DataFrame(
            {
                "outcome": np.random.binomial(1, 0.3, n),
                "factor": np.random.choice([0, 1], n).astype(float),
                "moderator": np.random.normal(0, 1, n),
            }
        )
        df.loc[0:9, "factor"] = np.nan  # 10 NaN rows
        analyzer = InteractionAnalyzer()
        result = analyzer.run(df, "outcome", ["factor"], ["moderator"])
        assert result.iloc[0]["n"] == 50  # 60 - 10 NaN rows


# ── Descriptive Analyzer ──────────────────────────────────────────────────


class TestDescriptiveAnalyzer:
    def test_continuous_stats(self) -> None:
        df = pd.DataFrame({"age": [20, 30, 40, 50, 60, 70, 80, 90, 100, 110]})
        analyzer = DescriptiveAnalyzer()
        result = analyzer.run(df, ["age"])
        stats = result["age"]
        assert stats["type"] == "continuous"
        assert stats["mean"] == 65.0
        assert stats["n_valid"] == 10

    def test_categorical_counts(self) -> None:
        df = pd.DataFrame({"sex": [0, 0, 1, 1, 1]})
        analyzer = DescriptiveAnalyzer()
        result = analyzer.run(df, ["sex"])
        stats = result["sex"]
        assert stats["type"] == "categorical"
        assert stats["counts"][1] == 3


# ── Plot Generator ──────────────────────────────────────────────────────


class TestPlotGenerator:
    def test_violin_creates_file(self, analysis_output_dir: Path) -> None:
        df = pd.DataFrame(
            {
                "outcome": [0, 0, 1, 1, 0, 1, 0, 0],
                "smoking": [0, 0, 1, 1, 1, 0, 0, 1],
            }
        )
        plotter = PlotGenerator()
        artifacts = plotter.generate(df, "outcome", "smoking", "violin", analysis_output_dir)
        assert artifacts is not None
        saved_path = artifacts.interactive or artifacts.static
        assert saved_path is not None
        assert saved_path.exists()
        assert saved_path.stat().st_size > 0

    def test_scatter_creates_file(self, analysis_output_dir: Path) -> None:
        np.random.seed(42)
        df = pd.DataFrame(
            {
                "outcome": np.random.binomial(1, 0.3, 50),
                "bmi": np.random.normal(25, 5, 50),
            }
        )
        plotter = PlotGenerator()
        artifacts = plotter.generate(df, "outcome", "bmi", "scatter", analysis_output_dir)
        assert artifacts is not None
        saved_path = artifacts.interactive or artifacts.static
        assert saved_path is not None
        assert saved_path.exists()

    def test_interaction_violin_creates_file(self, analysis_output_dir: Path) -> None:
        np.random.seed(42)
        n = 100
        df = pd.DataFrame(
            {
                "outcome": np.random.binomial(1, 0.3, n),
                "smoking": np.random.choice([0, 1], n),
                "age": np.random.normal(40, 15, n),
                "sex": np.random.choice([0, 1], n),
            }
        )
        plotter = PlotGenerator()
        artifacts = plotter.generate(
            df, "outcome", "smoking", "interaction_violin", analysis_output_dir
        )
        assert artifacts is not None
        saved_path = artifacts.interactive or artifacts.static
        assert saved_path is not None
        assert saved_path.exists()

    def test_unknown_plot_type_returns_none(self, analysis_output_dir: Path) -> None:
        df = pd.DataFrame({"outcome": [0, 1], "x": [1, 2]})
        plotter = PlotGenerator()
        path = plotter.generate(df, "outcome", "x", "nonexistent_type", analysis_output_dir)
        assert path is None


# ── Result Interpreter ──────────────────────────────────────────────────


class TestResultInterpreter:
    def test_univariate_interpretation(self) -> None:
        results = pd.DataFrame(
            {
                "predictor": ["age", "sex"],
                "n": [100, 100],
                "OR": [0.98, 2.0],
                "p_value": [0.01, 0.04],
                "ci_lo": [0.96, 1.05],
                "ci_hi": [1.0, 3.8],
                "significant": [True, True],
            }
        )
        interp = ResultInterpreter()
        text = interp.interpret_univariate(results, "recurrence", "Index Cases")
        assert "significant" in text.lower()
        assert "age" in text
        assert "sex" in text

    def test_multivariate_failure_interpretation(self) -> None:
        result = {
            "error": "Initial model fit failed: Singular matrix",
            "converged": False,
            "n": 50,
            "retained_predictors": [],
            "recommendation": "Consider Firth's penalized regression for rare events",
        }
        interp = ResultInterpreter()
        text = interp.interpret_multivariate(result, "Household Contacts")
        assert "failed" in text.lower() or "error" in text.lower()
        assert "Firth" in text

    def test_caveats_underpowered(self) -> None:
        df = pd.DataFrame(
            {
                "outcome": [1, 1, 1, 0, 0, 0, 0, 0, 0, 0] * 5,
            }
        )
        interp = ResultInterpreter()
        text = interp.generate_caveats(df, "outcome", "Test Cohort")
        assert "underpowered" in text.lower() or "low power" in text.lower()


# ── run_full_analysis (integration with synthetic data) ────────────────


class TestRunFullAnalysis:
    def test_cohort_a_synthetic(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
        analysis_output_dir: Path,
    ) -> None:
        result = run_full_analysis(
            knowledge=study_knowledge_fixture,
            data_dir=synthetic_cohort_data,
            output_dir=analysis_output_dir,
            cohort_id="cohort_a",
            predictors=["smoking", "diabetes", "bmi", "age", "sex"],
            analysis_types=["univariate", "multivariate"],
            plot_types=["violin", "scatter"],
        )
        assert isinstance(result, AnalysisResult)
        assert result.n == 50
        assert result.events == 5
        assert result.univariate is not None
        assert len(result.univariate) > 0
        assert result.narrative != ""
        # Check output file was saved
        assert (analysis_output_dir / "cohort_a_analytic.csv").exists()

    def test_cohort_b_underpowered(
        self,
        synthetic_cohort_data: Path,
        study_knowledge_fixture: StudyKnowledge,
        analysis_output_dir: Path,
    ) -> None:
        result = run_full_analysis(
            knowledge=study_knowledge_fixture,
            data_dir=synthetic_cohort_data,
            output_dir=analysis_output_dir,
            cohort_id="cohort_b",
            predictors=["smoking"],
            analysis_types=["univariate", "multivariate"],
            plot_types=[],
        )
        assert result.events <= 5  # very few events
        assert "underpowered" in result.caveats.lower() or "low power" in result.caveats.lower()
