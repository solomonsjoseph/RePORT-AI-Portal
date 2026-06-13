"""CI-safe tests for the offline retrieval-evaluation harness.

Three test classes
------------------
TestEvalQuestions    — structural invariants on the gold table (always runs)
TestFakeLocalRouting — fake-local routing contract (always runs, no network,
                       no bundle required)
TestResolvabilityAndLatency
                     — resolvability + latency (pytest.skip when bundle absent)
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

# Require langchain_core or skip the whole module.
langchain_core = pytest.importorskip("langchain_core", reason="langchain_core required")

from scripts.eval.eval_questions import EVAL_QUESTIONS  # noqa: E402

# ---------------------------------------------------------------------------
# TestEvalQuestions — structural invariants on EVAL_QUESTIONS
# ---------------------------------------------------------------------------


class TestEvalQuestions:
    def test_non_empty(self) -> None:
        assert len(EVAL_QUESTIONS) >= 10, "Gold table must have at least 10 questions"

    def test_ids_unique(self) -> None:
        ids = [q.id for q in EVAL_QUESTIONS]
        assert len(ids) == len(set(ids)), "Question IDs must be unique"

    def test_all_have_required_fields(self) -> None:
        for q in EVAL_QUESTIONS:
            assert q.id, f"Missing id: {q}"
            assert q.question, f"Missing question text: {q.id}"
            assert q.kind in ("statistical", "definitional"), f"Invalid kind {q.kind!r} for {q.id}"
            assert q.expected_primary_tool, f"Missing expected_primary_tool: {q.id}"
            assert q.source_files, f"No source_files for {q.id}"
            assert q.backing_columns, f"No backing_columns for {q.id}"

    def test_statistical_questions_present(self) -> None:
        stat = [q for q in EVAL_QUESTIONS if q.kind == "statistical"]
        assert len(stat) >= 4, "Expected at least 4 statistical questions"

    def test_definitional_questions_present(self) -> None:
        defn = [q for q in EVAL_QUESTIONS if q.kind == "definitional"]
        assert len(defn) >= 6, "Expected at least 6 definitional questions"

    def test_cohort_a_questions_present(self) -> None:
        ids = {q.id for q in EVAL_QUESTIONS}
        for qid in ("Q-A1", "Q-A2", "Q-A3"):
            assert qid in ids, f"Required Cohort A question {qid} missing"

    def test_cohort_b_question_present(self) -> None:
        ids = {q.id for q in EVAL_QUESTIONS}
        assert "Q-B1" in ids, "Q-B1 (Cohort B) missing from gold table"

    def test_outcome_column_present_cohort_a(self) -> None:
        q_a1 = next(q for q in EVAL_QUESTIONS if q.id == "Q-A1")
        assert "FOA_COHAOUT" in q_a1.backing_columns

    def test_outcome_column_present_cohort_b(self) -> None:
        q_b1 = next(q for q in EVAL_QUESTIONS if q.id == "Q-B1")
        assert "FOB_COHBOUT" in q_b1.backing_columns

    def test_subjid_join_key_present(self) -> None:
        """Every question should reference the join key SUBJID."""
        for q in EVAL_QUESTIONS:
            assert "SUBJID" in q.backing_columns, (
                f"{q.id}: SUBJID join key missing from backing_columns"
            )

    def test_source_files_use_relative_paths(self) -> None:
        for q in EVAL_QUESTIONS:
            for f in q.source_files:
                assert not f.startswith("/"), (
                    f"{q.id}: source_files must be relative to llm_source/, got {f!r}"
                )


# ---------------------------------------------------------------------------
# TestFakeLocalRouting — routing contract (always runs)
# ---------------------------------------------------------------------------


class TestFakeLocalRouting:
    """Exercises the 3 deterministic routing branches of _FakeLocalChatModel."""

    PROBES: ClassVar[list[dict[str, str]]] = [
        {
            "question": "list available datasets",
            "expected": "list_available_datasets",
        },
        {
            "question": "how many records and stats are in the dataset?",
            "expected": "get_dataset_stats",
        },
        {
            "question": "What is HIV_HIV and how is it coded?",
            "expected": "answer_catalog_question",
        },
    ]

    @pytest.fixture(autouse=True)
    def _patch_env_and_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set fake-local provider.

        STUDY_LLM_SOURCE_DIR intentionally stays at the real output/ tree.
        Tools like get_dataset_stats and list_available_datasets call
        assert_output_zone(), which is hardcoded against the project's real
        output/ directory. answer_catalog_question reads SoT YAMLs via
        load_policy_yaml(), which now calls validate_agent_read() — that
        gate checks the path against STUDY_LLM_SOURCE_DIR, so the real
        output/ path must remain the zone root (a tmp redirect would cause
        zone violations on valid SoT YAML reads).
        """
        import config as _config

        monkeypatch.setenv("REPORTAL_TEST_FAKE_LLM", "1")
        monkeypatch.setattr(_config, "LLM_PROVIDER", "fake-local", raising=False)
        monkeypatch.setattr(_config, "LLM_MODEL", "fake-local", raising=False)

        # Reset cached agent so it picks up the new provider
        from scripts.ai_assistant import agent_graph as ag

        monkeypatch.setattr(ag, "_agent", None)
        monkeypatch.setattr(ag, "_checkpointer", None)

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p["expected"])
    def test_routing_branch(self, probe: dict[str, str]) -> None:
        from langchain_core.messages import HumanMessage, ToolMessage

        from scripts.ai_assistant import agent_graph as ag

        agent = ag.get_agent()
        result = agent.invoke(
            {"messages": [HumanMessage(content=probe["question"])]},
            config={"configurable": {"thread_id": f"eval-ci-{probe['expected']}"}},
        )
        tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert tool_messages, f"No tool was called for question: {probe['question']!r}"
        assert tool_messages[0].name == probe["expected"], (
            f"Expected tool {probe['expected']!r}, got {tool_messages[0].name!r}"
        )

    def test_all_three_branches_accuracy_100pct(self) -> None:
        """Aggregate check: all 3 branches must pass (accuracy = 1.0)."""
        from langchain_core.messages import HumanMessage, ToolMessage

        from scripts.ai_assistant import agent_graph as ag

        correct = 0
        for probe in self.PROBES:
            agent = ag.get_agent()
            result = agent.invoke(
                {"messages": [HumanMessage(content=probe["question"])]},
                config={"configurable": {"thread_id": f"eval-agg-{probe['expected']}"}},
            )
            tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
            if tool_messages and tool_messages[0].name == probe["expected"]:
                correct += 1
        assert correct == len(self.PROBES), (
            f"Expected {len(self.PROBES)}/3 correct, got {correct}/3"
        )


# ---------------------------------------------------------------------------
# TestResolvabilityAndLatency — skip when bundle absent
# ---------------------------------------------------------------------------

_BUNDLE_PATH = Path(__file__).parents[2] / "output" / "Indo-VAP" / "llm_source"
_BUNDLE_DATASETS = _BUNDLE_PATH / "dataset_schema" / "files"


def _bundle_has_datasets() -> bool:
    """True only when the published bundle has actual dataset JSONL content.

    Guards against a half-wiped state where ``llm_source/`` still exists (e.g. an
    empty ``SoT/`` survives a failed rebuild) but the dataset files are gone — in
    which case resolvability/latency must SKIP, not fail with a 0.0 score.
    """
    return _BUNDLE_DATASETS.is_dir() and any(_BUNDLE_DATASETS.glob("*.jsonl"))


_BUNDLE_SKIP = pytest.mark.skipif(
    not _bundle_has_datasets(),
    reason="Published dataset bundle absent/empty "
    "(output/Indo-VAP/llm_source/dataset_schema/files has no *.jsonl) — skip",
)


class TestResolvabilityAndLatency:
    @_BUNDLE_SKIP
    def test_resolvability_returns_results(self) -> None:
        from scripts.eval.retrieval_eval import _resolve_question

        results = [_resolve_question(q) for q in EVAL_QUESTIONS]
        assert len(results) == len(EVAL_QUESTIONS)
        for r in results:
            assert "overall_score" in r
            assert 0.0 <= r["overall_score"] <= 1.0

    @_BUNDLE_SKIP
    def test_resolvability_statistical_above_threshold(self) -> None:
        from scripts.eval.retrieval_eval import _resolve_question

        results = [_resolve_question(q) for q in EVAL_QUESTIONS if q.kind == "statistical"]
        scores = [r["overall_score"] for r in results]
        mean = sum(scores) / len(scores)
        assert mean >= 0.80, f"Statistical resolvability mean {mean:.4f} is below 0.80 threshold"

    @_BUNDLE_SKIP
    def test_resolvability_definitional_above_threshold(self) -> None:
        from scripts.eval.retrieval_eval import _resolve_question

        results = [_resolve_question(q) for q in EVAL_QUESTIONS if q.kind == "definitional"]
        scores = [r["overall_score"] for r in results]
        mean = sum(scores) / len(scores)
        assert mean >= 0.70, f"Definitional resolvability mean {mean:.4f} is below 0.70 threshold"

    @_BUNDLE_SKIP
    def test_direct_read_latency_runs_and_within_bound(self) -> None:
        from scripts.eval.retrieval_eval import _bench_direct_read

        result = _bench_direct_read()
        assert result.get("p50_s") is not None, "Direct-read benchmark did not return p50"
        assert result.get("p95_s") is not None, "Direct-read benchmark did not return p95"
        # Generous bound: must complete within 2 seconds p95
        assert result["p95_s"] < 2.0, f"Direct-read p95 {result['p95_s']:.4f}s exceeds 2.0s bound"

    def test_simulated_rag_latency_runs(self) -> None:
        """Simulated RAG benchmark runs regardless of bundle presence."""
        pytest.importorskip("numpy", reason="numpy required for RAG benchmark")
        from scripts.eval.retrieval_eval import _bench_simulated_rag

        result = _bench_simulated_rag()
        # Must succeed and return numeric values
        assert result.get("index_build_s") is not None
        assert result.get("per_query_p50_s") is not None
        assert result.get("per_query_p95_s") is not None
        # Sanity: index build should be positive time
        assert result["index_build_s"] > 0
