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


# ═══════════════════════════════════════════════════════════════════════════
# extract_pdfs_to_jsonl — REPORTALIN_PDF_EXTRACTION_MODE dispatch (PR #16)
# ═══════════════════════════════════════════════════════════════════════════


class TestPdfExtractionModeDispatch:
    """The wizard's PDF-extraction-source radio sets
    ``REPORTALIN_PDF_EXTRACTION_MODE`` to ``llm`` or ``snapshot``;
    ``extract_pdfs_to_jsonl`` dispatches to the matching helper.

    Both helpers must NEVER call ``_resolve_pdf_provider`` (it lives on
    the legacy raw-PDF API path; it has its own two-part PHI-free
    attestation gate that the new modes intentionally bypass — the
    orchestrator redacts text before any byte leaves the host, the
    snapshot mode never makes an LLM call at all).
    """

    def _seed_snapshot(self, monkeypatch_config: Path, stem: str, payload: dict) -> Path:
        # PR #18 relocated the baseline to ``snapshots/{STUDY}/pdfs/``
        # (tracked, repo-root). The test conftest patches
        # ``config.STUDY_SNAPSHOTS_DIR`` to a tmp_path-anchored location,
        # so we read it from there rather than hardcoding the layout.
        snap_dir = Path(config.STUDY_SNAPSHOTS_DIR) / "pdfs"
        snap_dir.mkdir(parents=True, exist_ok=True)
        out = snap_dir / f"{stem}_variables.json"
        out.write_text(json.dumps(payload), encoding="utf-8")
        return out

    def test_snapshot_mode_publishes_baseline_verbatim(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_src = tmp_path / "annotated_pdfs"
        pdf_src.mkdir()
        (pdf_src / "form_x.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")

        self._seed_snapshot(
            monkeypatch_config,
            "form_x",
            {
                "form_name": "Form X",
                "source_pdf": "form_x.pdf",
                "variables": {"AAA": {"description": "alpha"}},
            },
        )

        # Provider must NOT be touched in snapshot mode.
        def _boom() -> None:
            raise AssertionError("_resolve_pdf_provider must not be invoked when mode=snapshot")

        monkeypatch.setattr(epd, "_resolve_pdf_provider", _boom)
        monkeypatch.setenv("REPORTALIN_PDF_EXTRACTION_MODE", "snapshot")

        result = extract_pdfs_to_jsonl(pdf_dir=pdf_src)

        assert result["errors"] == [], result["errors"]
        assert result["files_created"] == 1
        out = config.STAGING_PDFS_DIR / "form_x_variables.json"
        assert out.is_file()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["variables"] == {"AAA": {"description": "alpha"}}
        # Tier marker is stamped onto the published JSON for traceability.
        assert data.get("extraction_tier") == "snapshot"

    def test_snapshot_mode_records_error_on_missing_baseline(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_src = tmp_path / "annotated_pdfs"
        pdf_src.mkdir()
        (pdf_src / "form_missing.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")
        # Snapshot dir exists but is empty for this PDF.
        (Path(config.STUDY_SNAPSHOTS_DIR) / "pdfs").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            epd,
            "_resolve_pdf_provider",
            lambda: (_ for _ in ()).throw(AssertionError("provider must not be called")),
        )
        monkeypatch.setenv("REPORTALIN_PDF_EXTRACTION_MODE", "snapshot")

        result = extract_pdfs_to_jsonl(pdf_dir=pdf_src)

        assert result["files_created"] == 0
        errors = result["errors"]
        assert errors and "No snapshot for form_missing.pdf" in errors[0]["error"]

    def test_llm_mode_delegates_to_orchestrator(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pdf_src = tmp_path / "annotated_pdfs"
        pdf_src.mkdir()
        (pdf_src / "form_y.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")

        # Stub the orchestrator so we don't fire a real LLM call. The dispatch
        # contract is what we're pinning here.
        from scripts.extraction import pdf_pipeline as pp

        captured: dict[str, object] = {}

        def _fake_extract(pdf_path: Path, **kw: object):  # type: ignore[no-untyped-def]
            captured["pdf_path"] = pdf_path
            captured.update(kw)
            return pp.ExtractionResult(
                pdf_name=pdf_path.name,
                tier="merged",
                data={
                    "form_name": "Form Y",
                    "source_pdf": pdf_path.name,
                    "variables": {"BBB": {"description": "beta"}},
                    "extraction_tier": "merged",
                },
                llm_succeeded=True,
                code_succeeded=True,
            )

        monkeypatch.setattr(pp, "extract_pdf", _fake_extract)
        monkeypatch.setattr(
            epd,
            "_resolve_pdf_provider",
            lambda: (_ for _ in ()).throw(AssertionError("provider must not be called")),
        )

        # Wizard subprocess env injection emulation.
        monkeypatch.setenv("REPORTALIN_PDF_EXTRACTION_MODE", "llm")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        result = extract_pdfs_to_jsonl(pdf_dir=pdf_src)

        assert result["errors"] == [], result["errors"]
        assert result["files_created"] == 1
        # Credentials must have been forwarded to the orchestrator.
        assert captured.get("provider") == "anthropic"
        assert captured.get("model") == "claude-opus-4-7"
        assert captured.get("api_key") == "sk-ant-test"
        # Cache + snapshot dirs are forwarded so the orchestrator can use them.
        assert captured.get("cache_dir") is not None
        # Output written verbatim from orchestrator's `data` field.
        out = config.STAGING_PDFS_DIR / "form_y_variables.json"
        assert out.is_file()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["variables"] == {"BBB": {"description": "beta"}}

    def test_unset_mode_falls_back_to_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the env var is unset, ``_resolve_pdf_provider`` must still
        be the entry point (preserves CLI back-compat for users not running
        the wizard)."""
        pdf_src = tmp_path / "annotated_pdfs"
        pdf_src.mkdir()
        (pdf_src / "legacy.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")

        sentinel = {"called": False}

        def _boom() -> None:
            sentinel["called"] = True
            raise ImportError("no provider in tests")

        monkeypatch.setattr(epd, "_resolve_pdf_provider", _boom)
        monkeypatch.delenv("REPORTALIN_PDF_EXTRACTION_MODE", raising=False)

        extract_pdfs_to_jsonl(pdf_dir=pdf_src)
        assert sentinel["called"] is True

    def test_unrecognised_mode_falls_back_to_legacy_path(
        self,
        tmp_path: Path,
        monkeypatch_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A garbage value behaves like ``unset`` rather than crashing —
        avoids surprising operators who fat-finger the env var."""
        pdf_src = tmp_path / "annotated_pdfs"
        pdf_src.mkdir()
        (pdf_src / "garbage.pdf").write_bytes(b"%PDF-1.4\n%EOF\n")

        sentinel = {"called": False}

        def _boom() -> None:
            sentinel["called"] = True
            raise ImportError("no provider in tests")

        monkeypatch.setattr(epd, "_resolve_pdf_provider", _boom)
        monkeypatch.setenv("REPORTALIN_PDF_EXTRACTION_MODE", "wibble")

        extract_pdfs_to_jsonl(pdf_dir=pdf_src)
        assert sentinel["called"] is True
