"""Tests for scripts/ai_assistant/agent_tools.py — tool registry and zone enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

langchain = pytest.importorskip("langchain_core", reason="langchain_core required")

from scripts.ai_assistant.agent_tools import ALL_TOOLS  # noqa: E402


class TestToolRegistry:
    def test_all_tools_is_list(self) -> None:
        assert isinstance(ALL_TOOLS, list)
        assert len(ALL_TOOLS) == 11

    def test_tools_have_names(self) -> None:
        for tool in ALL_TOOLS:
            assert hasattr(tool, "name")
            assert isinstance(tool.name, str)

    def test_tool_names_unique(self) -> None:
        names = [t.name for t in ALL_TOOLS]
        assert len(names) == len(set(names))

    def test_expected_tool_names(self) -> None:
        names = {t.name for t in ALL_TOOLS}
        expected = {
            "list_llm_source",
            "search_llm_source",
            "read_llm_source_file",
            "search_variables",
            "query_dataset",
            "list_available_datasets",
            "get_dataset_stats",
            "run_python_analysis",
            "answer_catalog_question",
            "cite_source",
            "get_study_variable_map",
        }
        assert expected == names


class TestGetDatasetStats:
    def test_returns_string(self, monkeypatch_config: Path) -> None:
        from scripts.ai_assistant.agent_tools import get_dataset_stats

        result = get_dataset_stats.invoke({})
        assert isinstance(result, str)


class TestSearchVariables:
    def test_returns_string(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import search_variables
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()
        (config.TRIO_DATASETS_DIR / "1A_ICScreening.jsonl").write_text(
            json.dumps({"AGE": 30}) + "\n"
        )

        result = search_variables.invoke({"query": "age"})
        assert isinstance(result, str)

    def test_falls_back_to_published_dataset_columns(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import search_variables
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()
        (config.TRIO_DATASETS_DIR / "1A_ICScreening.jsonl").write_text(
            json.dumps({"IS_ELIGIBLE": "Yes", "IS_VISDAT": "2014-07-02"}) + "\n"
        )

        payload = json.loads(search_variables.invoke({"query": "IS_ELIGIBLE"}))

        assert payload[0]["variable_name"] == "IS_ELIGIBLE"
        assert payload[0]["dataset"] == "1A_ICScreening"
        assert payload[0]["source"] == "dataset_schema"


class TestAnswerCatalogQuestion:
    def test_exact_variable_id_beats_common_question_words(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        import config
        from scripts.ai_assistant.agent_tools import answer_catalog_question

        llm_source = tmp_path / "output" / "Indo-VAP" / "llm_source"
        source_truth = llm_source / "source_truth"
        dataset_schema = llm_source / "dataset_schema"
        agent_dir = tmp_path / "output" / "Indo-VAP" / "agent"
        source_truth.mkdir(parents=True)
        dataset_schema.mkdir(parents=True)
        agent_dir.mkdir(parents=True)
        monkeypatch.setattr(config, "REPO_ROOT", tmp_path, raising=False)
        monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", llm_source)
        monkeypatch.setattr(config, "TRIO_BUNDLE_DIR", llm_source)
        monkeypatch.setattr(config, "AGENT_STATE_DIR", agent_dir)

        (source_truth / "14_CaseControl_policy.lean.yaml").write_text(
            """
study: Indo-VAP
form:
  number: "14"
  title: Case Control
sections:
  main: Main
variables:
  CC_WTRSRC:
    section: main
    pdf_question: What is the main source of water?
    widget: text
    type: text
""".lstrip(),
            encoding="utf-8",
        )
        (source_truth / "6_HIV_policy.lean.yaml").write_text(
            """
study: Indo-VAP
form:
  number: "6"
  title: HIV
sections:
  main: Main
variables:
  HIV_HIV:
    section: main
    pdf_question: HIV test result
    type: code
    description: HIV test result code
    options: [Positive, Negative]
""".lstrip(),
            encoding="utf-8",
        )
        (dataset_schema / "6_HIV_schema.json").write_text(
            json.dumps(
                {
                    "study": "Indo-VAP",
                    "form": "6_HIV",
                    "source_dataset": "data/raw/Indo-VAP/datasets/6_HIV.xlsx",
                    "jsonl_file": "tmp/6_HIV.jsonl",
                    "record_count": 1401,
                    "columns": [
                        {
                            "name": "HIV_HIV",
                            "source_order": 6,
                            "phi_action": "retain",
                            "published_in_jsonl": True,
                            "llm_status": "available",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        payload = json.loads(answer_catalog_question.invoke({"question": "What is HIV_HIV?"}))

        assert payload["variable_ids"] == ["HIV_HIV"]
        answer = json.loads(payload["answer"])
        assert answer["metadata"]["pdf"]["question"] == "HIV test result"
        assert "source_order" not in answer["metadata"]["dataset"]
        assert answer["metadata"]["dataset"]["phi_action"] == "retain"

    def test_exact_variable_id_uses_new_sot_pair_layout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        import config
        from scripts.ai_assistant.agent_tools import answer_catalog_question

        pair_dir = tmp_path / "output" / "Indo-VAP" / "llm_source" / "SoT" / "6_HIV"
        policy_dir = pair_dir / "pdf"
        dataset_dir = pair_dir / "dataset"
        agent_dir = tmp_path / "output" / "Indo-VAP" / "agent"
        policy_dir.mkdir(parents=True)
        dataset_dir.mkdir(parents=True)
        agent_dir.mkdir(parents=True)
        monkeypatch.setattr(config, "REPO_ROOT", tmp_path, raising=False)
        monkeypatch.setattr(config, "AGENT_STATE_DIR", agent_dir)

        (policy_dir / "6_HIV_policy.yaml").write_text(
            """
study: Indo-VAP
form:
  number: "6"
  title: HIV
sections:
  main: Main
variables:
  HIV_CD4DAT:
    section: main
    pdf_question: 3a. CD4 Test Date
    type: date
    description: CD4 test date
""".lstrip(),
            encoding="utf-8",
        )
        (dataset_dir / "6_HIV_schema.json").write_text(
            json.dumps(
                {
                    "study": "Indo-VAP",
                    "form": "6_HIV",
                    "source_dataset": "data/raw/Indo-VAP/datasets/6_HIV.xlsx",
                    "record_count": 1401,
                    "columns": [
                        {
                            "name": "HIV_CD4DAT",
                            "source_order": 12,
                            "phi_action": "jitter_date",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        payload = json.loads(answer_catalog_question.invoke({"question": "What is HIV_CD4DAT?"}))
        answer = json.loads(payload["answer"])

        assert answer["metadata"]["pdf"]["question"] == "3a. CD4 Test Date"
        assert answer["metadata"]["dataset"] == {"phi_action": "jitter_date"}


class TestQueryDataset:
    def test_row_sample_redacts_dates_instead_of_blocking_tool(
        self, monkeypatch_config: Path
    ) -> None:
        import config
        from scripts.ai_assistant.agent_tools import query_dataset

        rows = [
            {"VISDAT": "2014-07-02", "RESULT": "Yes"},
            {"VISDAT": "2014-07-03", "RESULT": "No"},
        ]
        (config.TRIO_DATASETS_DIR / "Visits.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows)
        )

        raw = query_dataset.invoke({"dataset_name": "Visits", "limit": 2})
        payload = json.loads(raw)

        assert payload["records"][0]["VISDAT"] == "<DATE_SHIFTED>"
        assert payload["date_values_redacted"] == ["VISDAT"]


# ---------------------------------------------------------------------------
# Sandbox security regression tests
# ---------------------------------------------------------------------------


class TestSafeImportCheck:
    """AST-level blocking of dangerous imports and dunder attributes."""

    def test_blocks_os_import(self) -> None:
        from scripts.ai_assistant.sandbox.runner import SandboxRejectionError, _ast_pre_check

        with pytest.raises(SandboxRejectionError):
            _ast_pre_check("import os")

    def test_blocks_subprocess_import(self) -> None:
        from scripts.ai_assistant.sandbox.runner import SandboxRejectionError, _ast_pre_check

        with pytest.raises(SandboxRejectionError):
            _ast_pre_check("import subprocess")

    def test_allows_pandas(self) -> None:
        from scripts.ai_assistant.sandbox.runner import _ast_pre_check

        # Should not raise SandboxRejectionError
        _ast_pre_check("import pandas as pd")

    def test_blocks_subclasses_attribute(self) -> None:
        from scripts.ai_assistant.sandbox.runner import SandboxRejectionError, _ast_pre_check

        with pytest.raises(SandboxRejectionError) as exc_info:
            _ast_pre_check("x = ().__class__.__bases__[0].__subclasses__()")
        assert "__subclasses__" in str(exc_info.value)

    def test_blocks_globals_attribute(self) -> None:
        from scripts.ai_assistant.sandbox.runner import SandboxRejectionError, _ast_pre_check

        with pytest.raises(SandboxRejectionError) as exc_info:
            _ast_pre_check("g = fn.__globals__")
        assert "__globals__" in str(exc_info.value)

    def test_blocks_class_attribute(self) -> None:
        from scripts.ai_assistant.sandbox.runner import SandboxRejectionError, _ast_pre_check

        with pytest.raises(SandboxRejectionError) as exc_info:
            _ast_pre_check("c = x.__class__")
        assert "__class__" in str(exc_info.value)


class TestSandboxRuntimeGuards:
    """Runtime guards (getattr, vars) block escape vectors."""

    @pytest.fixture(autouse=True)
    def _isolate_agent_output(self, monkeypatch_config: Path) -> None:
        """Keep persisted sandbox snippets out of real output during tests."""

    def test_getattr_blocks_globals(self) -> None:
        from scripts.ai_assistant.agent_tools import run_python_analysis

        code = 'import json\ng = getattr(json.dumps, chr(95)*2+"globals"+chr(95)*2)'
        result = run_python_analysis.invoke(code)
        assert "not allowed" in result.lower() or "error" in result.lower()
        assert "ESCAPED" not in result

    def test_getattr_blocks_subclasses(self) -> None:
        from scripts.ai_assistant.agent_tools import run_python_analysis

        code = "x = getattr(type, '__subclasses__')"
        result = run_python_analysis.invoke(code)
        assert "not allowed" in result.lower() or "error" in result.lower()

    def test_vars_strips_builtins(self) -> None:
        from scripts.ai_assistant.agent_tools import run_python_analysis

        code = (
            "import json\nv = vars(json)\n"
            "key = chr(95)*2 + 'builtins' + chr(95)*2\n"
            "print('has_key:', key in v)"
        )
        result = run_python_analysis.invoke(code)
        assert "has_key: False" in result

    def test_getattr_allows_normal_attributes(self) -> None:
        from scripts.ai_assistant.agent_tools import run_python_analysis

        code = (
            "import pandas as pd\n"
            "df = pd.DataFrame({'x': [1,2,3]})\n"
            "shape = getattr(df, 'shape')\n"
            "print(f'rows={shape[0]} cols={shape[1]}')"
        )
        result = run_python_analysis.invoke(code)
        assert "rows=3 cols=1" in result

    def test_legitimate_analysis_works(self) -> None:
        from scripts.ai_assistant.agent_tools import run_python_analysis

        code = "import numpy as np\ndata = [1, 2, 3, 4, 5]\nprint('mean:', np.mean(data))\n"
        result = run_python_analysis.invoke(code)
        assert "mean: 3.0" in result


# ---------------------------------------------------------------------------
# Phase 5b Task 4c — dead variables.json pipeline must be removed
# ---------------------------------------------------------------------------


def test_load_variables_json_removed() -> None:
    """_load_variables_json must not exist after Phase 5b."""
    from scripts.ai_assistant import agent_tools

    assert not hasattr(agent_tools, "_load_variables_json")


def test_variables_json_path_removed() -> None:
    """VARIABLES_JSON_PATH config constant must be removed."""
    import config

    assert not hasattr(config, "VARIABLES_JSON_PATH")


def test_build_variables_reference_module_removed() -> None:
    """The build_variables_reference module must be deleted."""
    with pytest.raises(ImportError):
        from scripts.extraction import build_variables_reference  # noqa: F401


# ---------------------------------------------------------------------------
# GAP-1: _gate_figure_path suppresses figures whose content contains a
#         blocking PHI pattern; clean aggregate figures pass through.
# ---------------------------------------------------------------------------


class TestGateFigurePath:
    """GAP-1 — figure-level PHI gate enforced before path is emitted."""

    def test_suppresses_figure_containing_email(self, tmp_path: Path) -> None:
        """A plotly JSON that embeds an email address is gated (suppressed)."""
        from scripts.ai_assistant.agent_tools import _gate_figure_path

        phi_figure = tmp_path / "phi_chart.json"
        phi_figure.write_text(
            json.dumps({"data": [{"x": ["a@b.com", "c@d.org"], "y": [1, 2], "type": "bar"}]}),
            encoding="utf-8",
        )

        assert _gate_figure_path(phi_figure) is False

    def test_clean_aggregate_figure_passes(self, tmp_path: Path) -> None:
        """A plotly JSON with only aggregate numeric data is NOT suppressed."""
        from scripts.ai_assistant.agent_tools import _gate_figure_path

        clean_figure = tmp_path / "clean_chart.json"
        clean_figure.write_text(
            json.dumps(
                {
                    "data": [{"x": ["18-34", "35-54", "55+"], "y": [12, 25, 8], "type": "bar"}],
                    "layout": {"title": "Age distribution"},
                }
            ),
            encoding="utf-8",
        )

        assert _gate_figure_path(clean_figure) is True

    def test_format_sandbox_result_omits_phi_figure_path(
        self, tmp_path: Path, monkeypatch_config: Path
    ) -> None:
        """_format_sandbox_result_for_agent must NOT include the path of a
        suppressed (PHI-containing) figure and MUST include a redaction note."""
        from unittest.mock import MagicMock

        from scripts.ai_assistant.agent_tools import _format_sandbox_result_for_agent

        # Build a fake SandboxResult with one PHI figure
        phi_figure = tmp_path / "agent" / "phi_plot.json"
        phi_figure.parent.mkdir(parents=True, exist_ok=True)
        phi_figure.write_text(
            json.dumps({"data": [{"x": ["user@example.com"], "y": [1], "type": "scatter"}]}),
            encoding="utf-8",
        )

        result = MagicMock()
        result.exit_code = 0
        result.timed_out = False
        result.oom_killed = False
        result.stdout = "some output"
        result.stderr = ""
        result.figure_paths = [phi_figure]
        result.code_paths = []

        formatted = _format_sandbox_result_for_agent(result)

        # The suppressed path must NOT appear
        assert str(phi_figure) not in formatted
        # A redaction/suppression note must be present
        assert "suppressed" in formatted.lower()

    def test_format_sandbox_result_includes_clean_plotly_path(
        self, tmp_path: Path, monkeypatch_config: Path
    ) -> None:
        """A clean plotly figure path IS included in the formatted result."""
        from unittest.mock import MagicMock

        from scripts.ai_assistant.agent_tools import _format_sandbox_result_for_agent

        clean_figure = tmp_path / "agent" / "clean_plot.json"
        clean_figure.parent.mkdir(parents=True, exist_ok=True)
        clean_figure.write_text(
            json.dumps({"data": [{"x": ["18-34", "35+"], "y": [10, 20], "type": "bar"}]}),
            encoding="utf-8",
        )

        result = MagicMock()
        result.exit_code = 0
        result.timed_out = False
        result.oom_killed = False
        result.stdout = ""
        result.stderr = ""
        result.figure_paths = [clean_figure]
        result.code_paths = []

        formatted = _format_sandbox_result_for_agent(result)

        assert str(clean_figure) in formatted


# ---------------------------------------------------------------------------
# GAP-6: query_dataset no-QI branch — rows returned (date-redacted),
#         kanon_note is a non-null string, kanon_violation is null.
# ---------------------------------------------------------------------------


class TestQueryDatasetNoQIBranch:
    """GAP-6 — no-quasi-identifier datasets return rows with advisory note."""

    def test_no_qi_columns_returns_rows_with_kanon_note(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 2-row dataset with only VISDAT + RESULT (no QI column) must:
        1. Return records (not suppress to empty).
        2. Redact date values to '<DATE_SHIFTED>'.
        3. Set kanon_note to a non-null string.
        4. Leave kanon_violation as null.
        """
        import config
        import scripts.ai_assistant.agent_tools as ag

        ds_dir = tmp_path / "trio_bundle" / "datasets"
        ds_dir.mkdir(parents=True)
        rows = [
            {"VISDAT": "2014-07-02", "RESULT": "Pos"},
            {"VISDAT": "2014-08-10", "RESULT": "Neg"},
        ]
        (ds_dir / "NoQI.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

        monkeypatch.setattr(config, "TRIO_DATASETS_DIR", ds_dir)
        monkeypatch.setattr(ag, "assert_output_zone", lambda _p: None)
        monkeypatch.setattr(ag, "validate_agent_read", lambda p: p)

        from scripts.ai_assistant.agent_tools import query_dataset
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()

        payload = json.loads(query_dataset.invoke({"dataset_name": "NoQI", "limit": 10}))

        # Records must be present
        assert len(payload["records"]) == 2, "expected rows to be returned for no-QI dataset"
        # Date values must be redacted
        for rec in payload["records"]:
            assert rec["VISDAT"] == "<DATE_SHIFTED>", "date field not redacted"
        # kanon_violation must be null (not suppressed)
        assert payload["kanon_violation"] is None
        # kanon_note must be a non-empty string
        assert isinstance(payload["kanon_note"], str)
        assert len(payload["kanon_note"]) > 0
