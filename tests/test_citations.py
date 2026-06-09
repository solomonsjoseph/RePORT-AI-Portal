"""Tests for scripts.ai_assistant.citations.cite_variable.

Builds a synthetic llm_source/ tree under tmp_path (no PHI, no production
data) and points the config path constants at it, then asserts the
source-precedence and not-found behaviour of the citation resolver.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.ai_assistant.citations import (
    Citation,
    CitationNotFoundError,
    cite_variable,
)


@pytest.fixture
def llm_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal llm_source tree and repoint config at it."""
    root = tmp_path / "llm_source"
    sot = root / "SoT"
    files = root / "dataset_schema" / "files"
    meta = root / "study_metadata"
    for d in (sot, files, meta):
        d.mkdir(parents=True, exist_ok=True)

    # Form 95_SAE: policy + schema + jsonl (header-only)
    sae = sot / "95_SAE"
    (sae / "pdf").mkdir(parents=True)
    (sae / "dataset").mkdir(parents=True)
    (sae / "pdf" / "95_SAE_policy.yaml").write_text(
        "variables:\n"
        "  SUBJID:\n    type: identifier\n"
        "  AE_AGE:\n    pdf_question: 'Age at time of event:'\n",
        encoding="utf-8",
    )
    (sae / "dataset" / "95_SAE_schema.json").write_text(
        json.dumps(
            {"columns": [{"name": "SUBJID"}, {"name": "AE_AGE"}, {"name": "AE_EVENT"}]}, indent=2
        ),
        encoding="utf-8",
    )
    (files / "95_SAE.jsonl").write_text(
        json.dumps({"SUBJID": None, "AE_AGE": None, "AE_ONLYINJSONL": None}) + "\n",
        encoding="utf-8",
    )

    # Form 98A_FOA: schema only (no policy) — exercises prefix resolution "98A"
    foa = sot / "98A_FOA"
    (foa / "dataset").mkdir(parents=True)
    (foa / "dataset" / "98A_FOA_schema.json").write_text(
        json.dumps({"columns": [{"name": "FOA_COHAOUT"}]}, indent=2),
        encoding="utf-8",
    )

    (meta / "study_variable_map.yaml").write_text(
        'cohorts:\n  cohort_a:\n    demographics:\n      sex:\n        column: "IS_SEX"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "BASE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_SOT_DIR", sot, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_DATASET_SCHEMA_FILES_DIR", files, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_STUDY_METADATA_DIR", meta, raising=False)
    return root


def test_form_policy_wins_precedence(llm_source: Path) -> None:
    """A field defined in the policy YAML cites form_policy, not schema/jsonl."""
    c = cite_variable("95_SAE", "AE_AGE")
    assert isinstance(c, Citation)
    assert c.source_kind == "form_policy"
    assert c.file.endswith("95_SAE_policy.yaml")
    assert c.line == 4  # 'AE_AGE:' line in the synthetic policy
    assert c.matched_term == "AE_AGE"


def test_falls_back_to_schema_when_no_policy(llm_source: Path) -> None:
    """Prefix form_id '98A' resolves to 98A_FOA; field cites dataset_schema."""
    c = cite_variable("98A", "FOA_COHAOUT")
    assert c.source_kind == "dataset_schema"
    assert c.file.endswith("98A_FOA_schema.json")


def test_jsonl_fallback_does_not_echo_values(llm_source: Path) -> None:
    """A column only in the jsonl header cites llm_jsonl with a safe snippet."""
    c = cite_variable("95_SAE", "AE_ONLYINJSONL")
    assert c.source_kind == "llm_jsonl"
    assert c.line == 1
    assert c.snippet == 'column "AE_ONLYINJSONL" present in 95_SAE.jsonl'


def test_study_config_fallback(llm_source: Path) -> None:
    """A field absent per-form but mapped in the variable map cites study_config."""
    c = cite_variable("95_SAE", "IS_SEX")
    assert c.source_kind == "study_config"
    assert c.file.endswith("study_variable_map.yaml")


def test_unknown_form_raises(llm_source: Path) -> None:
    with pytest.raises(CitationNotFoundError):
        cite_variable("NoSuchForm", "AE_AGE")


def test_unknown_field_raises(llm_source: Path) -> None:
    with pytest.raises(CitationNotFoundError):
        cite_variable("95_SAE", "NOPE_NOT_A_FIELD")


def test_empty_field_raises(llm_source: Path) -> None:
    with pytest.raises(CitationNotFoundError):
        cite_variable("95_SAE", "   ")


# ---------------------------------------------------------------------------
# GAP-9: _cite_in_study_config uses anchored patterns — no substring-word hits
# ---------------------------------------------------------------------------


@pytest.fixture
def variable_map_llm_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Synthetic llm_source tree with a study_variable_map.yaml containing 'CAGE'."""
    root = tmp_path / "llm_source"
    sot = root / "SoT"
    files = root / "dataset_schema" / "files"
    meta = root / "study_metadata"
    for d in (sot, files, meta):
        d.mkdir(parents=True, exist_ok=True)

    # A form with a CAGE column (deliberately contains 'AGE' as substring)
    form_dir = sot / "99_Test"
    (form_dir / "pdf").mkdir(parents=True)
    (form_dir / "dataset").mkdir(parents=True)
    (form_dir / "pdf" / "99_Test_policy.yaml").write_text(
        "variables:\n  CAGE:\n    pdf_question: CAGE alcohol screening\n",
        encoding="utf-8",
    )
    (form_dir / "dataset" / "99_Test_schema.json").write_text(
        json.dumps({"columns": [{"name": "CAGE"}]}),
        encoding="utf-8",
    )

    (meta / "study_variable_map.yaml").write_text(
        "cohorts:\n"
        "  cohort_a:\n"
        "    alcohol:\n"
        "      cage_score:\n"
        "        column: CAGE\n"
        "        description: CAGE alcohol screening score\n"
        "    demographics:\n"
        "      enrollment_age:\n"
        # Unquoted longer column sharing the 'AGE' prefix — the suffix-collision
        # case (GAP-9): querying 'AGE' must NOT match this.
        "        column: AGE_AT_ENROLL\n"
        "        description: age at enrollment in years\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "BASE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_SOT_DIR", sot, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_DATASET_SCHEMA_FILES_DIR", files, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_STUDY_METADATA_DIR", meta, raising=False)
    return root


def test_cite_study_config_no_substring_false_citation(
    variable_map_llm_source: Path,
) -> None:
    """GAP-9: querying 'AGE' must NOT match a line that only contains 'CAGE'.
    The anchored pattern must reject a substring-of-word coincidence."""
    from scripts.ai_assistant.citations import _cite_in_study_config

    result = _cite_in_study_config("AGE")
    # 'AGE' appears inside 'CAGE' in the variable map, but the anchored pattern
    # must not match — no false citation.
    assert result is None


def test_cite_study_config_no_suffix_false_citation(
    variable_map_llm_source: Path,
) -> None:
    """GAP-9 (suffix case): querying 'AGE' must NOT match the unquoted longer
    column 'AGE_AT_ENROLL'. The unquoted-value branch needs an end-of-token
    boundary; without it 'column: AGE_AT_ENROLL' would falsely cite for 'AGE'."""
    from scripts.ai_assistant.citations import _cite_in_study_config

    assert _cite_in_study_config("AGE") is None


def test_cite_study_config_exact_column_resolves(
    variable_map_llm_source: Path,
) -> None:
    """GAP-9: an exact column name that IS a 'column:' value resolves correctly."""
    from scripts.ai_assistant.citations import _cite_in_study_config

    result = _cite_in_study_config("CAGE")
    assert result is not None
    assert result.source_kind == "study_config"
    assert result.matched_term == "CAGE"
    assert "CAGE" in result.snippet

    # The longer unquoted column still resolves when queried EXACTLY.
    enroll = _cite_in_study_config("AGE_AT_ENROLL")
    assert enroll is not None
    assert enroll.matched_term == "AGE_AT_ENROLL"
