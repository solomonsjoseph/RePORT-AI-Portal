"""Tests for get_study_variable_map tool.

Uses the real published map at output/Indo-VAP/llm_source/study_metadata/
when present; skips gracefully when absent (CI without a populated output/).

The tool must return pure metadata (variable names, encodings, formulas) —
never row values.  Tests assert exact concept→column bindings, the Chumlea
formula string, malnutrition threshold, and outcome aggregation verbs against
the authoritative YAML.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

langchain = pytest.importorskip("langchain_core", reason="langchain_core required")
yaml_mod = pytest.importorskip("yaml", reason="pyyaml required")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_MAP_PATH = (
    REPO_ROOT / "output" / "Indo-VAP" / "llm_source" / "study_metadata" / "study_variable_map.yaml"
)

_real_map_available = pytest.mark.skipif(
    not _REAL_MAP_PATH.is_file(),
    reason=f"Real study_variable_map.yaml not present at {_REAL_MAP_PATH}",
)


def _invoke(cohort: str = "", concept: str = "") -> dict:
    """Invoke the tool and return parsed JSON."""
    from scripts.ai_assistant.agent_tools import get_study_variable_map

    raw = get_study_variable_map.invoke({"cohort": cohort, "concept": concept})
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Tests against the real published map
# ---------------------------------------------------------------------------


def _patch_real_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch config + secure_env so the real published map passes zone checks."""
    import config
    import scripts.security.secure_env as _se

    real_llm_source = _REAL_MAP_PATH.parent.parent  # output/Indo-VAP/llm_source
    real_agent_dir = real_llm_source.parent / "agent"
    monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", real_llm_source)
    monkeypatch.setattr(config, "LLM_SOURCE_STUDY_METADATA_DIR", _REAL_MAP_PATH.parent)
    monkeypatch.setattr(config, "AGENT_STATE_DIR", real_agent_dir)
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", real_llm_source.parent / "audit")
    monkeypatch.setattr(_se, "_OUTPUT_MARKER", str(real_llm_source.parent.parent.resolve()))
    monkeypatch.setattr(_se, "_CLEAN_MARKER", str(real_llm_source.resolve()))


class TestGetStudyVariableMapRealMap:
    """Run against the real output/Indo-VAP/... YAML when it exists."""

    @_real_map_available
    def test_cohort_a_recurrence_column_and_aggregation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cohort_a recurrence must resolve to FOA_COHAOUT + worst_per_subject."""
        _patch_real_map(monkeypatch)
        payload = _invoke(cohort="cohort_a", concept="recurrence")
        outcome = payload["data"]["outcomes"]["recurrence"]
        assert outcome["column"] == "FOA_COHAOUT", outcome
        assert outcome["aggregation"] == "worst_per_subject", outcome

    @_real_map_available
    def test_cohort_a_diabetes_column(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cohort_a diabetes must bind to IC_DMDX."""
        _patch_real_map(monkeypatch)
        payload = _invoke(cohort="cohort_a", concept="diabetes")
        predictor = payload["data"]["predictors"]["diabetes"]
        assert predictor["column"] == "IC_DMDX", predictor

    @_real_map_available
    def test_cohort_b_diabetes_column(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cohort_b diabetes must bind to HC_DMDX."""
        _patch_real_map(monkeypatch)
        payload = _invoke(cohort="cohort_b", concept="diabetes")
        predictor = payload["data"]["predictors"]["diabetes"]
        assert predictor["column"] == "HC_DMDX", predictor

    @_real_map_available
    def test_chumlea_formula_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Knee-height note must contain the Chumlea formula coefficients."""
        _patch_real_map(monkeypatch)
        # Both cohorts carry the formula; test cohort_a
        payload = _invoke(cohort="cohort_a", concept="knee_height")
        knee = payload["data"]["predictors"]["knee_height"]
        note = (knee.get("note") or "").lower()
        # Formula contains "2.02" and "64.19"
        assert "2.02" in note, f"Chumlea coefficient 2.02 missing from note: {note!r}"
        assert "64.19" in note, f"Chumlea coefficient 64.19 missing from note: {note!r}"

    @_real_map_available
    def test_malnutrition_threshold(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Malnutrition threshold must be 18.5."""
        _patch_real_map(monkeypatch)
        payload = _invoke(cohort="cohort_a", concept="malnutrition")
        mal = payload["data"]["derived_variables"]["malnutrition"]
        assert mal["threshold"] == 18.5, mal

    @_real_map_available
    def test_cohort_b_incident_tb_primary_and_additional_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cohort_b incident_tb: primary FOB_COHBOUT + FUB_TBDIAG additional source."""
        _patch_real_map(monkeypatch)
        payload = _invoke(cohort="cohort_b", concept="incident_tb")
        outcome = payload["data"]["outcomes"]["incident_tb"]
        assert outcome["column"] == "FOB_COHBOUT", outcome
        assert outcome["aggregation"] == "any_positive_per_subject", outcome
        additional_cols = [s["column"] for s in outcome.get("additional_sources", [])]
        assert "FUB_TBDIAG" in additional_cols, f"FUB_TBDIAG missing from {additional_cols}"

    @_real_map_available
    def test_both_cohorts_returned_when_no_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Blank cohort argument must return both cohort_a and cohort_b."""
        _patch_real_map(monkeypatch)
        payload = _invoke()
        assert "cohort_a" in payload["cohorts"], payload.keys()
        assert "cohort_b" in payload["cohorts"], payload.keys()

    @_real_map_available
    def test_subjid_join_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dataset_relationships.join_key must be SUBJID."""
        _patch_real_map(monkeypatch)
        payload = _invoke()
        assert payload["dataset_relationships"]["join_key"] == "SUBJID", payload


# ---------------------------------------------------------------------------
# Tests against a synthetic minimal YAML (no filesystem dependency)
# ---------------------------------------------------------------------------

_MINIMAL_MAP = """\
study:
  name: "TestStudy"
cohorts:
  cohort_a:
    subject_id: "SUBJID"
    predictors:
      diabetes:
        column: "DM_COL"
        dataset: "screening.jsonl"
        type: "categorical"
    derived_variables:
      bmi:
        formula: "weight_kg / (height_m ^ 2)"
      malnutrition:
        type: "binary"
        source: "bmi"
        threshold: 18.5
    outcomes:
      recurrence:
        column: "OUTCOME_COL"
        dataset: "outcome.jsonl"
        aggregation: "worst_per_subject"
  cohort_b:
    subject_id: "SUBJID"
    predictors:
      diabetes:
        column: "DM_COL_B"
        dataset: "screening_b.jsonl"
        type: "categorical"
    outcomes:
      incident_tb:
        column: "INC_COL"
        dataset: "inc.jsonl"
        aggregation: "any_positive_per_subject"
        additional_sources:
          - column: "DIAG_COL"
            dataset: "diag.jsonl"
            positive_values: [1]
dataset_relationships:
  join_key: "SUBJID"
"""


@pytest.fixture()
def synthetic_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a minimal synthetic YAML inside a fake llm_source tree.

    Patches both ``STUDY_LLM_SOURCE_DIR`` and ``LLM_SOURCE_STUDY_METADATA_DIR``
    so ``validate_agent_read`` accepts paths under the fixture directory, and
    also patches ``AGENT_STATE_DIR`` so zone checks don't need the real output
    tree.  Returns the ``study_metadata/`` directory path.
    """
    import config
    import scripts.security.secure_env as _se

    llm_source = tmp_path / "llm_source"
    meta_dir = llm_source / "study_metadata"
    meta_dir.mkdir(parents=True)
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)

    (meta_dir / "study_variable_map.yaml").write_text(_MINIMAL_MAP, encoding="utf-8")

    monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", llm_source)
    monkeypatch.setattr(config, "LLM_SOURCE_STUDY_METADATA_DIR", meta_dir)
    monkeypatch.setattr(config, "AGENT_STATE_DIR", agent_dir)
    monkeypatch.setattr(config, "STUDY_AUDIT_DIR", audit_dir)
    monkeypatch.setattr(_se, "_OUTPUT_MARKER", str(tmp_path.resolve()))
    monkeypatch.setattr(_se, "_CLEAN_MARKER", str(llm_source.resolve()))
    return meta_dir


class TestGetStudyVariableMapSynthetic:
    """Run against a synthetic minimal map — always executes (no real-data dep).

    The ``synthetic_map`` fixture already patches ``STUDY_LLM_SOURCE_DIR``,
    ``LLM_SOURCE_STUDY_METADATA_DIR``, and ``AGENT_STATE_DIR`` so that
    ``validate_agent_read`` accepts the synthetic path.
    """

    def test_concept_filter_cohort_a_diabetes(
        self,
        synthetic_map: Path,
    ) -> None:
        payload = _invoke(cohort="cohort_a", concept="diabetes")
        assert payload["data"]["predictors"]["diabetes"]["column"] == "DM_COL"

    def test_concept_filter_cohort_b_diabetes(
        self,
        synthetic_map: Path,
    ) -> None:
        payload = _invoke(cohort="cohort_b", concept="diabetes")
        assert payload["data"]["predictors"]["diabetes"]["column"] == "DM_COL_B"

    def test_malnutrition_threshold_synthetic(
        self,
        synthetic_map: Path,
    ) -> None:
        payload = _invoke(cohort="cohort_a", concept="malnutrition")
        assert payload["data"]["derived_variables"]["malnutrition"]["threshold"] == 18.5

    def test_recurrence_aggregation_synthetic(
        self,
        synthetic_map: Path,
    ) -> None:
        payload = _invoke(cohort="cohort_a", concept="recurrence")
        assert payload["data"]["outcomes"]["recurrence"]["aggregation"] == "worst_per_subject"

    def test_incident_tb_additional_source_synthetic(
        self,
        synthetic_map: Path,
    ) -> None:
        payload = _invoke(cohort="cohort_b", concept="incident_tb")
        outcome = payload["data"]["outcomes"]["incident_tb"]
        assert outcome["aggregation"] == "any_positive_per_subject"
        additional_cols = [s["column"] for s in outcome.get("additional_sources", [])]
        assert "DIAG_COL" in additional_cols

    def test_blank_cohort_returns_both(
        self,
        synthetic_map: Path,
    ) -> None:
        payload = _invoke()
        assert "cohort_a" in payload["cohorts"]
        assert "cohort_b" in payload["cohorts"]

    def test_join_key_synthetic(
        self,
        synthetic_map: Path,
    ) -> None:
        payload = _invoke()
        assert payload["dataset_relationships"]["join_key"] == "SUBJID"

    def test_missing_map_returns_error_json(
        self,
        synthetic_map: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the YAML is absent but the dir is in the read zone, returns JSON error."""
        import config

        # Place an empty metadata dir inside the existing llm_source tree so
        # validate_agent_read still accepts it.
        llm_source = synthetic_map.parent
        empty_meta = llm_source / "study_metadata_absent"
        empty_meta.mkdir()
        monkeypatch.setattr(config, "LLM_SOURCE_STUDY_METADATA_DIR", empty_meta)
        from scripts.ai_assistant.agent_tools import get_study_variable_map

        raw = get_study_variable_map.invoke({})
        payload = json.loads(raw)
        assert "error" in payload, f"Expected error key in: {payload}"

    def test_return_is_string(
        self,
        synthetic_map: Path,
    ) -> None:
        from scripts.ai_assistant.agent_tools import get_study_variable_map

        result = get_study_variable_map.invoke({})
        assert isinstance(result, str)
        # Must be valid JSON
        json.loads(result)
