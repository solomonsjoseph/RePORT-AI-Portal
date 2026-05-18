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
        assert len(ALL_TOOLS) == 10

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
            "search_variables",
            "query_dataset",
            "list_available_datasets",
            "get_dataset_stats",
            "run_python_analysis",
            "run_study_analysis",
            "answer_catalog_question",
            "produce_evidence_report",
            "produce_custom_evidence_report",
            "cite_source",
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
        agent_dir = tmp_path / "output" / "Indo-VAP" / "agent"
        source_truth.mkdir(parents=True)
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
    widget: radio
    type: code
    options: [Positive, Negative]
""".lstrip(),
            encoding="utf-8",
        )

        payload = json.loads(answer_catalog_question.invoke({"question": "What is HIV_HIV?"}))

        assert payload["variable_ids"] == ["HIV_HIV"]


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
        from scripts.ai_assistant.agent_tools import _safe_import_check

        assert _safe_import_check("import os") is not None

    def test_blocks_subprocess_import(self) -> None:
        from scripts.ai_assistant.agent_tools import _safe_import_check

        assert _safe_import_check("import subprocess") is not None

    def test_allows_pandas(self) -> None:
        from scripts.ai_assistant.agent_tools import _safe_import_check

        assert _safe_import_check("import pandas as pd") is None

    def test_blocks_subclasses_attribute(self) -> None:
        from scripts.ai_assistant.agent_tools import _safe_import_check

        err = _safe_import_check("x = ().__class__.__bases__[0].__subclasses__()")
        assert err is not None
        assert "__subclasses__" in err

    def test_blocks_globals_attribute(self) -> None:
        from scripts.ai_assistant.agent_tools import _safe_import_check

        err = _safe_import_check("g = fn.__globals__")
        assert err is not None
        assert "__globals__" in err

    def test_blocks_class_attribute(self) -> None:
        from scripts.ai_assistant.agent_tools import _safe_import_check

        err = _safe_import_check("c = x.__class__")
        assert err is not None
        assert "__class__" in err


class TestSandboxRuntimeGuards:
    """Runtime guards (getattr, vars) block escape vectors."""

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
