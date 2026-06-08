"""CI-safe smoke tests for the Track B cloud eval runner.

All tests monkeypatch config to fake-local so no network call is made.
The test verifies the structural contract (per-question dict fields,
latency floats, tool lists) rather than model quality.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Skip the whole module when langchain_core is absent
langchain_core = pytest.importorskip("langchain_core", reason="langchain_core required")

from scripts.eval.eval_questions import EVAL_QUESTIONS  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Probe IDs used by the smoke test — pick 3 that exercise different
# _FakeLocalChatModel routing branches (list, stats, catalog).
_SMOKE_IDS = ("Q-D1", "Q-D2", "Q-A1")


def _smoke_questions() -> list[Any]:
    """Return 3 questions whose IDs are in _SMOKE_IDS."""
    return [q for q in EVAL_QUESTIONS if q.id in _SMOKE_IDS]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_local_env(monkeypatch: pytest.MonkeyPatch, monkeypatch_config: Path) -> None:
    """Patch env + config to fake-local; reset agent singleton."""
    import config as _config

    monkeypatch.setenv("REPORTAL_TEST_FAKE_LLM", "1")
    monkeypatch.setattr(_config, "LLM_PROVIDER", "fake-local", raising=False)
    monkeypatch.setattr(_config, "LLM_MODEL", "fake-local", raising=False)

    from scripts.ai_assistant import agent_graph as ag

    monkeypatch.setattr(ag, "_agent", None)
    monkeypatch.setattr(ag, "_checkpointer", None)

    yield

    # Ensure the agent is torn down after the test
    monkeypatch.setattr(ag, "_agent", None)
    monkeypatch.setattr(ag, "_checkpointer", None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCloudEvalSmoke:
    """Structural smoke tests — no network, no API key, fake-local only."""

    def test_run_returns_aggregate_and_per_question(
        self, fake_local_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_cloud_eval returns the expected top-level structure."""
        from scripts.eval import cloud_eval

        # Redirect output directory to tmp_path so we don't write into docs/
        monkeypatch.setattr(cloud_eval, "DOCS_EVAL_DIR", tmp_path)

        result = cloud_eval.run_cloud_eval(
            questions=_smoke_questions(),
            thread_prefix="ci-smoke",
        )

        assert "aggregate" in result, "result must have 'aggregate' key"
        assert "per_question" in result, "result must have 'per_question' key"

    def test_per_question_structure(
        self, fake_local_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each per-question entry has the expected fields with correct types."""
        from scripts.eval import cloud_eval

        monkeypatch.setattr(cloud_eval, "DOCS_EVAL_DIR", tmp_path)

        result = cloud_eval.run_cloud_eval(
            questions=_smoke_questions(),
            thread_prefix="ci-struct",
        )

        per_q = result["per_question"]
        assert len(per_q) == len(_smoke_questions()), "wrong number of per-question records"

        for rec in per_q:
            assert isinstance(rec["id"], str), f"id must be str: {rec}"
            assert isinstance(rec["kind"], str), f"kind must be str: {rec}"
            assert isinstance(rec["latency_s"], float), f"latency_s must be float: {rec}"
            assert rec["latency_s"] >= 0.0, f"latency_s must be non-negative: {rec}"
            assert isinstance(rec["tools_called"], list), f"tools_called must be list: {rec}"
            assert isinstance(rec["answered"], bool), f"answered must be bool: {rec}"
            assert isinstance(rec["tools_used_ok"], bool), f"tools_used_ok must be bool: {rec}"
            # keyword_overlap is float or None
            assert rec["keyword_overlap"] is None or isinstance(rec["keyword_overlap"], float), (
                f"keyword_overlap must be float or None: {rec}"
            )

    def test_aggregate_latency_fields_are_floats(
        self, fake_local_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Aggregate must contain p50/p95 latency floats."""
        from scripts.eval import cloud_eval

        monkeypatch.setattr(cloud_eval, "DOCS_EVAL_DIR", tmp_path)

        result = cloud_eval.run_cloud_eval(
            questions=_smoke_questions(),
            thread_prefix="ci-latency",
        )

        agg = result["aggregate"]
        assert isinstance(agg["latency_p50_s"], float), "latency_p50_s must be float"
        assert isinstance(agg["latency_p95_s"], float), "latency_p95_s must be float"
        assert agg["latency_p50_s"] >= 0.0
        assert agg["latency_p95_s"] >= 0.0

    def test_aggregate_run_type_is_smoke(
        self, fake_local_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Smoke run must be labelled 'smoke-fake-local'."""
        from scripts.eval import cloud_eval

        monkeypatch.setattr(cloud_eval, "DOCS_EVAL_DIR", tmp_path)

        result = cloud_eval.run_cloud_eval(
            questions=_smoke_questions()[:1],
            thread_prefix="ci-runtype",
        )

        assert result["aggregate"]["run_type"] == "smoke-fake-local", (
            "run_type must be 'smoke-fake-local' for fake-local provider"
        )

    def test_limit_caps_questions(
        self, fake_local_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--limit N parameter restricts the number of evaluated questions."""
        from scripts.eval import cloud_eval

        monkeypatch.setattr(cloud_eval, "DOCS_EVAL_DIR", tmp_path)

        result = cloud_eval.run_cloud_eval(
            limit=2,
            thread_prefix="ci-limit",
        )

        assert len(result["per_question"]) == 2, (
            f"Expected 2 questions with limit=2, got {len(result['per_question'])}"
        )

    def test_output_files_written(
        self, fake_local_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_cloud_eval must write both .json and .md result files."""
        from scripts.eval import cloud_eval

        monkeypatch.setattr(cloud_eval, "DOCS_EVAL_DIR", tmp_path)

        cloud_eval.run_cloud_eval(
            questions=_smoke_questions()[:1],
            thread_prefix="ci-files",
        )

        assert (tmp_path / "cloud_eval_results.json").exists(), "JSON result file not written"
        assert (tmp_path / "cloud_eval_results.md").exists(), "Markdown result file not written"

    def test_tools_called_are_strings(
        self, fake_local_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every entry in tools_called must be a str (ToolMessage.name)."""
        from scripts.eval import cloud_eval

        monkeypatch.setattr(cloud_eval, "DOCS_EVAL_DIR", tmp_path)

        result = cloud_eval.run_cloud_eval(
            questions=_smoke_questions(),
            thread_prefix="ci-toolnames",
        )

        for rec in result["per_question"]:
            for name in rec["tools_called"]:
                assert isinstance(name, str), f"tool name must be str, got {type(name)}: {name}"


class TestPreflight:
    """Unit tests for the preflight key-check logic."""

    def test_fake_local_always_passes(self) -> None:
        from scripts.eval.cloud_eval import _preflight_check

        assert _preflight_check("fake-local") is True

    def test_ollama_passes_without_key(self) -> None:
        """Ollama is not in _KEY_REQUIRED_PROVIDERS — should pass."""
        from scripts.eval.cloud_eval import _preflight_check

        assert _preflight_check("ollama") is True

    def test_cloud_provider_fails_without_key(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """anthropic provider with no key in KeyStore must return False."""
        from scripts.ai_assistant.keystore import KeyStore

        # Use a fresh keystore with no keys
        fresh_ks = KeyStore()

        from scripts.eval import cloud_eval

        monkeypatch.setattr(
            "scripts.eval.cloud_eval.__builtins__",
            __builtins__,
            raising=False,
        )

        # Patch get_keystore to return our empty keystore
        monkeypatch.setattr(
            "scripts.ai_assistant.keystore._PROCESS_KEYSTORE",
            fresh_ks,
        )

        # Also ensure the env var is not set (it would allow a bypass)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        result = cloud_eval._preflight_check("anthropic")
        captured = capsys.readouterr()

        assert result is False, "preflight must return False when cloud key is absent"
        # The message uses get_keystore().set(); check for the canonical form
        assert "get_keystore" in captured.out or "KeyStore" in captured.out, (
            "preflight message must mention the KeyStore API"
        )
        assert "ABORT" in captured.out, "preflight message must contain ABORT"


class TestPercentile:
    """Unit tests for the percentile helper."""

    def test_empty_list(self) -> None:
        from scripts.eval.cloud_eval import _percentile

        assert _percentile([], 50) == 0.0

    def test_single_value(self) -> None:
        from scripts.eval.cloud_eval import _percentile

        assert _percentile([1.5], 50) == 1.5
        assert _percentile([1.5], 95) == 1.5

    def test_p50_of_sorted_list(self) -> None:
        from scripts.eval.cloud_eval import _percentile

        values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        p50 = _percentile(values, 50)
        assert 0.4 <= p50 <= 0.6, f"p50 of 10 evenly-spaced values should be ~0.5, got {p50}"
