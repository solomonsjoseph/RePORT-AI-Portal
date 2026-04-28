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
        assert len(ALL_TOOLS) == 12

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
            "find_variable_candidates",
            "get_variable_details",
            "list_forms",
            "get_form_variables",
            "query_dataset",
            "get_dataset_stats",
            "get_study_overview",
            "run_python_analysis",
            "cross_reference_variables",
            "run_study_analysis",
            "search_pdf_context",
        }
        assert expected == names


class TestGetStudyOverview:
    def test_returns_string(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import get_study_overview

        vf = config.TRIO_BUNDLE_DIR / "variables.json"
        vf.write_text(json.dumps([{"variable_name": "AGE", "description": "Age"}]))

        result = get_study_overview.invoke({})
        assert isinstance(result, str)


class TestGetDatasetStats:
    def test_returns_string(self, monkeypatch_config: Path) -> None:
        from scripts.ai_assistant.agent_tools import get_dataset_stats

        result = get_dataset_stats.invoke({})
        assert isinstance(result, str)


class TestSearchVariables:
    def test_returns_string(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import search_variables

        vf = config.TRIO_BUNDLE_DIR / "variables.json"
        vf.write_text(json.dumps([{"variable_name": "AGE", "description": "Age at enrollment"}]))

        result = search_variables.invoke({"query": "age"})
        assert isinstance(result, str)


class TestFindVariableCandidates:
    """Fuzzy top-k disambiguator — must always return <= k ranked candidates."""

    def _write_fixture(self, path: Path) -> None:
        fixture = [
            {
                "variable_name": "AE_AGE",
                "form_id": "95",
                "form_name": "Serious Adverse Event Form",
                "section": "Demographics",
                "description": "Age at time of event (in Years)",
                "data_type": "number",
                "coded_options": "",
            },
            {
                "variable_name": "AGE_ENROLL",
                "form_id": "2A",
                "form_name": "Index Case Clinical/Demographic Form",
                "section": "Enrollment",
                "description": "Age at enrollment (years)",
                "data_type": "number",
                "coded_options": "",
            },
            {
                "variable_name": "BIDIYN",
                "form_id": "2A",
                "form_name": "Index Case Clinical/Demographic Form",
                "section": "Smoking",
                "description": "Smoked regularly: beedis, hand-rolled or unfiltered cigarettes",
                "data_type": "enum",
                "coded_options": "Y=Yes;N=No",
            },
        ]
        path.write_text(json.dumps(fixture))

    def test_returns_valid_json(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import find_variable_candidates

        self._write_fixture(config.TRIO_BUNDLE_DIR / "variables.json")
        raw = find_variable_candidates.invoke({"description": "age at event", "k": 3})
        payload = json.loads(raw)
        assert payload["count"] >= 1
        assert payload["candidates"][0]["rank"] == 1
        assert 0.0 <= payload["candidates"][0]["confidence"] <= 1.0

    def test_top_hit_is_the_right_one(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import find_variable_candidates

        self._write_fixture(config.TRIO_BUNDLE_DIR / "variables.json")
        raw = find_variable_candidates.invoke(
            {"description": "age at time of serious adverse event", "k": 3}
        )
        payload = json.loads(raw)
        assert payload["candidates"][0]["variable_name"] == "AE_AGE"
        assert payload["candidates"][0]["form_id"] == "95"

    def test_clamps_k_to_bounds(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import find_variable_candidates

        self._write_fixture(config.TRIO_BUNDLE_DIR / "variables.json")
        # k=0 should behave like k=1
        raw = find_variable_candidates.invoke({"description": "age", "k": 0})
        assert json.loads(raw)["count"] == 1

    def test_includes_form_context(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import find_variable_candidates

        self._write_fixture(config.TRIO_BUNDLE_DIR / "variables.json")
        raw = find_variable_candidates.invoke({"description": "beedis smoking", "k": 3})
        top = json.loads(raw)["candidates"][0]
        assert top["variable_name"] == "BIDIYN"
        assert top["form_name"] == "Index Case Clinical/Demographic Form"
        assert top["coded_options"]  # surfaced for enum disambiguation

    def test_empty_reference(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import find_variable_candidates

        (config.TRIO_BUNDLE_DIR / "variables.json").write_text("[]")
        raw = find_variable_candidates.invoke({"description": "anything"})
        assert "No variables reference" in raw


class TestSearchPdfContext:
    """Keyword search over extracted CRF form text."""

    def _write_pdf_fixture(self, pdf_dir: Path) -> None:
        pdf_dir.mkdir(parents=True, exist_ok=True)
        (pdf_dir / "17 Eligibility.json").write_text(
            json.dumps(
                {
                    "form_name": "Eligibility Confirmation Form - Cohort A",
                    "source_pdf": "Form 17.pdf",
                    "version": "v1.0",
                    "summary": "Used to confirm final Cohort A eligibility "
                    "within 6-month follow-up based on enrollment criteria.",
                    "variables": {
                        "EC_ELIG2A1": {
                            "description": "Does the participant have "
                            "culture-confirmed pulmonary TB?",
                            "section_context": "Final Cohort A eligibility "
                            "is confirmed within the 6-month follow-up period.",
                        }
                    },
                }
            )
        )
        (pdf_dir / "1B HHC.json").write_text(
            json.dumps(
                {
                    "form_name": "Household Contact Screening Form",
                    "source_pdf": "Form 1B.pdf",
                    "summary": "Screens household contacts of index TB cases "
                    "living in the same dwelling for at least 3 months.",
                    "variables": {
                        "BINCL01": {
                            "description": "Past 3 months lived in same home",
                            "section_context": "",
                        }
                    },
                }
            )
        )

    def test_returns_ranked_snippets(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import search_pdf_context
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()
        self._write_pdf_fixture(config.PDF_EXTRACTIONS_DIR)

        raw = search_pdf_context.invoke({"query": "Cohort A eligibility", "k": 3})
        payload = json.loads(raw)
        assert payload["count"] >= 1
        assert payload["snippets"][0]["rank"] == 1
        assert 0.0 <= payload["snippets"][0]["score"] <= 1.0

    def test_cites_form_name(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import search_pdf_context
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()
        self._write_pdf_fixture(config.PDF_EXTRACTIONS_DIR)

        raw = search_pdf_context.invoke({"query": "household contact same dwelling"})
        top = json.loads(raw)["snippets"][0]
        assert "Household Contact" in top["form_name"]
        assert top["source_pdf"] == "Form 1B.pdf"

    def test_low_confidence_flag(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import search_pdf_context
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()
        self._write_pdf_fixture(config.PDF_EXTRACTIONS_DIR)

        raw = search_pdf_context.invoke({"query": "participant enrollment"})
        payload = json.loads(raw)
        # low-confidence flag set on weak matches
        assert "low_confidence" in payload

    def test_no_pdfs_directory(self, monkeypatch_config: Path) -> None:
        from scripts.ai_assistant.agent_tools import search_pdf_context
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()
        # monkeypatch_config points everything at tmp_path, so pdfs dir is empty
        raw = search_pdf_context.invoke({"query": "anything"})
        assert "No extracted PDF context" in raw or '"count": 0' in raw

    def test_no_matches(self, monkeypatch_config: Path) -> None:
        import config
        from scripts.ai_assistant.agent_tools import search_pdf_context
        from scripts.ai_assistant.tool_cache import tool_cache

        tool_cache.clear()
        self._write_pdf_fixture(config.PDF_EXTRACTIONS_DIR)

        raw = search_pdf_context.invoke({"query": "quantum chromodynamics"})
        payload = json.loads(raw)
        assert payload["count"] == 0


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
