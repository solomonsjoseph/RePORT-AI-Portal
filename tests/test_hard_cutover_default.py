"""Tests for issue #81 — Hard cutover default flip (PRD #65 final slice).

After this cutover, the catalog plus Dataset Schema path is the standard
runtime. The previously opt-in flags (``REPORTALIN_USE_CATALOG_RUNTIME``,
``REPORTALIN_USE_CATALOG_BINDING``) are now default-on, and the legacy
``StudyKnowledge`` YAML path is reachable only when the explicit
``REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE`` override env var is set.

These tests pin:

* The new defaults: with no env vars, the runtime + binding flags both
  return True and the catalog-aware tool set / system prompt are in
  effect.
* The legacy override env var: when ``REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE``
  is truthy, both flags fall back to False and the old
  ``StudyKnowledge`` path is reachable. The override beats the explicit
  ``REPORTALIN_USE_CATALOG_RUNTIME=1`` flag (it is a hard kill switch).
* User-facing terminology no longer says "trio bundle" in the prompt /
  tool descriptions the LLM sees by default. File path constants such
  as ``output/{study}/trio_bundle/`` are filesystem layout, not user
  language, and remain untouched.
* A rollback document exists describing how to set the legacy override
  for one release window.
* In normal config (no env vars), the legacy ``StudyKnowledge`` loader
  is never instantiated when the system answers a metadata question
  through the catalog path.
* The hard cutover validation gate (issue #80) still reports every AC
  as ``pass`` after the defaults flip.
* No hidden keyword router was introduced: the production files added
  or modified for this slice contain none of the forbidden
  ``route_by_keywords`` / ``force_tool`` / ``is_small_talk`` patterns.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.source_truth.builder import build_source_truth_artifact
from scripts.source_truth.catalog import build_catalog_artifact
from scripts.source_truth.retrieval import SourceTruthRetriever

_RUNTIME_FLAG = "REPORTALIN_USE_CATALOG_RUNTIME"
_BINDING_FLAG = "REPORTALIN_USE_CATALOG_BINDING"
_LEGACY_FLAG = "REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE"
_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Source-truth fixture used across the tests.
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


def _clear_all_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helper to ensure no env vars influence the test."""
    monkeypatch.delenv(_RUNTIME_FLAG, raising=False)
    monkeypatch.delenv(_BINDING_FLAG, raising=False)
    monkeypatch.delenv(_LEGACY_FLAG, raising=False)


# ---------------------------------------------------------------------------
# Default flag state — catalog runtime + binding ON by default after #81.
# ---------------------------------------------------------------------------


class TestDefaultsAfterCutover:
    """With no env vars set, the catalog path is the standard runtime."""

    def test_catalog_runtime_default_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all_flags(monkeypatch)
        from scripts.ai_assistant import agent_graph

        assert agent_graph.is_catalog_runtime_enabled() is True

    def test_catalog_binding_default_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_all_flags(monkeypatch)
        import scripts.ai_assistant.analytical_engine as engine

        assert engine.is_catalog_binding_enabled() is True

    def test_runtime_tools_returns_catalog_aware_tool_set_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no env vars, ``runtime_tools`` exposes the catalog tool."""
        _clear_all_flags(monkeypatch)
        from scripts.ai_assistant.agent_graph import (
            is_catalog_runtime_enabled,
            runtime_tools,
        )

        tools = runtime_tools(is_catalog_runtime_enabled())
        names = {t.name for t in tools}
        assert "answer_catalog_question" in names

    def test_runtime_system_prompt_is_catalog_prompt_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_all_flags(monkeypatch)
        from scripts.ai_assistant.agent_graph import (
            is_catalog_runtime_enabled,
            runtime_system_prompt,
        )
        from scripts.ai_assistant.agent_prompts import (
            CATALOG_RUNTIME_SYSTEM_PROMPT,
        )

        prompt = runtime_system_prompt(is_catalog_runtime_enabled())
        assert prompt == CATALOG_RUNTIME_SYSTEM_PROMPT

    def test_run_full_analysis_refuses_legacy_path_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Carry-over from #75: with the binding flag on (now the default),
        the legacy ``run_full_analysis`` entry point raises rather than
        instantiating the old ``StudyKnowledge`` runner.
        """
        _clear_all_flags(monkeypatch)
        import scripts.ai_assistant.analytical_engine as engine
        from scripts.source_truth.analysis_binding import AnalysisBindingError

        with pytest.raises(AnalysisBindingError):
            engine.run_full_analysis(
                knowledge=None,  # type: ignore[arg-type]
                data_dir=Path("/dev/null"),
                output_dir=Path("/dev/null"),
                cohort_id="cohort_a",
            )


# ---------------------------------------------------------------------------
# Legacy override — REPORTALIN_USE_LEGACY_STUDY_KNOWLEDGE=1 keeps the old
# StudyKnowledge path reachable for one release window.
# ---------------------------------------------------------------------------


class TestLegacyStudyKnowledgeOverride:
    """The legacy override env var keeps the old YAML path reachable."""

    def test_legacy_override_disables_catalog_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_all_flags(monkeypatch)
        monkeypatch.setenv(_LEGACY_FLAG, "1")
        from scripts.ai_assistant import agent_graph

        assert agent_graph.is_catalog_runtime_enabled() is False

    def test_legacy_override_disables_catalog_binding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_all_flags(monkeypatch)
        monkeypatch.setenv(_LEGACY_FLAG, "1")
        import scripts.ai_assistant.analytical_engine as engine

        assert engine.is_catalog_binding_enabled() is False

    def test_legacy_override_beats_explicit_catalog_runtime_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The legacy override is a kill switch: even if the explicit
        ``REPORTALIN_USE_CATALOG_RUNTIME=1`` flag is set, the legacy
        override wins. Operators shouldn't have to unset the new flag
        manually to roll back to the old path.
        """
        _clear_all_flags(monkeypatch)
        monkeypatch.setenv(_RUNTIME_FLAG, "1")
        monkeypatch.setenv(_LEGACY_FLAG, "1")
        import scripts.ai_assistant.analytical_engine as engine
        from scripts.ai_assistant import agent_graph

        assert agent_graph.is_catalog_runtime_enabled() is False
        assert engine.is_catalog_binding_enabled() is False

    def test_legacy_study_knowledge_class_is_reachable_under_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The override does not delete the legacy YAML loader; the
        ``StudyKnowledge`` class must still be importable and usable so
        operators can run the old path during the rollback window.
        """
        _clear_all_flags(monkeypatch)
        monkeypatch.setenv(_LEGACY_FLAG, "1")
        from scripts.ai_assistant.study_knowledge import StudyKnowledge

        # The class itself is still defined and callable. We don't
        # exercise the YAML-load path here (no fixture); we only
        # confirm the import surface remains intact.
        assert StudyKnowledge is not None
        assert callable(StudyKnowledge)

    def test_legacy_override_runtime_tools_returns_legacy_tool_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under the legacy override, the runtime helpers report
        flag-OFF behavior so the agent sees the legacy prompt and
        original tool surface."""
        _clear_all_flags(monkeypatch)
        monkeypatch.setenv(_LEGACY_FLAG, "1")
        from scripts.ai_assistant.agent_graph import (
            is_catalog_runtime_enabled,
            runtime_system_prompt,
        )
        from scripts.ai_assistant.agent_prompts import SYSTEM_PROMPT

        flag_on = is_catalog_runtime_enabled()
        assert flag_on is False
        prompt = runtime_system_prompt(flag_on)
        assert prompt == SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Trio-bundle terminology — replaced in user-facing prompts/tool descriptions.
# Storage path constants (``trio_bundle/`` directory layout) are exempt.
# ---------------------------------------------------------------------------


class TestUserFacingTerminologyDoesNotSayTrioBundle:
    """User-facing language on the default path drops "trio bundle"."""

    def test_default_runtime_system_prompt_does_not_mention_trio_bundle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The system prompt the LLM sees by default after cutover must
        not contain the legacy "trio" / "trio bundle" wording. The LLM
        never sees directory-layout strings, so the only guarded text
        is the prompt itself.
        """
        _clear_all_flags(monkeypatch)
        from scripts.ai_assistant.agent_graph import (
            is_catalog_runtime_enabled,
            runtime_system_prompt,
        )

        prompt = runtime_system_prompt(is_catalog_runtime_enabled()).lower()
        # The legacy "complete trio (PDF + data dictionary + dataset)"
        # phrasing must not be the user-facing language any more.
        assert "trio bundle" not in prompt
        assert "complete trio" not in prompt
        # The cutover language is "catalog" / "current dataset".
        assert "catalog" in prompt

    def test_run_python_analysis_tool_description_does_not_mention_trio_bundle(
        self,
    ) -> None:
        """LangChain surfaces tool docstrings to the LLM verbatim. The
        ``run_python_analysis`` description previously said "study's
        de-identified trio bundle"; after cutover that phrase is
        replaced with catalog/dataset language.
        """
        from scripts.ai_assistant.agent_tools import run_python_analysis

        description = (run_python_analysis.description or "").lower()
        assert "trio bundle" not in description


# ---------------------------------------------------------------------------
# Rollback documentation — describes the legacy override env var.
# ---------------------------------------------------------------------------


class TestRollbackDocumentation:
    """A rollback / feature-flag fallback doc must exist for one release window.

    The doc must mention the explicit override env var name so an
    operator can map "I need the old behavior" to a concrete action.
    """

    def test_rollback_doc_exists_and_names_legacy_override_env_var(self) -> None:
        candidates = list(
            (_REPO_ROOT / "docs" / "sphinx" / "developer_guide").glob("*.rst")
        ) + list((_REPO_ROOT / "docs" / "sphinx" / "developer_guide").glob("*.md"))
        matches: list[Path] = []
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if _LEGACY_FLAG in text:
                matches.append(path)
        assert matches, (
            f"expected at least one developer-guide doc to mention the "
            f"{_LEGACY_FLAG} env var so operators can roll back to the "
            f"legacy path during the cutover window."
        )

    def test_rollback_doc_is_indexed_in_developer_guide(self) -> None:
        """The rollback doc must be reachable from the developer-guide
        toctree so it's actually discoverable."""
        index = (_REPO_ROOT / "docs" / "sphinx" / "developer_guide" / "index.rst").read_text(
            encoding="utf-8"
        )
        assert "catalog_cutover" in index


# ---------------------------------------------------------------------------
# Old runtime path disabled in normal config — StudyKnowledge.__init__
# is not invoked when the system answers a metadata question.
# ---------------------------------------------------------------------------


class TestOldRuntimePathDisabledInNormalConfig:
    """Spy on ``StudyKnowledge.__init__`` and prove zero invocations on
    the default path."""

    def test_catalog_metadata_answer_does_not_instantiate_study_knowledge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_all_flags(monkeypatch)
        from scripts.ai_assistant import study_knowledge as sk

        invocations: list[Any] = []
        original_init = sk.StudyKnowledge.__init__

        def _spy(self: Any, *args: Any, **kwargs: Any) -> None:
            invocations.append((args, kwargs))
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(sk.StudyKnowledge, "__init__", _spy)

        catalog = build_catalog_artifact(_source_truth_artifact())
        retriever = SourceTruthRetriever.from_catalog_artifact(catalog)
        answer = retriever.answer_chat_question("What is HIV_HIV?")

        # Catalog answer succeeded.
        assert answer.variable_ids == ["HIV_HIV"]
        assert answer.analysis_queryable is True
        # And the legacy YAML loader was never spun up.
        assert invocations == []


# ---------------------------------------------------------------------------
# Cutover validation gate (issue #80) still passes with the new defaults.
# ---------------------------------------------------------------------------


def _hiv_pilot_inputs() -> dict[str, Any]:
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_path": "Indo-VAP/datasets/6_HIV.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [
            {
                "sheet": "_6_HIV",
                "columns": [
                    "SUBJID",
                    "ICTC",
                    "HIV_VISIT",
                    "HIV_HIV",
                    "Time_Stamp",
                ],
            }
        ],
        "column_count": 5,
    }
    pdf_extraction = {
        "page_count": 1,
        "annotation_count": 4,
        "real_annotation_variables": ["SUBJID", "ICTC", "HIV_VISIT", "HIV_HIV"],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": ["SUBJID", "ICTC", "HIV_VISIT", "HIV_HIV"],
            }
        ],
        "option_sets": {
            "hiv_result_pdf": {
                "source": "PDF option text",
                "values": ["Positive (+)", "Negative (-)", "Indeterminate"],
            }
        },
        "metadata": {"form_number": "Form 6", "form_title": "6 HIV"},
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "6_HIV.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/6 HIV v1.0.pdf",
        "coverage": {
            "boundary": (
                "Dataset column names plus PDF annotations and visible options "
                "only; raw dataset values not inspected."
            )
        },
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "ICTC": {
                "action": "pseudonymize",
                "reason": "facility_clinic_ictc_or_site_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "HIV_VISIT": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
            },
            "HIV_HIV": {
                "action": "keep",
                "reason": "direct_pdf_annotated_clinical_or_categorical_field",
                "confidence": "high",
                "section": "hiv_fields",
                "pdf_annotation_status": "direct",
                "option_set": "hiv_result_pdf",
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "non_pdf_system_timestamp_metadata",
                "confidence": "high",
                "section": "system_metadata",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }
    return {
        "column_inventory": column_inventory,
        "pdf_extraction": pdf_extraction,
        "field_policy": field_policy,
    }


def _fob_pilot_inputs() -> dict[str, Any]:
    columns = [
        "SUBJID",
        "FOB_VISDAT",
        "FOB_COHBOUT",
        "FOB_REVIEW_NOTE",
        "SYSTEM_ID",
        "Time_Stamp",
    ]
    column_inventory = {
        "study": "Indo-VAP",
        "source_file": "98B_FOB.xlsx",
        "source_path": "Indo-VAP/datasets/98B_FOB.xlsx",
        "extraction_boundary": "column_names_only_header_row",
        "sheets": [{"sheet": "_98B_FOB", "columns": list(columns)}],
        "column_count": len(columns),
    }
    pdf_extraction = {
        "page_count": 2,
        "annotation_count": 4,
        "real_annotation_variables": [
            "SUBJID",
            "FOB_VISDAT",
            "FOB_COHBOUT",
            "FOB_REVIEW_NOTE",
        ],
        "annotation_pages": [
            {
                "page": 1,
                "annotations": [
                    "SUBJID",
                    "FOB_VISDAT",
                    "FOB_COHBOUT",
                    "FOB_REVIEW_NOTE",
                ],
            }
        ],
        "option_sets": {
            "fob_cohort_outcome": {
                "source": "PDF option text",
                "values": ["No TB", "Probable case", "Definite case"],
            }
        },
        "metadata": {"form_number": "Form 98B", "form_title": "Final Outcome"},
    }
    field_policy = {
        "study": "Indo-VAP",
        "source_file": "98B_FOB.xlsx",
        "source_pdf": "Indo-VAP/annotated_pdfs/98B_FOB.pdf",
        "coverage": {
            "boundary": (
                "Dataset column names plus PDF annotations/options only; "
                "raw dataset values not inspected."
            )
        },
        "fields": {
            "SUBJID": {
                "action": "pseudonymize",
                "reason": "participant_identifier",
                "confidence": "high",
                "section": "participant_header",
                "pdf_annotation_status": "direct",
            },
            "FOB_VISDAT": {
                "action": "jitter_date",
                "reason": "date_field",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "final_outcome_visit",
                "pdf_annotation_status": "direct",
            },
            "FOB_COHBOUT": {
                "action": "keep",
                "reason": "clinical_outcome",
                "confidence": "high",
                "field_class": "study_variable",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
                "option_set": "fob_cohort_outcome",
            },
            "FOB_REVIEW_NOTE": {
                "action": "review_required",
                "reason": "ambiguous_free_text_follow_up_note",
                "confidence": "low",
                "field_class": "study_variable",
                "section": "final_outcome",
                "pdf_annotation_status": "direct",
            },
            "SYSTEM_ID": {
                "action": "drop",
                "reason": "system_metadata",
                "confidence": "high",
                "field_class": "system_metadata",
                "pdf_annotation_status": "not_annotated",
            },
            "Time_Stamp": {
                "action": "drop",
                "reason": "timestamp_metadata",
                "confidence": "high",
                "field_class": "timestamp_metadata",
                "pdf_annotation_status": "not_annotated",
            },
        },
    }
    return {
        "column_inventory": column_inventory,
        "pdf_extraction": pdf_extraction,
        "field_policy": field_policy,
    }


def _write_pilot_form(root: Path, form_id: str, inputs: dict[str, Any]) -> None:
    import json

    import yaml

    form_dir = root / form_id
    form_dir.mkdir(parents=True, exist_ok=True)
    (form_dir / "column_inventory.json").write_text(
        json.dumps(inputs["column_inventory"]), encoding="utf-8"
    )
    (form_dir / "pdf_extraction.json").write_text(
        json.dumps(inputs["pdf_extraction"]), encoding="utf-8"
    )
    (form_dir / "field_policy.draft.yaml").write_text(
        yaml.safe_dump(inputs["field_policy"], sort_keys=False), encoding="utf-8"
    )


@pytest.fixture()
def policy_pilot_root(tmp_path: Path) -> Path:
    root = tmp_path / "policy_pilot"
    _write_pilot_form(root, "6_HIV", _hiv_pilot_inputs())
    _write_pilot_form(root, "98B_FOB", _fob_pilot_inputs())
    return root


class TestCutoverGateStillPassesWithNewDefaults:
    """Regression: issue #80's hard cutover validation gate must still
    report every AC as ``pass`` after the defaults flip."""

    def test_run_hard_cutover_validation_passes_all_acs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        policy_pilot_root: Path,
    ) -> None:
        _clear_all_flags(monkeypatch)
        from scripts.source_truth.cutover_gate import (
            STATUS_PASS,
            STATUS_WARN,
            run_hard_cutover_validation,
        )

        report = run_hard_cutover_validation(policy_pilot_root=policy_pilot_root)

        # Every AC must be in {pass, warn} -- no failures.
        statuses = {entry["ac_id"]: entry["status"] for entry in report}
        failed = {ac for ac, status in statuses.items() if status == "fail"}
        assert failed == set(), report

        # AC1..AC8 must report pass (AC9 is permitted to warn when the
        # agent_zone_root is omitted; that branch is exercised in the
        # gate's own test file).
        for ac in ("AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7", "AC8"):
            assert statuses[ac] == STATUS_PASS, (ac, report)
        assert statuses["AC9"] in {STATUS_PASS, STATUS_WARN}, report


# ---------------------------------------------------------------------------
# No hidden keyword router regression — the production files modified
# for this slice must not introduce any of the forbidden patterns.
# ---------------------------------------------------------------------------


class TestNoHiddenKeywordRouter:
    """Maintainer constraint #1 — no hidden keyword router."""

    _PRODUCTION_FILES = (
        "scripts/ai_assistant/agent_graph.py",
        "scripts/ai_assistant/agent_tools.py",
        "scripts/ai_assistant/agent_prompts.py",
        "scripts/ai_assistant/analytical_engine.py",
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
            text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
            for pattern in self._FORBIDDEN_PATTERNS:
                assert pattern.search(text) is None, (
                    f"{rel} contains forbidden keyword-routing pattern "
                    f"{pattern.pattern!r}; PRD #65 / issue #81 forbid "
                    "deterministic keyword routing on the runtime path."
                )
