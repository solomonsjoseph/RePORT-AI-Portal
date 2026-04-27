"""Tests for the PDF PHI-safety two-part gate (env flag + attestation note)."""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from scripts.extraction import extract_pdf_data


class TestPDFPHIFreeOptIn:
    def test_false_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPORTALIN_PDF_PHI_FREE", raising=False)
        assert extract_pdf_data._pdf_phi_free_opt_in() is False

    def test_true_for_accepted_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("1", "true", "TRUE", "yes", "YES", "on"):
            monkeypatch.setenv("REPORTALIN_PDF_PHI_FREE", value)
            assert extract_pdf_data._pdf_phi_free_opt_in() is True, value

    def test_false_for_unrecognized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("0", "false", "", "maybe"):
            monkeypatch.setenv("REPORTALIN_PDF_PHI_FREE", value)
            assert extract_pdf_data._pdf_phi_free_opt_in() is False, value


class TestPDFPHIFreeAuthority:
    def test_absent_when_file_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        assert extract_pdf_data._pdf_phi_free_authority_present() is False

    def test_absent_when_file_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        (tmp_path / "authorities").mkdir()
        (tmp_path / "authorities" / "phi_free_pdfs.md").write_text("")
        assert extract_pdf_data._pdf_phi_free_authority_present() is False

    def test_present_when_file_has_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        (tmp_path / "authorities").mkdir()
        (tmp_path / "authorities" / "phi_free_pdfs.md").write_text(
            "Reviewed by: operator\nDeclaration: verified PHI-free.\n"
        )
        assert extract_pdf_data._pdf_phi_free_authority_present() is True


class TestResolvePDFProviderGate:
    def test_refuses_without_env_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPORTALIN_PDF_PHI_FREE", raising=False)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-20250514")
        with pytest.raises(ValueError, match="PDF extraction via external LLM API refused"):
            extract_pdf_data._resolve_pdf_provider()

    def test_refuses_with_flag_but_no_authority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Env flag set, but attestation note missing — must still refuse.
        monkeypatch.setenv("REPORTALIN_PDF_PHI_FREE", "1")
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-20250514")
        with pytest.raises(ValueError, match=r"attestation note at .* is missing or empty"):
            extract_pdf_data._resolve_pdf_provider()

    def test_refuses_with_flag_but_empty_authority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTALIN_PDF_PHI_FREE", "1")
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        (tmp_path / "authorities").mkdir()
        (tmp_path / "authorities" / "phi_free_pdfs.md").write_text("")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-20250514")
        with pytest.raises(ValueError, match=r"attestation note at .* is missing or empty"):
            extract_pdf_data._resolve_pdf_provider()

    def test_proceeds_with_flag_and_authority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Full happy path for the two-part gate. After both conditions
        # hold, the resolver moves past the gate; it will then try to
        # initialise the provider client, which will fail with a
        # provider-specific ImportError when the anthropic package is
        # missing in the test env. We assert the error is NOT the
        # gate refusal — i.e. the gate let us through.
        monkeypatch.setenv("REPORTALIN_PDF_PHI_FREE", "1")
        monkeypatch.setattr(config, "BASE_DIR", tmp_path)
        (tmp_path / "authorities").mkdir()
        (tmp_path / "authorities" / "phi_free_pdfs.md").write_text(
            "Reviewed by: operator\nDeclaration: verified PHI-free.\n"
        )
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-20250514")
        # The gate should pass; any downstream error must not be the
        # gate-refusal message.
        try:
            extract_pdf_data._resolve_pdf_provider()
        except ValueError as exc:
            assert "refused" not in str(exc), (
                f"Gate should have allowed this call through; got refusal: {exc}"
            )
        except ImportError:
            # Expected when the anthropic package is not installed — the
            # gate has passed at this point.
            pass
