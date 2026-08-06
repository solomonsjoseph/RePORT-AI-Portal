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
        json.dumps({"columns": [{"name": "SUBJID"}, {"name": "AE_AGE"}, {"name": "AE_EVENT"}]}, indent=2),
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
