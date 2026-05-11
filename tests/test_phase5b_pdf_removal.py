"""Phase 5b Task 4d regression tests — dead PDF extraction code removed.

Pins the contract that the LLM-based PDF extraction pipeline and the
agent-side ``search_pdf_context`` tool have been fully retired now that
the study catalog supersedes per-PDF context.
"""

from __future__ import annotations

import pytest


def test_pdf_extraction_module_removed() -> None:
    """extract_pdf_data module must be deleted after Phase 5b Task 4d."""
    with pytest.raises(ImportError):
        from scripts.extraction import extract_pdf_data  # noqa: F401


def test_pdf_pipeline_module_removed() -> None:
    """pdf_pipeline module must be deleted after Phase 5b Task 4d."""
    with pytest.raises(ImportError):
        from scripts.extraction import pdf_pipeline  # noqa: F401


def test_extract_pdfs_to_jsonl_export_removed() -> None:
    """extract_pdfs_to_jsonl must no longer be exported from scripts.extraction."""
    import scripts.extraction as ext

    assert not hasattr(ext, "extract_pdfs_to_jsonl"), (
        "extract_pdfs_to_jsonl should be removed from scripts.extraction"
    )


def test_pdf_extractions_dir_constant_removed() -> None:
    """PDF_EXTRACTIONS_DIR config constant must be removed."""
    import config

    assert not hasattr(config, "PDF_EXTRACTIONS_DIR")


def test_temp_prefix_trio_bundle_removed() -> None:
    """TEMP_PREFIX_TRIO_BUNDLE config constant must be removed."""
    import config

    assert not hasattr(config, "TEMP_PREFIX_TRIO_BUNDLE")


def test_pdf_extraction_env_constants_removed() -> None:
    """PDF_EXTRACTION_* config constants must be removed."""
    import config

    assert not hasattr(config, "PDF_EXTRACTION_INTER_DELAY")
    assert not hasattr(config, "PDF_EXTRACTION_MAX_TOKENS")


def test_pdf_context_snippets_removed() -> None:
    """_pdf_context_snippets must be deleted from agent_tools."""
    from scripts.ai_assistant import agent_tools

    assert not hasattr(agent_tools, "_pdf_context_snippets")


def test_search_pdf_context_removed() -> None:
    """search_pdf_context tool must be deleted from agent_tools."""
    from scripts.ai_assistant import agent_tools

    assert not hasattr(agent_tools, "search_pdf_context")


def test_llm_capabilities_module_removed() -> None:
    """scripts.utils.llm_capabilities must be deleted (only PDF consumed it)."""
    with pytest.raises(ImportError):
        from scripts.utils import llm_capabilities  # noqa: F401
