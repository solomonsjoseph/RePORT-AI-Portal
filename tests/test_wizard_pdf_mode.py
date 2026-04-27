"""Tests for wizard PDF-extraction mode plumbing (PR #16).

Pins the contract that:

1. ``run_pipeline()`` propagates ``st.session_state["pdf_extraction_mode"]``
   into the spawned subprocess via ``REPORTALIN_PDF_EXTRACTION_MODE`` —
   so the operator's radio choice in step 2 actually reaches
   ``scripts.extraction.extract_pdf_data.extract_pdfs_to_jsonl``.
2. ``_resolve_pdf_mode_options()`` only offers the ``llm`` option when
   the configured model passes the capability allowlist — non-capable
   selections see ``snapshot`` only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ── run_pipeline env injection ──────────────────────────────────────────────


def _stub_subprocess_run(captured: dict[str, Any]) -> MagicMock:
    """Return a ``subprocess.run`` mock that captures the passed env."""

    def _fake_run(*args: Any, **kw: Any) -> MagicMock:
        captured["args"] = args
        captured["env"] = kw.get("env")
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    return MagicMock(side_effect=_fake_run)


@pytest.mark.parametrize("mode", ["snapshot", "llm"])
def test_run_pipeline_propagates_mode(
    mode: str,
    tmp_path: Path,
    monkeypatch_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wizard's mode radio selection must reach the subprocess as
    ``REPORTALIN_PDF_EXTRACTION_MODE``."""
    from scripts.ai_assistant.ui import wizard

    # Stub PHI key bootstrap (no real disk I/O).
    monkeypatch.setattr(wizard, "_ensure_phi_key", lambda: None)

    # Stub session_state with the chosen mode. dict-like is enough.
    fake_ss: dict[str, Any] = {"pdf_extraction_mode": mode}
    monkeypatch.setattr(wizard.st, "session_state", fake_ss)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(wizard.subprocess, "run", _stub_subprocess_run(captured))

    out = wizard.run_pipeline()
    assert out["success"] is True

    env = captured["env"]
    assert env is not None
    assert env.get("REPORTALIN_PDF_EXTRACTION_MODE") == mode


def test_run_pipeline_defaults_unset_to_snapshot(
    tmp_path: Path,
    monkeypatch_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If session_state lacks the mode (e.g. user skipped wizard), the
    env var defaults to ``snapshot`` — never empty / never invalid."""
    from scripts.ai_assistant.ui import wizard

    monkeypatch.delenv("REPORTALIN_PDF_EXTRACTION_MODE", raising=False)
    monkeypatch.setattr(wizard, "_ensure_phi_key", lambda: None)
    fake_ss: dict[str, Any] = {}
    monkeypatch.setattr(wizard.st, "session_state", fake_ss)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(wizard.subprocess, "run", _stub_subprocess_run(captured))

    wizard.run_pipeline()
    env = captured["env"]
    assert env.get("REPORTALIN_PDF_EXTRACTION_MODE") == "snapshot"


def test_run_pipeline_drops_unrecognised_mode(
    tmp_path: Path,
    monkeypatch_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surprise value (somehow stuck in session_state) must NOT be
    forwarded — even if ``REPORTALIN_PDF_EXTRACTION_MODE`` is present
    in the parent env. The wizard must not echo arbitrary garbage; the
    legacy CLI path runs in the subprocess instead."""
    from scripts.ai_assistant.ui import wizard

    # Pre-existing parent-env value should be preserved as the fallback —
    # the wizard's job is to never *introduce* an unrecognised mode.
    monkeypatch.setenv("REPORTALIN_PDF_EXTRACTION_MODE", "snapshot")
    monkeypatch.setattr(wizard, "_ensure_phi_key", lambda: None)
    fake_ss: dict[str, Any] = {"pdf_extraction_mode": "WIBBLE"}
    monkeypatch.setattr(wizard.st, "session_state", fake_ss)

    captured: dict[str, Any] = {}
    monkeypatch.setattr(wizard.subprocess, "run", _stub_subprocess_run(captured))

    wizard.run_pipeline()
    env = captured["env"]
    # Pre-existing parent-env value preserved; never echoes the garbage.
    assert env.get("REPORTALIN_PDF_EXTRACTION_MODE") == "snapshot"
    assert env.get("REPORTALIN_PDF_EXTRACTION_MODE") not in {"WIBBLE", "wibble"}


# ── _resolve_pdf_mode_options gating ────────────────────────────────────────


def _set_session_state(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:
    """Patch wizard.st.session_state with the supplied keys."""
    from scripts.ai_assistant.ui import wizard

    monkeypatch.setattr(wizard.st, "session_state", dict(kw))


def test_mode_options_capable_model_offers_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.ai_assistant.ui import wizard

    _set_session_state(
        monkeypatch,
        llm_provider_label="Anthropic",
        llm_model="claude-opus-4-7",
    )
    options, capable, prov, model = wizard._resolve_pdf_mode_options()
    assert "snapshot" in options
    assert "llm" in options
    assert capable is True
    assert prov == "anthropic"
    assert model == "claude-opus-4-7"


def test_mode_options_non_capable_model_hides_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.ai_assistant.ui import wizard

    _set_session_state(
        monkeypatch,
        llm_provider_label="Anthropic",
        llm_model="claude-haiku-3",  # not on the capable allowlist
    )
    options, capable, _prov, _model = wizard._resolve_pdf_mode_options()
    assert options == ["snapshot"]
    assert capable is False


def test_mode_options_openai_capable_but_unsupported_by_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI gpt-5 IS on the capability allowlist, but
    ``pdf_pipeline._extract_via_llm`` only wires anthropic + google —
    so the wizard must hide the ``llm`` option to prevent the operator
    from picking it and silently getting a snapshot fallback."""
    from scripts.ai_assistant.ui import wizard

    _set_session_state(
        monkeypatch,
        llm_provider_label="OpenAI",
        llm_model="gpt-5",
    )
    options, capable, prov, _model = wizard._resolve_pdf_mode_options()
    assert options == ["snapshot"]
    assert capable is False
    assert prov == "openai"


def test_mode_options_nvidia_capable_but_unsupported_by_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same gate for NVIDIA-hosted Llama 3.3 405B."""
    from scripts.ai_assistant.ui import wizard

    _set_session_state(
        monkeypatch,
        llm_provider_label="NVIDIA AI Endpoints",
        llm_model="meta/llama-3.3-405b-instruct",
    )
    options, capable, prov, _model = wizard._resolve_pdf_mode_options()
    assert options == ["snapshot"]
    assert capable is False
    assert prov == "nvidia-ai-endpoints"


def test_mode_options_ollama_excluded_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama is gated off the LLM extraction tier unless the operator
    explicitly opts in via the env override (matches
    ``llm_capabilities`` defaults)."""
    from scripts.ai_assistant.ui import wizard

    monkeypatch.delenv("REPORTALIN_PDF_LLM_CAPABLE_MODELS", raising=False)
    _set_session_state(
        monkeypatch,
        llm_provider_label="Ollama (local)",
        llm_model="qwen3:32b",
    )
    options, capable, _prov, _model = wizard._resolve_pdf_mode_options()
    assert options == ["snapshot"]
    assert capable is False


# ── _PDF_MODE_LABELS sanity ─────────────────────────────────────────────────


def test_mode_labels_cover_all_modes() -> None:
    """Every mode the dispatcher accepts has a human-readable label so
    the wizard's radio doesn't silently render a raw constant."""
    from scripts.ai_assistant.ui.wizard import _PDF_MODE_LABELS

    assert {"snapshot", "llm"} <= set(_PDF_MODE_LABELS.keys())
    for label in _PDF_MODE_LABELS.values():
        assert label and label != ""
