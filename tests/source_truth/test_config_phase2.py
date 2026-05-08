"""Phase 2 config constants must export new llm_source layout paths and size thresholds."""

from __future__ import annotations

from pathlib import Path

import config


def test_dataset_schema_files_dir() -> None:
    p = config.LLM_SOURCE_DATASET_SCHEMA_FILES_DIR
    assert isinstance(p, Path)
    assert p.parent.name == "dataset_schema"
    assert p.parent.parent == config.STUDY_LLM_SOURCE_DIR


def test_dataset_schema_catalog_path() -> None:
    p = config.LLM_SOURCE_DATASET_SCHEMA_CATALOG_PATH
    assert isinstance(p, Path)
    assert p.name == "catalog.json"
    assert p.parent == config.STUDY_LLM_SOURCE_DIR / "dataset_schema"


def test_dictionary_catalog_path() -> None:
    p = config.LLM_SOURCE_DICTIONARY_CATALOG_PATH
    assert isinstance(p, Path)
    assert p.name == "catalog.json"
    assert p.parent == config.LLM_SOURCE_DICTIONARY_MAPPING_DIR


def test_llm_source_dictionary_mapping_dir() -> None:
    p = config.LLM_SOURCE_DICTIONARY_MAPPING_DIR
    assert isinstance(p, Path)
    assert p.name == "dictionary_mapping"
    assert p.parent == config.STUDY_LLM_SOURCE_DIR


def test_llm_source_dictionary_mapping_jsonl_dir() -> None:
    p = config.LLM_SOURCE_DICTIONARY_MAPPING_JSONL_DIR
    assert isinstance(p, Path)
    assert p.name == "jsonl"
    assert p.parent == config.LLM_SOURCE_DICTIONARY_MAPPING_DIR


def test_evidence_packs_dir() -> None:
    p = config.LLM_SOURCE_EVIDENCE_PACKS_DIR
    assert isinstance(p, Path)
    assert p.name == "evidence_packs"
    assert p.parent == config.STUDY_LLM_SOURCE_DIR


def test_concept_dir() -> None:
    p = config.LLM_SOURCE_CONCEPT_DIR
    assert isinstance(p, Path)
    assert p.name == "concept"
    assert p.parent == config.STUDY_LLM_SOURCE_DIR


def test_size_threshold_constants() -> None:
    assert config.LEAN_CATALOG_DICTIONARY_MAX_BYTES == 20 * 1024
    assert config.LEAN_CATALOG_DATASET_SCHEMA_MAX_BYTES == 50 * 1024
    assert config.LEAN_CATALOG_STUDY_METADATA_MAX_BYTES == 200 * 1024
