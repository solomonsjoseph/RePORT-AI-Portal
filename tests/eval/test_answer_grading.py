"""Graded answer-correctness tests (Goal B — Steps 1 & 4).

Deterministic, no-network coverage of ``scripts.eval.answer_grading``:

* the statistical numeric grader self-scores the golden cohort-A univariate
  table at 1.0 and fails on magnitude / significance / privacy perturbations —
  this is the Step-4 regression pin on the ``run_python_analysis`` numbers;
* the table parser is robust to suppressed cells and column reordering;
* the definitional LLM-judge harness parses an injected stub's verdict and
  fails closed on a judge error.

GR-1: every fixture below is an aggregate coefficient already published in the
golden chat reports — no row values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval import answer_grading as grading

_GOLDEN_DIR = Path(__file__).resolve().parents[2] / "tests" / "golden" / "chat_reports"
_COHORT_A = _GOLDEN_DIR / "01_cohort_a_univariate.md"
_COHORT_B = _GOLDEN_DIR / "03_cohort_b_univariate.md"


def _golden(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------


class TestParseStatTable:
    def test_parses_all_six_predictors(self) -> None:
        rows = grading.parse_stat_table(_golden(_COHORT_A))
        assert set(rows) == {"malnutrition", "diabetes", "alcohol", "smoking", "age", "sex"}

    def test_extracts_known_values(self) -> None:
        rows = grading.parse_stat_table(_golden(_COHORT_A))
        # Sex: OR 2.059, p 0.0355 (the one significant predictor)
        assert rows["sex"].odds_ratio == pytest.approx(2.059, abs=1e-3)
        assert rows["sex"].p_value == pytest.approx(0.0355, abs=1e-4)
        assert rows["sex"].suppressed is False

    def test_suppressed_rows_parse_as_none(self) -> None:
        # Cohort B: every predictor skipped below the k=5 floor.
        rows = grading.parse_stat_table(_golden(_COHORT_B))
        assert rows, "cohort B table should still parse predictor rows"
        assert all(r.suppressed for r in rows.values())
        assert all(r.odds_ratio is None and r.p_value is None for r in rows.values())

    def test_column_reorder_tolerated(self) -> None:
        text = "| Predictor | p-value | Odds ratio |\n|---|---:|---:|\n| Sex | 0.0355 | 2.059 |\n"
        rows = grading.parse_stat_table(text)
        assert rows["sex"].odds_ratio == pytest.approx(2.059, abs=1e-3)
        assert rows["sex"].p_value == pytest.approx(0.0355, abs=1e-4)


# ---------------------------------------------------------------------------
# Statistical grading — the Step-4 regression pin
# ---------------------------------------------------------------------------


class TestGradeStatistical:
    def test_golden_self_grades_perfect(self) -> None:
        """An answer identical to the golden must score exactly 1.0."""
        g = grading.grade_statistical(_golden(_COHORT_A), _golden(_COHORT_A))
        assert g.score == 1.0
        assert g.matched == g.total == 6
        assert not g.privacy_regressions

    def test_or_magnitude_drift_fails_that_row(self) -> None:
        golden = _golden(_COHORT_A)
        # Bump Sex OR 2.059 -> 3.5 (well beyond the 5% relative tolerance).
        perturbed = golden.replace("2.059", "3.500")
        g = grading.grade_statistical(perturbed, golden)
        assert g.per_predictor["sex"].startswith("mismatch")
        assert g.matched == 5 and g.score == pytest.approx(round(5 / 6, 4))

    def test_significance_flip_fails_even_within_abs_tol(self) -> None:
        golden = _golden(_COHORT_A)
        # Move Sex p 0.0355 -> 0.0520: only 0.0165 away, but crosses 0.05.
        perturbed = golden.replace("0.0355", "0.0520")
        g = grading.grade_statistical(perturbed, golden)
        assert g.per_predictor["sex"].startswith("mismatch")
        assert "p " in g.per_predictor["sex"]

    def test_small_within_tolerance_drift_still_matches(self) -> None:
        golden = _golden(_COHORT_A)
        # Sex OR 2.059 -> 2.10 (~2% — within 5%) and p 0.0355 -> 0.0360.
        perturbed = golden.replace("2.059", "2.100").replace("0.0355", "0.0360")
        g = grading.grade_statistical(perturbed, golden)
        assert g.per_predictor["sex"] == "match"
        assert g.score == 1.0

    def test_privacy_regression_flagged(self) -> None:
        """Disclosing a number where the golden suppressed is a privacy miss."""
        golden = _golden(_COHORT_B)  # all rows suppressed
        # Agent answer discloses a real OR/p for Sex — a leak.
        leaked = golden.replace(
            "| Sex | 2195 | <5 | - | - | - |",
            "| Sex | 2195 | 7 | 1.85 | 1.1 to 3.1 | 0.0210 |",
        )
        g = grading.grade_statistical(leaked, golden)
        assert "sex" in g.privacy_regressions
        assert g.per_predictor["sex"] == "privacy_regression"
        assert g.matched < g.total

    def test_unparseable_golden_returns_parse_failure(self) -> None:
        g = grading.grade_statistical("some answer", "no table here")
        assert g.parse_ok is False
        assert g.score == 0.0


# ---------------------------------------------------------------------------
# Definitional grading — LLM judge harness
# ---------------------------------------------------------------------------


class TestGradeDefinitional:
    def test_stub_judge_score_parsed(self) -> None:
        def stub(_prompt: str) -> str:
            return '{"score": 0.83, "rationale": "grounded and complete"}'

        score, rationale = grading.grade_definitional(
            "What is a household contact?", "A person sharing a dwelling.", "golden facts", stub
        )
        assert score == pytest.approx(0.83)
        assert "grounded" in rationale

    def test_empty_answer_scores_zero_without_calling_judge(self) -> None:
        calls: list[str] = []

        def stub(prompt: str) -> str:
            calls.append(prompt)
            return '{"score": 1.0}'

        score, _rationale = grading.grade_definitional("Q?", "   ", "golden", stub)
        assert score == 0.0
        assert not calls, "judge must not be called for an empty answer"

    def test_judge_error_fails_closed(self) -> None:
        def boom(_prompt: str) -> str:
            raise RuntimeError("provider down")

        score, rationale = grading.grade_definitional("Q?", "an answer", "golden", boom)
        assert score == 0.0
        assert "judge error" in rationale

    def test_score_clamped_to_unit_interval(self) -> None:
        score, _ = grading.grade_definitional("Q?", "a", "g", lambda _p: '{"score": 7.5}')
        assert score == 1.0

    def test_prompt_contains_all_three_inputs(self) -> None:
        prompt = grading.build_judge_prompt("the question", "the answer", "the golden")
        assert "the question" in prompt
        assert "the answer" in prompt
        assert "the golden" in prompt
