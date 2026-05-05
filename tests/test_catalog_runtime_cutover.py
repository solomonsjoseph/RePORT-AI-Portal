"""Tests for issue #79 — catalog-runtime feature flag cutover.

The runtime cutover wires the assistant's tool set + system prompt to a
catalog-aware path behind ``REPORTALIN_USE_CATALOG_RUNTIME``. The flag
must:

* leave existing behavior untouched when OFF (default);
* steer the LLM to the catalog tools and discourage the legacy
  ``StudyKnowledge`` path when ON;
* gate the analysis runner so deterministic analyses go through
  ``resolve_analysis_bindings`` + Dataset Schema validation;
* preserve the verbatim ``AUDIT_ONLY_NOTE`` boundary;
* not widen the file-access boundary;
* not add a hidden keyword router (per maintainer constraints carried
  over from HITL #83).

The tests are strictly *configuration-level*: they pin the wiring that
``agent_graph.get_agent`` performs (tools + prompt selection) and the
boundary refusals exposed by the catalog retrieval and analysis-binding
modules. They do NOT exercise a live LLM — that is intentional: the
maintainer's bar is "the LLM tool-choice path covers it", which is a
configuration property of which tools and which prompt the model sees.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.source_truth.analysis_binding import (
    AnalysisBindingError,
    resolve_analysis_bindings,
)
from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import AUDIT_ONLY_NOTE, build_catalog_artifact
from scripts.source_truth.dataset_schema import build_dataset_schema
from scripts.source_truth.retrieval import SourceTruthRetriever

_RUNTIME_FLAG = "REPORTALIN_USE_CATALOG_RUNTIME"
_BINDING_FLAG = "REPORTALIN_USE_CATALOG_BINDING"
_LEGACY_FLAG = "REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE"
_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Source-truth fixture covering all four boundary cases.
# ---------------------------------------------------------------------------


def _source_truth_artifact() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "sheets": [
            {
                "sheet": "_6_HIV",
                "columns": ["HIV_HIV", "SUBJID", "HIV_SIGN"],
            }
        ],
    }
    pdf_extraction = {
        "real_annotation_variables": [
            "HIV_HIV",
            "SUBJID",
            "HIV_SIGN",
            "HIV_FORM_INSTRUCTION",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "HIV_HIV",
                    "SUBJID",
                    "HIV_SIGN",
                    "HIV_FORM_INSTRUCTION",
                ],
            }
        ],
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf",
        "fields": {
            "HIV_HIV": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
            },
            "SUBJID": {
                "action": "pseudonymize",
                "label": "SUBJ",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
                "sensitivity_flags": ["direct_identifier"],
            },
            "HIV_SIGN": {
                "action": "drop",
                "reason": "signature_field",
                "confidence": "high",
                "section": "completion",
                "pdf_annotation_status": "direct",
            },
            "HIV_FORM_INSTRUCTION": {
                "action": "keep",
                "reason": "pdf_only_instruction",
                "confidence": "high",
                "source_kind": "source_only",
                "dataset_present": False,
                "pdf_annotation_status": "direct",
            },
        },
    }
    return build_source_truth_artifact(column_inventory, pdf_extraction, field_policy)


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


class TestRuntimeFlag:
    """``REPORTALIN_USE_CATALOG_RUNTIME`` is a top-level runtime flag.

    It must be readable from ``scripts.ai_assistant.agent_graph`` and
    must imply ``REPORTALIN_USE_CATALOG_BINDING`` (so the analysis path
    refuses the legacy ``StudyKnowledge`` runner) without forcing the
    operator to set both flags by hand.
    """

    def test_runtime_flag_default_on_after_hard_cutover(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After issue #81 the catalog runtime is the default. The
        explicit ``REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE`` override
        rolls it back; otherwise the runtime is on.
        """
        monkeypatch.delenv(_RUNTIME_FLAG, raising=False)
        monkeypatch.delenv(_BINDING_FLAG, raising=False)
        monkeypatch.delenv(_LEGACY_FLAG, raising=False)
        from scripts.ai_assistant import agent_graph

        assert agent_graph.is_catalog_runtime_enabled() is True

    def test_runtime_flag_disabled_under_legacy_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(_RUNTIME_FLAG, raising=False)
        monkeypatch.setenv(_LEGACY_FLAG, "1")
        from scripts.ai_assistant import agent_graph

        assert agent_graph.is_catalog_runtime_enabled() is False

    def test_runtime_flag_enabled_with_truthy_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_LEGACY_FLAG, raising=False)
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        from scripts.ai_assistant import agent_graph

        assert agent_graph.is_catalog_runtime_enabled() is True

    def test_runtime_flag_implies_catalog_binding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The runtime and binding flags share the same default and the
        same legacy override.

        Pre-cutover (#79) this test asserted that an explicitly set
        ``REPORTALIN_USE_CATALOG_RUNTIME`` implied the binding flag.
        After issue #81 both default to True; the legacy override
        flips both to False together. The implication property still
        holds (turning the runtime on never leaves the binding off).
        """
        monkeypatch.delenv(_BINDING_FLAG, raising=False)
        monkeypatch.delenv(_LEGACY_FLAG, raising=False)
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        import scripts.ai_assistant.analytical_engine as engine

        assert engine.is_catalog_binding_enabled() is True


# ---------------------------------------------------------------------------
# Tool-set selection — flag-OFF is the legacy union; flag-ON is catalog-first.
# ---------------------------------------------------------------------------


class TestRuntimeToolSelection:
    """The flag selects which tools the LLM sees, not what user-input
    keywords get routed. ``ALL_TOOLS`` (a union of every tool we ship)
    stays unchanged so existing tests in ``test_agent_tools.py`` keep
    passing; runtime helpers select a subset by flag.
    """

    def test_all_tools_constant_unchanged_count(self) -> None:
        """ALL_TOOLS pins the union — must remain at 13 (legacy + catalog)."""
        from scripts.ai_assistant.agent_tools import ALL_TOOLS

        assert len(ALL_TOOLS) == 13

    def test_legacy_tools_when_flag_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_RUNTIME_FLAG, raising=False)
        from scripts.ai_assistant.agent_graph import runtime_tools

        tools = runtime_tools(False)
        names = {t.name for t in tools}
        # Legacy path: the catalog-only tool is still available (LLM may
        # call it on its own) but the legacy lookup tools must remain
        # present so today's behaviour is preserved.
        assert "search_variables" in names
        assert "get_variable_details" in names
        assert "list_forms" in names
        assert "run_study_analysis" in names

    def test_catalog_tools_when_flag_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        from scripts.ai_assistant.agent_graph import runtime_tools

        tools = runtime_tools(True)
        names = {t.name for t in tools}
        # Catalog-aware metadata answering is always available when the
        # runtime flag is on.
        assert "answer_catalog_question" in names

    def test_runtime_tools_does_not_branch_on_user_query(self) -> None:
        """``runtime_tools`` must take only the flag, never a user query.

        This is the structural anti-router check: the function signature
        cannot accept a user-input string, because if it could it would
        be the start of a hidden keyword router.
        """
        import inspect

        from scripts.ai_assistant.agent_graph import runtime_tools

        sig = inspect.signature(runtime_tools)
        params = list(sig.parameters.values())
        assert len(params) == 1
        assert params[0].annotation in (bool, "bool")


# ---------------------------------------------------------------------------
# System-prompt selection — flag-ON variant gently steers users back to
# study queries WITHOUT a keyword router and quotes the boundary text.
# ---------------------------------------------------------------------------


class TestRuntimeSystemPrompt:
    def test_legacy_prompt_when_flag_off(self) -> None:
        from scripts.ai_assistant.agent_graph import runtime_system_prompt
        from scripts.ai_assistant.agent_prompts import SYSTEM_PROMPT

        prompt = runtime_system_prompt(False)
        assert prompt is SYSTEM_PROMPT or prompt == SYSTEM_PROMPT

    def test_catalog_prompt_when_flag_on_quotes_audit_only_note_verbatim(self) -> None:
        """The catalog runtime prompt must quote AUDIT_ONLY_NOTE so the
        LLM sees the exact phrasing to surface for audit-only questions.
        """
        from scripts.ai_assistant.agent_graph import runtime_system_prompt

        prompt = runtime_system_prompt(True)
        assert AUDIT_ONLY_NOTE in prompt

    def test_catalog_prompt_steers_small_talk_back_to_study(self) -> None:
        """Small talk handling is configuration-level: the prompt must
        instruct the LLM to respond and steer back to study-related
        queries, mentioning the study/catalog/research surface, WITHOUT
        a hardcoded "small talk detected" classifier phrase.
        """
        from scripts.ai_assistant.agent_graph import runtime_system_prompt

        prompt = runtime_system_prompt(True).lower()
        # Steers back to substantive study / catalog content
        assert "study" in prompt
        # Catalog-first guidance
        assert "catalog" in prompt
        # No keyword-routing artifact
        assert "small talk detected" not in prompt
        assert "is_small_talk" not in prompt
        assert "route_by_keywords" not in prompt

    def test_catalog_prompt_recommends_catalog_question_tool(self) -> None:
        from scripts.ai_assistant.agent_graph import runtime_system_prompt

        prompt = runtime_system_prompt(True)
        assert "answer_catalog_question" in prompt


# ---------------------------------------------------------------------------
# Catalog routing for metadata questions: the catalog path resolves
# variable metadata WITHOUT loading the legacy ``StudyKnowledge`` YAML.
# ---------------------------------------------------------------------------


class TestCatalogRoutingDoesNotLoadStudyKnowledge:
    def test_catalog_metadata_answer_does_not_instantiate_study_knowledge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The catalog Q&A path must answer metadata questions without
        spinning up the legacy YAML loader. The retriever lives in
        ``scripts.source_truth.retrieval`` and never imports StudyKnowledge.

        We monkeypatch ``StudyKnowledge.__init__`` to raise so any
        accidental load surfaces as a hard test failure.
        """
        from scripts.ai_assistant import study_knowledge as sk

        instantiated: list[bool] = []

        def _boom(self: Any, *args: Any, **kwargs: Any) -> None:
            instantiated.append(True)
            raise AssertionError(
                "StudyKnowledge was instantiated even though the catalog "
                "runtime path is supposed to bypass it."
            )

        monkeypatch.setattr(sk.StudyKnowledge, "__init__", _boom)

        catalog = build_catalog_artifact(_source_truth_artifact())
        retriever = SourceTruthRetriever.from_catalog_artifact(catalog)
        answer = retriever.answer_chat_question("What is HIV_HIV?")

        # The catalog path produced a usable metadata answer.
        assert answer.variable_ids == ["HIV_HIV"]
        assert answer.analysis_queryable is True
        # And StudyKnowledge was never spun up.
        assert instantiated == []


# ---------------------------------------------------------------------------
# Analysis binding via Dataset Schema (``binding_source == "dataset_schema"``)
# end-to-end. This is the AC3 acceptance: analysis requests use catalog-
# selected dataset variables and Dataset Schema validation BEFORE execution.
# ---------------------------------------------------------------------------


class TestAnalysisBindingThroughDatasetSchema:
    def test_outcome_and_predictors_bind_through_dataset_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_RUNTIME_FLAG, "1")

        source_truth = _source_truth_artifact()
        catalog = build_catalog_artifact(source_truth)
        schema = build_dataset_schema(source_truth)

        bindings = resolve_analysis_bindings(
            question="distribution of HIV_HIV",
            cohort_id="cohort_a",
            catalog=catalog,
            dataset_schema=schema,
            outcome_variable_id="HIV_HIV",
            predictor_variable_ids=[],
        )

        outcome = bindings["outcome"]
        # AC3: binding_source must be "dataset_schema" end-to-end.
        assert outcome["binding_source"] == "dataset_schema"
        assert outcome["analysis_queryable"] is True
        assert outcome["review_required"] is False
        # And the legacy run_full_analysis must refuse outright.
        import scripts.ai_assistant.analytical_engine as engine

        with pytest.raises((AnalysisBindingError, NotImplementedError)):
            engine.run_full_analysis(
                knowledge=None,  # type: ignore[arg-type]
                data_dir=Path("/dev/null"),
                output_dir=Path("/dev/null"),
                cohort_id="cohort_a",
            )


# ---------------------------------------------------------------------------
# Audit-only carry-over: the verbatim AUDIT_ONLY_NOTE must continue to
# surface for pseudonymized/PHI-handled records, regardless of the flag.
# ---------------------------------------------------------------------------


class TestAuditOnlyBoundaryCarryOver:
    def test_audit_only_record_returns_verbatim_audit_only_note(self) -> None:
        catalog = build_catalog_artifact(_source_truth_artifact())
        retriever = SourceTruthRetriever.from_catalog_artifact(catalog)

        answer = retriever.answer_chat_question("What is SUBJID?")
        assert answer.audit_only is True
        assert answer.text == AUDIT_ONLY_NOTE


# ---------------------------------------------------------------------------
# File-access boundary regression — flag-on must NOT widen the validator.
# ---------------------------------------------------------------------------


class TestFileAccessBoundaryNotWidened:
    """The cutover MUST NOT widen the agent-zone validator. Reads from
    ``data/raw/``, ``tmp/``, ``output/{study}/audit/``, and
    ``data/snapshots/{study}/`` continue to raise ``ZoneViolationError``.
    """

    def test_raw_data_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch, monkeypatch_config: Path
    ) -> None:
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        from scripts.ai_assistant.file_access import (
            ZoneViolationError,
            validate_agent_read,
        )

        f = monkeypatch_config / "raw" / "leak.xlsx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)

    def test_tmp_workspace_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch, monkeypatch_config: Path
    ) -> None:
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        import config
        from scripts.ai_assistant.file_access import (
            ZoneViolationError,
            validate_agent_read,
        )

        f = config.TMP_DIR / "extracted_variables" / "scratch.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)

    def test_audit_zone_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch, monkeypatch_config: Path
    ) -> None:
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        import config
        from scripts.ai_assistant.file_access import (
            ZoneViolationError,
            validate_agent_read,
        )

        f = config.STUDY_AUDIT_DIR / "phi_scrub_report.json"
        f.write_text("{}", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)

    def test_data_snapshots_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch, monkeypatch_config: Path
    ) -> None:
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        import config
        from scripts.ai_assistant.file_access import (
            ZoneViolationError,
            validate_agent_read,
        )

        f = config.STUDY_SNAPSHOTS_DIR / "baseline.jsonl"
        f.write_text("{}", encoding="utf-8")
        with pytest.raises(ZoneViolationError):
            validate_agent_read(f)


# ---------------------------------------------------------------------------
# No-router regression: production code added for #79 must not contain
# deterministic keyword-routing patterns.
# ---------------------------------------------------------------------------


def _read(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


class TestNoHiddenKeywordRouter:
    """Maintainer constraint #1 — no hidden keyword router. The flag
    selects which TOOLS the LLM sees and which prompt it uses, not what
    user-input keywords get force-routed.
    """

    _PRODUCTION_FILES = (
        "scripts/ai_assistant/agent_graph.py",
        "scripts/ai_assistant/agent_tools.py",
        "scripts/ai_assistant/agent_prompts.py",
    )

    _FORBIDDEN_PATTERNS = (
        re.compile(r'if\s+["\']audit["\']\s+in\s+\w+\.lower\(\)'),
        re.compile(r'if\s+["\']phi["\']\s+in\s+\w+\.lower\(\)'),
        re.compile(r"if\s+any\s*\(\s*kw\s+in\s+\w+\s+for\s+kw\s+in"),
        re.compile(r"\broute_by_keywords?\s*\("),
        re.compile(r"\bforce_tool\s*\("),
        re.compile(r"\bblock_tool\s*\("),
        re.compile(r"\bis_small_talk\s*\("),
        re.compile(r"\bdef\s+is_small_talk\b"),
    )

    def test_no_forbidden_patterns_in_production_modules(self) -> None:
        for rel in self._PRODUCTION_FILES:
            text = _read(rel)
            for pattern in self._FORBIDDEN_PATTERNS:
                assert pattern.search(text) is None, (
                    f"{rel} contains forbidden keyword-routing pattern "
                    f"{pattern.pattern!r}; the maintainer's #1 constraint "
                    "forbids deterministic keyword routing."
                )
