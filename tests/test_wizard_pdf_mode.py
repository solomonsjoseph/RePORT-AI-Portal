"""Tests for the wizard's two-button study-load flow (PR #18 rewrite).

PR #18 replaced PR #16's PDF-extraction-mode radio with a higher-level
two-button choice in step 2: *Use Existing Study* (skip the pipeline,
trust ``output/{STUDY}/trio_bundle/``) vs *Load Study* (run the
pipeline subprocess, with the repo-tracked snapshot baseline at
``snapshots/{STUDY}/pdfs/`` as the per-PDF fallback when the LLM
tier is unavailable).

This file pins the contract that ``run_pipeline()`` propagates the
right env signal to the subprocess, and that the orchestrator-mode
gate is no longer surfaced to the operator at the per-PDF level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


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


# ── run_pipeline env injection ──────────────────────────────────────────────


def test_run_pipeline_always_signals_llm_mode(
    tmp_path: Path,
    monkeypatch_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subprocess always sees ``REPORTALIN_PDF_EXTRACTION_MODE=llm``
    so the dispatcher chooses the orchestrator (which has its own
    per-PDF snapshot fallback). The pre-PR-#18 radio that let operators
    pick ``snapshot`` directly is gone — the user never makes that
    choice at the per-PDF level any more."""
    from scripts.ai_assistant.ui import wizard

    monkeypatch.setattr(wizard, "_ensure_phi_key", lambda: None)
    monkeypatch.setattr(wizard.st, "session_state", {})

    captured: dict[str, Any] = {}
    monkeypatch.setattr(wizard.subprocess, "run", _stub_subprocess_run(captured))

    out = wizard.run_pipeline()
    assert out["success"] is True

    env = captured["env"]
    assert env is not None
    assert env.get("REPORTALIN_PDF_EXTRACTION_MODE") == "llm"


def test_run_pipeline_does_not_leak_session_state_extras(
    tmp_path: Path,
    monkeypatch_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale leftover keys in ``st.session_state`` from older sessions
    must not bleed into the subprocess env. Only the explicit
    ``REPORTALIN_PDF_EXTRACTION_MODE=llm`` signal is set."""
    from scripts.ai_assistant.ui import wizard

    monkeypatch.setattr(wizard, "_ensure_phi_key", lambda: None)
    # Pre-existing PR-#16-era key shouldn't change behaviour.
    monkeypatch.setattr(wizard.st, "session_state", {"pdf_extraction_mode": "snapshot"})

    captured: dict[str, Any] = {}
    monkeypatch.setattr(wizard.subprocess, "run", _stub_subprocess_run(captured))

    wizard.run_pipeline()
    env = captured["env"]
    # PR #18 always uses orchestrator mode regardless of stale session-state.
    assert env.get("REPORTALIN_PDF_EXTRACTION_MODE") == "llm"


# ── Wizard module surface ───────────────────────────────────────────────────


def test_pr18_removed_pdf_mode_helpers() -> None:
    """The PR #16 helpers (``_resolve_pdf_mode_options``, ``_PDF_MODE_LABELS``)
    must be gone. Their presence in newer code would mean the radio was
    re-added, contradicting the two-button rewrite."""
    from scripts.ai_assistant.ui import wizard

    assert not hasattr(wizard, "_resolve_pdf_mode_options"), (
        "PR #16 helper survived; the wizard rewrite was incomplete"
    )
    assert not hasattr(wizard, "_PDF_MODE_LABELS"), (
        "PR #16 label map survived; the wizard rewrite was incomplete"
    )


def test_run_pipeline_export_still_present() -> None:
    """The Streamlit step-2 buttons both call ``wizard.run_pipeline``
    (Load Study) or set ``pipeline_ready`` directly (Use Existing Study).
    ``run_pipeline`` must remain a public symbol of the module."""
    from scripts.ai_assistant.ui import wizard

    assert callable(wizard.run_pipeline)
