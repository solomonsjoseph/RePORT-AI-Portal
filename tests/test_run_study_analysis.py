"""Tests for the run_study_analysis tool (agent_tools.py Tool #11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ai_assistant.agent_tools import run_study_analysis


class TestRunStudyAnalysisInput:
    def test_missing_cohort(self) -> None:
        result = run_study_analysis.invoke({"cohort": ""})
        assert "Missing required parameter" in result or "cohort" in result.lower()

    def test_unknown_cohort(self, monkeypatch_config: Path) -> None:
        result = run_study_analysis.invoke({"cohort": "cohort_z"})
        assert (
            "error" in result.lower() or "unknown" in result.lower() or "failed" in result.lower()
        )


class TestRunStudyAnalysisWithData:
    def test_cohort_a_basic(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test with synthetic data by temporarily pointing config to it."""
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)
        # AGENT_OUTPUT_DIR is already patched by ``monkeypatch_config`` (via the
        # ``synthetic_cohort_data`` fixture dependency) to a path inside
        # ``AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_a",
                "predictors": "smoking,age,sex",
                "analysis_types": "univariate",
                "plot_types": "",
            }
        )
        assert isinstance(result, str)
        assert "Analysis complete:" in result
        assert "Detailed model tables, plots, and narrative are rendered below." in result
        assert "Tell the user" not in result
        assert "smoking" in result.lower() or "univariate" in result.lower()
        assert len(result) > 100  # Should have substantive content

    def test_cohort_a_multivariate(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test multivariate analysis path."""
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)
        # AGENT_OUTPUT_DIR is already patched by ``monkeypatch_config`` (via the
        # ``synthetic_cohort_data`` fixture dependency) to a path inside
        # ``AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_a",
                "predictors": "smoking,diabetes,age",
                "analysis_types": "univariate,multivariate",
                "plot_types": "",
            }
        )
        assert isinstance(result, str)
        assert len(result) > 100

    def test_cohort_a_descriptive(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test descriptive analysis path."""
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)
        # AGENT_OUTPUT_DIR is already patched by ``monkeypatch_config`` (via the
        # ``synthetic_cohort_data`` fixture dependency) to a path inside
        # ``AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_a",
                "predictors": "smoking,age,sex",
                "analysis_types": "descriptive",
                "plot_types": "",
            }
        )
        assert isinstance(result, str)
        assert len(result) > 50

    def test_figures_markers(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)
        # AGENT_OUTPUT_DIR is already patched by ``monkeypatch_config`` (via the
        # ``synthetic_cohort_data`` fixture dependency) to a path inside
        # ``AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_a",
                "predictors": "smoking",
                "analysis_types": "univariate",
                "plot_types": "violin",
            }
        )
        # Figures are now saved to the narrative file; tool returns an RPLN_ANALYSIS marker
        assert "<RPLN_ANALYSIS:" in result
        # Verify narrative file on disk contains the figure markers
        import re

        match = re.search(r"<RPLN_ANALYSIS:([^>]+)>", result)
        assert match is not None
        narrative_path = Path(match.group(1).strip())
        assert narrative_path.exists()
        narrative = narrative_path.read_text(encoding="utf-8")
        assert "<RPLN_PLOTLY:" in narrative or "<RPLN_FIGURE:" in narrative

    def test_cohort_b_underpowered(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)
        # AGENT_OUTPUT_DIR is already patched by ``monkeypatch_config`` (via the
        # ``synthetic_cohort_data`` fixture dependency) to a path inside
        # ``AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_b",
                "predictors": "smoking",
                "analysis_types": "univariate,multivariate",
                "plot_types": "",
            }
        )
        lower = result.lower()
        # Either the hard-refuse floor fires (events<5) or the underpowered
        # caveat is surfaced in the LLM summary (events<10 or EPV<5). Both
        # paths are acceptable — the test just requires that the tool
        # honestly signals limited statistical power to the agent.
        assert (
            "analysis not run" in lower
            or "event floor" in lower
            or "underpowered" in lower
            or "low power" in lower
        )

    def test_cohort_b_single_predictor(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cohort B with a single predictor for minimal analysis."""
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)
        # AGENT_OUTPUT_DIR is already patched by ``monkeypatch_config`` (via the
        # ``synthetic_cohort_data`` fixture dependency) to a path inside
        # ``AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_b",
                "predictors": "age",
                "analysis_types": "univariate",
                "plot_types": "",
            }
        )
        assert isinstance(result, str)

    def test_empty_predictors_uses_defaults(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When predictors is empty, the tool should use study defaults."""
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)
        # AGENT_OUTPUT_DIR is already patched by ``monkeypatch_config`` (via the
        # ``synthetic_cohort_data`` fixture dependency) to a path inside
        # ``AGENT_STATE_DIR`` so ``validate_agent_write`` accepts it.

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_a",
                "predictors": "",
                "analysis_types": "univariate",
                "plot_types": "",
            }
        )
        assert isinstance(result, str)
        assert len(result) > 50


class TestMalnutritionPredictor:
    def test_malnutrition_accepted_and_narrated(
        self,
        synthetic_cohort_data: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """malnutrition (BMI<18.5) must be accepted as a predictor and reach the narrative."""
        import config

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", synthetic_cohort_data)

        result = run_study_analysis.invoke(
            {
                "cohort": "cohort_a",
                "predictors": "malnutrition,age,sex",
                "analysis_types": "univariate",
                "plot_types": "",
            }
        )
        assert isinstance(result, str)
        narrative_path = config.AGENT_OUTPUT_DIR / "cohort_a_narrative.md"
        assert narrative_path.exists()
        narrative = narrative_path.read_text().lower()
        assert "malnutrition" in narrative


class TestRunStudyAnalysisInAllTools:
    def test_in_all_tools(self) -> None:
        from scripts.ai_assistant.agent_tools import ALL_TOOLS

        names = [t.name for t in ALL_TOOLS]
        assert "run_study_analysis" in names
