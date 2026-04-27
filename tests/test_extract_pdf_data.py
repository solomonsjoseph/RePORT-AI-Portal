"""Tests for scripts.extraction.extract_pdf_data — PDF variable extraction.

Covers: discover_variable_jsons, load_variables_json, check_json_integrity,
validate_depends_on, clean_existing_jsons, and provider resolution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from scripts.extraction import extract_pdf_data as epd
from scripts.extraction.extract_pdf_data import (
    check_json_integrity,
    discover_variable_jsons,
    extract_pdfs_to_jsonl,
    load_variables_json,
)

# ═══════════════════════════════════════════════════════════════════════════
# discover_variable_jsons
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscoverVariableJsons:
    def test_finds_variable_jsons(self, tmp_path: Path):
        (tmp_path / "form_a_variables.json").write_text("{}")
        (tmp_path / "form_b_variables.json").write_text("{}")
        (tmp_path / "not_a_match.json").write_text("{}")
        result = discover_variable_jsons(tmp_path)
        assert len(result) == 2
        names = [p.name for p in result]
        assert "form_a_variables.json" in names
        assert "form_b_variables.json" in names

    def test_excludes_hidden_files(self, tmp_path: Path):
        (tmp_path / ".hidden_variables.json").write_text("{}")
        (tmp_path / "real_variables.json").write_text("{}")
        result = discover_variable_jsons(tmp_path)
        assert len(result) == 1
        assert result[0].name == "real_variables.json"

    def test_excludes_excel_lock_files(self, tmp_path: Path):
        (tmp_path / "~$temp_variables.json").write_text("{}")
        (tmp_path / "real_variables.json").write_text("{}")
        result = discover_variable_jsons(tmp_path)
        assert len(result) == 1

    def test_nonexistent_directory(self, tmp_path: Path):
        result = discover_variable_jsons(tmp_path / "nonexistent")
        assert result == []

    def test_sorted_order(self, tmp_path: Path):
        (tmp_path / "z_variables.json").write_text("{}")
        (tmp_path / "a_variables.json").write_text("{}")
        result = discover_variable_jsons(tmp_path)
        assert result[0].name == "a_variables.json"
        assert result[1].name == "z_variables.json"


# ═══════════════════════════════════════════════════════════════════════════
# load_variables_json
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadVariablesJson:
    def test_loads_valid_json(self, tmp_path: Path):
        data = {"form_name": "Test", "variables": {"A": {"description": "test"}}}
        path = tmp_path / "test_variables.json"
        path.write_text(json.dumps(data))
        result = load_variables_json(path)
        assert result["form_name"] == "Test"
        assert "A" in result["variables"]

    def test_invalid_json_raises(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not valid json{{{")
        with pytest.raises(json.JSONDecodeError):
            load_variables_json(path)


# ═══════════════════════════════════════════════════════════════════════════
# check_json_integrity
# ═══════════════════════════════════════════════════════════════════════════


class TestCheckJsonIntegrity:
    def test_valid_file(self, tmp_path: Path):
        path = tmp_path / "good.json"
        path.write_text(
            json.dumps({"form_name": "Test", "variables": {"SUBJID": {"type": "text"}}})
        )
        assert check_json_integrity(path) is True

    def test_missing_file(self, tmp_path: Path):
        assert check_json_integrity(tmp_path / "missing.json") is False

    def test_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("")
        assert check_json_integrity(path) is False

    def test_non_dict_json(self, tmp_path: Path):
        path = tmp_path / "array.json"
        path.write_text("[1, 2, 3]")
        assert check_json_integrity(path) is False


# ═══════════════════════════════════════════════════════════════════════════
# extract_pdfs_to_jsonl — staging default (Task 2)
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractPdfsToJsonlDefaultOutput:
    """extract_pdfs_to_jsonl defaults output to config.STAGING_PDFS_DIR.

    We avoid real LLM calls by stashing a dummy .pdf and monkey-patching
    ``_resolve_pdf_provider`` to raise ``ImportError``. ``dest_dir.mkdir``
    runs before the provider call, so we can assert on the staging path
    getting created even though extraction fails.
    """

    def test_default_output_is_staging(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,  # side-effect: patches config paths to tmp
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_src = tmp_path / "annotated_pdfs"
        pdf_src.mkdir()
        # Minimal byte blob — the test never parses it because the provider
        # init fails before any LLM call.
        (pdf_src / "form_a.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")

        def _boom() -> None:  # pragma: no cover — signature matches tuple return
            raise ImportError("no provider in tests")

        monkeypatch.setattr(epd, "_resolve_pdf_provider", _boom)

        # No output_dir → default must resolve to STAGING_PDFS_DIR
        result = extract_pdfs_to_jsonl(pdf_dir=pdf_src)

        assert config.STAGING_PDFS_DIR.exists(), (
            "default staging PDFs dir must have been created (mkdir before provider init)"
        )
        # Provider failure recorded as error
        errors = result.get("errors", [])
        assert errors and "Failed to initialize LLM client" in errors[0]["error"]
        # No files written into trio_bundle/pdfs
        assert not list(config.PDF_EXTRACTIONS_DIR.glob("*.json")), (
            "staging rewrite must not write to trio_bundle anymore"
        )

    def test_explicit_output_overrides_default(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,  # side-effect: patches config paths to tmp
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_src = tmp_path / "annotated_pdfs2"
        pdf_src.mkdir()
        (pdf_src / "form_b.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")

        explicit = tmp_path / "explicit_pdf_out"

        def _boom() -> None:
            raise ImportError("no provider in tests")

        monkeypatch.setattr(epd, "_resolve_pdf_provider", _boom)

        extract_pdfs_to_jsonl(pdf_dir=pdf_src, output_dir=explicit)
        # Explicit path must have been used (mkdir runs before provider init).
        assert explicit.exists()
        # Staging must NOT have been used when caller is explicit.
        assert not config.STAGING_PDFS_DIR.exists()
