"""Tests for chat UI published-bundle readiness detection."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.ai_assistant.ui.bundle_status import bundle_readiness_issues, published_bundle_exists


def _point_bundle_config(monkeypatch: pytest.MonkeyPatch, llm_source: Path) -> None:
    raw_root = llm_source.parents[2] / "data" / "raw" / "Study"
    monkeypatch.setattr(config, "STUDY_LLM_SOURCE_DIR", llm_source)
    monkeypatch.setattr(config, "TRIO_DATASETS_DIR", llm_source / "dataset_schema" / "files")
    monkeypatch.setattr(
        config,
        "DICTIONARY_JSON_OUTPUT_DIR",
        llm_source / "dictionary_mapping" / "jsonl",
    )
    monkeypatch.setattr(config, "DATA_DICTIONARY_DIR", raw_root / "data_dictionary")
    monkeypatch.setattr(config, "LLM_SOURCE_SOT_DIR", llm_source / "SoT")
    monkeypatch.setattr(
        config,
        "LLM_SOURCE_LEGACY_SOURCE_TRUTH_DIR",
        llm_source / "source_truth",
    )


def test_published_bundle_requires_dataset_jsonl_and_plugin_sot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    datasets = config.TRIO_DATASETS_DIR
    policy_dir = config.LLM_SOURCE_SOT_DIR / "6_HIV" / "pdf"
    datasets.mkdir(parents=True)
    policy_dir.mkdir(parents=True)
    (datasets / "6_HIV.jsonl").write_text('{"_metadata": true}\n', encoding="utf-8")
    (policy_dir / "6_HIV_policy.yaml").write_text("variables: {}\n", encoding="utf-8")

    assert published_bundle_exists() is True


def test_published_bundle_requires_dictionary_mapping_when_raw_dictionary_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    policy_dir = config.LLM_SOURCE_SOT_DIR / "6_HIV" / "pdf"
    policy_dir.mkdir(parents=True)
    config.DATA_DICTIONARY_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    (policy_dir / "6_HIV_policy.yaml").write_text("variables: {}\n", encoding="utf-8")
    (config.DATA_DICTIONARY_DIR / "dictionary.csv").write_text(
        "variable,label\nHIV_HIV,HIV result\n",
        encoding="utf-8",
    )

    assert published_bundle_exists() is False
    assert any("dictionary mapping JSONL" in issue for issue in bundle_readiness_issues())


def test_published_bundle_accepts_dictionary_mapping_when_raw_dictionary_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    policy_dir = config.LLM_SOURCE_SOT_DIR / "6_HIV" / "pdf"
    policy_dir.mkdir(parents=True)
    config.DATA_DICTIONARY_DIR.mkdir(parents=True)
    config.DICTIONARY_JSON_OUTPUT_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    (policy_dir / "6_HIV_policy.yaml").write_text("variables: {}\n", encoding="utf-8")
    (config.DATA_DICTIONARY_DIR / "dictionary.csv").write_text(
        "variable,label\nHIV_HIV,HIV result\n",
        encoding="utf-8",
    )
    (config.DICTIONARY_JSON_OUTPUT_DIR / "dictionary.jsonl").write_text(
        '{"variable": "HIV_HIV"}\n',
        encoding="utf-8",
    )

    assert published_bundle_exists() is True


def test_published_bundle_rejects_dataset_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )

    assert published_bundle_exists() is False


def test_published_bundle_accepts_legacy_source_truth_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm_source = tmp_path / "output" / "Study" / "llm_source"
    _point_bundle_config(monkeypatch, llm_source)

    config.TRIO_DATASETS_DIR.mkdir(parents=True)
    legacy_dir = config.LLM_SOURCE_LEGACY_SOURCE_TRUTH_DIR
    legacy_dir.mkdir(parents=True)
    (config.TRIO_DATASETS_DIR / "6_HIV.jsonl").write_text(
        '{"_metadata": true}\n',
        encoding="utf-8",
    )
    (legacy_dir / "6_HIV_policy.lean.yaml").write_text("variables: {}\n", encoding="utf-8")

    assert published_bundle_exists() is True
