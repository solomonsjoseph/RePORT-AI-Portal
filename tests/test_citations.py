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


def _write_joined_view(form_dir: Path, form_name: str, variables: list[str]) -> None:
    joined = form_dir / "joined"
    joined.mkdir(parents=True, exist_ok=True)
    lines = ["study: TestStudy", f"form: {form_name}", "variables:"]
    for var in variables:
        lines.append(f"  {var}:")
        lines.append("    pdf:")
        lines.append("      question: synthetic")
    (joined / f"{form_name}_joined_query_view.yaml").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
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

    # Form 95_SAE: joined view + jsonl (header-only)
    sae = sot / "95_SAE"
    _write_joined_view(sae, "95_SAE", ["SUBJID", "AE_AGE"])
    (files / "95_SAE.jsonl").write_text(
        json.dumps({"SUBJID": None, "AE_AGE": None, "AE_ONLYINJSONL": None}) + "\n",
        encoding="utf-8",
    )

    # Form 98A_FOA: joined view only — exercises prefix resolution "98A"
    foa = sot / "98A_FOA"
    _write_joined_view(foa, "98A_FOA", ["FOA_COHAOUT"])

    (meta / "study_variable_map.yaml").write_text(
        'cohorts:\n  cohort_a:\n    demographics:\n      sex:\n        column: "IS_SEX"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "BASE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_SOT_DIR", sot, raising=False)
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", files, raising=False)
    monkeypatch.setattr(config, "LLM_SOURCE_STUDY_METADATA_DIR", meta, raising=False)
    return root


def test_joined_view_wins_precedence(llm_source: Path) -> None:
    """A field in the joined query view cites joined_query_view, not jsonl."""
    c = cite_variable("95_SAE", "AE_AGE")
    assert isinstance(c, Citation)
    assert c.source_kind == "joined_query_view"
    assert c.file.endswith("95_SAE_joined_query_view.yaml")
    assert c.line == 7  # 'AE_AGE:' line in the synthetic joined view
    assert c.matched_term == "AE_AGE"


def test_prefix_form_resolves_joined_view(llm_source: Path) -> None:
    """Prefix form_id '98A' resolves to 98A_FOA; field cites joined_query_view."""
    c = cite_variable("98A", "FOA_COHAOUT")
    assert c.source_kind == "joined_query_view"
    assert c.file.endswith("98A_FOA_joined_query_view.yaml")


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

    form_dir = sot / "99_Test"
    _write_joined_view(form_dir, "99_Test", ["CAGE"])

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
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", files, raising=False)
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
