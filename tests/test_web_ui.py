"""Regression tests for the Streamlit LLM setup wizard."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from streamlit.testing.v1 import AppTest

import config
from scripts.ai_assistant.ui.chat import _MODEL_DESCRIPTIONS, _pretty_model_label
from scripts.ai_assistant.ui.conversations import (
    _conversation_has_artifacts,
    _list_conversations_bucketed,
)
from scripts.ai_assistant.ui.providers import (
    _OTHER_MODEL_OPTION,
    _build_ollama_selector_state,
    _get_ollama_base_url,
    _is_ollama_chat_model,
    _ollama_models_match,
)
from scripts.ai_assistant.ui.shell import _TITLE_DISPLAY_LIMIT, _truncate_title
from scripts.ai_assistant.ui.streaming import (
    _MEMORY_KEYWORDS,
    _artifact_file_download,
    _sanitize_file_refs,
)
from scripts.ai_assistant.web_ui import (
    _CSS_PATH,
    _render_conv_title_dropdown,
    _render_export_submenu,
    _render_sidebar,
)

_PROJECT_ROOT = Path(__file__).parent.parent


def test_ollama_model_matching_treats_latest_as_implicit() -> None:
    assert _ollama_models_match("mistral", "mistral:latest")
    assert _ollama_models_match("mistral:latest", "mistral")


def test_ollama_base_url_prefers_documented_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "ollama.internal:11435/")
    monkeypatch.setenv("OLLAMA_HOST", "ignored.local:11434")

    assert _get_ollama_base_url() == "http://ollama.internal:11435"


def test_ollama_base_url_supports_legacy_host_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.example.test/")

    assert _get_ollama_base_url() == "https://ollama.example.test"


def test_embedding_models_are_hidden_from_chat_selector() -> None:
    assert not _is_ollama_chat_model("nomic-embed-text:latest")
    assert not _is_ollama_chat_model("bge-large:latest")
    assert _is_ollama_chat_model("mistral:latest")


def test_fallback_selector_prefers_existing_configured_model() -> None:
    selector = _build_ollama_selector_state(
        ["qwen3:8b", "mistral:latest", _OTHER_MODEL_OPTION],
        source="fallback",
        saved_model="mistral",
        configured_model="qwen3:8b",
        default_model="qwen3:8b",
    )

    assert selector["options"] == ["qwen3:8b", "mistral:latest", _OTHER_MODEL_OPTION]
    assert selector["index"] == 1
    assert selector["fallback_hint"] == "mistral:latest"


def test_live_selector_skips_embeddings_when_auto_picking() -> None:
    selector = _build_ollama_selector_state(
        ["nomic-embed-text:latest", "mistral:latest", _OTHER_MODEL_OPTION],
        source="api",
        saved_model="",
        configured_model="",
        default_model="qwen3:8b",
    )

    assert selector["options"] == ["mistral:latest", _OTHER_MODEL_OPTION]
    assert selector["index"] == 0
    assert selector["hidden_models"] == ["nomic-embed-text:latest"]


def test_model_pill_has_descriptions_for_curated_models() -> None:
    # Representative models from each supported provider should have curated
    # descriptions rendered in the Claude-style pill dropdown.
    assert "claude-opus-4-6" in _MODEL_DESCRIPTIONS
    assert "claude-sonnet-4-6" in _MODEL_DESCRIPTIONS
    assert "gpt-4.1" in _MODEL_DESCRIPTIONS
    assert "qwen3:8b" in _MODEL_DESCRIPTIONS


def test_pretty_model_label_shortens_known_families() -> None:
    assert _pretty_model_label("claude-opus-4-6") == "Opus 4.6"
    assert _pretty_model_label("claude-sonnet-4-6") == "Sonnet 4.6"
    assert _pretty_model_label("gpt-4.1") == "GPT-4.1"
    # Unknown / local model ids pass through unchanged.
    assert _pretty_model_label("qwen3:8b") == "qwen3:8b"


def test_theme_includes_hidden_end_chat_and_model_pill() -> None:
    css = _CSS_PATH.read_text(encoding="utf-8")
    bridge = _CSS_PATH.with_name("bridge.js").read_text(encoding="utf-8")
    chat_source = (_PROJECT_ROOT / "scripts/ai_assistant/ui/chat.py").read_text(encoding="utf-8")
    shell_source = (_PROJECT_ROOT / "scripts/ai_assistant/ui/shell.py").read_text(encoding="utf-8")
    web_ui_source = (_PROJECT_ROOT / "scripts/ai_assistant/web_ui.py").read_text(encoding="utf-8")
    wizard_source = (_PROJECT_ROOT / "scripts/ai_assistant/ui/wizard.py").read_text(
        encoding="utf-8"
    )
    streaming_source = (_PROJECT_ROOT / "scripts/ai_assistant/ui/streaming.py").read_text(
        encoding="utf-8"
    )
    assert ".st-key-rpln_end_chat_hidden" in css
    assert "rpln_composer_dock" in chat_source
    assert '[class*="st-key-rpln_composer_dock"]' in css
    assert "width: auto !important;" in css
    assert "max-width: none !important;" in css
    assert ':not([class*="st-key-FormSubmitter-rpln_composer_form"])' in css
    assert "max-width: 40px !important;" in css
    assert '[class*="st-key-rpln_composer_model"]' in css
    assert '.st-key-rpln_composer_model [data-testid="stPopoverButton"] svg' in css
    assert '.stElementContainer[class*="st-key-rpln_"]' not in css
    assert ".stElementContainer.st-key-rpln_open_search" in css
    assert ".stElementContainer.st-key-rpln_profile_settings_btn" in css
    assert ".stElementContainer.st-key-rpln_profile_logout_btn" in css
    assert ".rpln-submit-proxy" in css
    assert 'data-rpln-action="submit-composer"' in chat_source
    assert "submit-composer" in bridge
    assert "suppressPasswordManagerHints" in bridge
    assert 'data-1p-ignore", "true"' in bridge
    assert 'data-lpignore", "true"' in bridge
    assert 'autocomplete", "new-password"' in bridge
    assert "bindModelPillToComposer" in bridge
    assert "slot.appendChild(pill)" not in bridge
    assert "positionModelPopover" in bridge
    assert "triggerRect.right - menuWidth" in bridge
    assert "copy-code" in bridge
    assert ".rpln-code-card" in css
    assert "_render_analysis_code_cards" in streaming_source
    assert "rpln_artifact_download_" in streaming_source
    assert "st.download_button(" in streaming_source
    assert 'on_click="ignore"' in streaming_source
    assert 'st.empty() if st.session_state.get("rpln_pending_stream")' in chat_source
    assert "assistant_slot = chat.render_thread()" in web_ui_source
    assert "chat.composer(assistant_slot=assistant_slot)" in web_ui_source
    assert '[class*="st-key-rpln_ui_bridge_"]' in css
    assert 'key="rpln_ui_bridge_shell"' in shell_source
    assert 'key="rpln_ui_bridge_redesign"' in web_ui_source
    assert 'key="rpln_ui_bridge_wizard"' in wizard_source
    assert 'key="rpln_ui_bridge_streaming"' in streaming_source


def test_thread_shell_is_real_scrollport() -> None:
    css = _CSS_PATH.read_text(encoding="utf-8")
    assert '[class*="st-key-rpln_thread_shell"]' in css
    assert "min-height: 0 !important;" in css
    assert "max-height: calc(100vh - 64px) !important;" in css
    assert "scroll-padding-bottom: 220px;" in css


def test_theme_prunes_stale_pre_dock_composer_css() -> None:
    css = _CSS_PATH.read_text(encoding="utf-8")
    stale_selectors = (
        '[data-testid="stMain"]:has(.rpln-composer)',
        ".rpln-composer-wrap",
        ".rpln-composer-row",
        ".rpln-composer-left",
        ".rpln-composer-right",
        '.rpln-composer button[kind="formSubmit"]',
        ".rpln-model-pill",
        ".rpln-think {",
        ".rpln-think:hover",
        ".thinking-bar",
        "@keyframes rpln-bounce",
    )
    for selector in stale_selectors:
        assert selector not in css


def test_streaming_loader_keeps_label_and_morphs_submit_icon() -> None:
    css = _CSS_PATH.read_text(encoding="utf-8")
    chat_source = (_PROJECT_ROOT / "scripts/ai_assistant/ui/chat.py").read_text(encoding="utf-8")
    streaming_source = (_PROJECT_ROOT / "scripts/ai_assistant/ui/streaming.py").read_text(
        encoding="utf-8"
    )
    assert streaming_source.index("status = st.empty()") < streaming_source.index(
        "placeholder = st.empty()"
    )
    assert "Working on it..." in streaming_source
    assert 'aria-label="Working on it..."' in streaming_source
    assert "travel_explore" not in streaming_source
    assert ".rpln-thinking-dots {" in css
    assert ".rpln-thinking-dots span" in css
    assert "assistant_slot: Any | None = None" in chat_source
    assert "thread-local slot" in chat_source
    assert ".rpln-thinking span {" not in css
    assert "display: inline-flex !important;" in css
    assert "display: inline-block !important;" in css
    assert "visibility: visible !important;" in css
    assert "rpln-composer-streaming-sentinel" in css
    assert "cursor: wait !important;" in css
    assert "background-position: center !important;" in css
    assert "background-size: 16px 16px !important;" in css
    assert "width='11' height='11' rx='3.25'" in css
    assert "box-shadow: inset 0 0 0 1px rgba(244,239,232,0.06) !important;" in css
    assert (
        '[class*="st-key-rpln_composer_shell"]:has(.rpln-composer-streaming-sentinel)\n'
        '  [data-testid="stFormSubmitButton"] button::before'
    ) not in css
    assert "rpln-compose-spin" not in css


def test_topbar_title_overlay_hides_native_popover_text() -> None:
    css = _CSS_PATH.read_text(encoding="utf-8")
    assert ".rpln-topbar-title-overlay" in css
    assert (
        '[class*="st-key-rpln_topbar_title_menu"] '
        '[data-testid="stPopoverButton"] [data-testid="stMarkdownContainer"]'
    ) in css
    assert "color: transparent !important;" in css
    assert _TITLE_DISPLAY_LIMIT == 48
    assert "min-width: 240px !important;" in css
    assert "max-width: min(680px, calc(100vw - 188px)) !important;" in css


def test_topbar_title_truncates_at_expanded_limit() -> None:
    title = "Cohort A: Which factors are associated with ventilator pneumonia?"
    label, truncated = _truncate_title(title)
    assert truncated is True
    assert len(label) <= _TITLE_DISPLAY_LIMIT
    assert label.startswith("Cohort A: Which factors are associated")


def test_memory_keywords_match_real_ollama_oom_message() -> None:
    """The UI classifier relies on substring match against Ollama's 500-body
    text ('model requires more system memory (X GiB) than is available (Y GiB)').
    A typo in ``_MEMORY_KEYWORDS`` would silently re-route OOM errors into the
    generic 'Query failed' catch-all — the original bug."""

    real_ollama_body = "model requires more system memory (5.5 GiB) than is available (2.9 GiB)"
    assert any(kw in real_ollama_body.lower() for kw in _MEMORY_KEYWORDS)

    # Case-insensitive via the classifier's .lower() call site.
    mixed_case = "Insufficient Memory to load qwen3:8b"
    assert any(kw in mixed_case.lower() for kw in _MEMORY_KEYWORDS)

    # An unrelated error must not spuriously match and mask a different root
    # cause (e.g., a connection failure wrongly presented as OOM).
    unrelated = "connection refused at 127.0.0.1:11434"
    assert not any(kw in unrelated.lower() for kw in _MEMORY_KEYWORDS)


def test_memory_branch_wins_against_conn_for_all_rungs_runtime_error() -> None:
    """``agent_graph._init_llm`` raises ``RuntimeError`` when every qwen3 rung
    OOMs. That message contains BOTH 'refused' (matches ``_conn_keywords``)
    AND 'insufficient memory' (matches ``_MEMORY_KEYWORDS``), so the elif
    ordering in ``_stream_response`` is load-bearing: memory must be checked
    before conn, otherwise the user sees the misleading 🔌 connection-refused
    card for a true out-of-memory failure."""

    all_rungs_fail_msg = (
        "All 3 qwen3 ladder rungs (qwen3:8b, qwen3:4b, qwen3:1.7b) "
        "were refused by Ollama due to insufficient memory. Close some "
        "apps to free RAM, or set LLM_MODEL to a smaller model manually."
    ).lower()

    # Re-declare the other classifier keyword tuples here (they live inside
    # _stream_response as locals) to lock the elif ordering into the test.
    _conn_keywords = (
        "connection",
        "refused",
        "connecterror",
        "cannot reach",
        "timeout",
        "timed out",
    )

    # Sanity: both matchers fire on this message — that's the collision.
    assert any(kw in all_rungs_fail_msg for kw in _conn_keywords)
    assert any(kw in all_rungs_fail_msg for kw in _MEMORY_KEYWORDS)

    # The actual classifier in streaming.py must therefore check MEMORY first.
    streaming_source = (_PROJECT_ROOT / "scripts/ai_assistant/ui/streaming.py").read_text(
        encoding="utf-8"
    )
    mem_idx = streaming_source.index("elif any(kw in low for kw in _MEMORY_KEYWORDS):")
    conn_idx = streaming_source.index("elif any(kw in low for kw in _conn_keywords):")
    assert mem_idx < conn_idx, (
        "classifier order regression: _MEMORY_KEYWORDS must be checked before "
        "_conn_keywords, otherwise the all-rungs-fail RuntimeError (which "
        "contains both 'refused' and 'insufficient memory') gets routed to "
        "the connection-refused card instead of the out-of-memory card."
    )


def test_error_card_persists_after_rerun() -> None:
    """Error cards must survive ``st.rerun()`` — the root cause of 'no response
    with Claude API' (and all other error paths).

    ``_stream_response`` renders via an ephemeral ``st.empty()`` slot; after
    ``chat.py`` calls ``st.rerun()``, that slot is wiped. The fix is for
    ``_stream_response`` to return the user-facing error markdown as the
    assistant answer, while setting a small session flag so ``chat.py`` can
    mark the saved assistant message as an error."""

    streaming_src = (_PROJECT_ROOT / "scripts/ai_assistant/ui/streaming.py").read_text(
        encoding="utf-8"
    )
    chat_src = (_PROJECT_ROOT / "scripts/ai_assistant/ui/chat.py").read_text(encoding="utf-8")

    assert 'st.session_state["_rpln_stream_error"] = True' in streaming_src
    assert "return _error_md, tools_detail" in streaming_src
    set_idx = streaming_src.index('st.session_state["_rpln_stream_error"] = True')
    ret_idx = streaming_src.index("        return _error_md, tools_detail", set_idx)
    assert set_idx < ret_idx

    assert '["error"] = True' in chat_src, (
        "chat.py must mark persisted provider failures with error=True in messages_meta"
    )


def test_sanitize_file_refs_strips_paths_extensions_and_markers() -> None:
    text = (
        "Saved /workspace/output/chart.png and "
        "output/Indo-VAP/trio_bundle/datasets/6_HIV.jsonl "
        "with <RPLN_PLOTLY:/workspace/plot.json>"
    )
    cleaned = _sanitize_file_refs(text)
    assert "/workspace/output/chart.png" not in cleaned
    assert "6_HIV.jsonl" not in cleaned
    assert "<RPLN_PLOTLY" not in cleaned


def test_chat_message_renderer_has_no_artifact_download_panel() -> None:
    streaming_src = (_PROJECT_ROOT / "scripts/ai_assistant/ui/streaming.py").read_text(
        encoding="utf-8"
    )

    assert "def _render_artifacts_panel" not in streaming_src
    assert "_append_artifact_bundle" not in streaming_src
    assert "answer_artifacts.zip" not in streaming_src
    assert "def _render_artifact_bar" in streaming_src
    assert "st.download_button(" in streaming_src
    assert 'on_click="ignore"' in streaming_src


def test_artifact_file_download_names_are_generic(tmp_path: Path) -> None:
    chart = tmp_path / "sensitive_internal_chart_name.json"
    chart.write_text('{"data":[],"layout":{}}', encoding="utf-8")

    data, file_name, mime = _artifact_file_download(chart, kind="PLOTLY", index=3)

    assert data == b'{"data":[],"layout":{}}'
    assert file_name == "interactive_chart_3.json"
    assert mime == "application/json"
    assert "sensitive" not in file_name


def test_web_chat_persists_analysis_code_for_copy_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = "print('rows:', 1)"

    def fake_stream(*_args: object, **_kwargs: object):
        yield {
            "agent": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "run_python_analysis",
                                "args": {"code": code},
                                "id": "call_1",
                            }
                        ],
                    )
                ]
            }
        }
        yield {
            "tools": {
                "messages": [
                    ToolMessage(
                        content="rows: 1",
                        name="run_python_analysis",
                        tool_call_id="call_1",
                    )
                ]
            }
        }
        yield {"agent": {"messages": [AIMessage(content="Done.")]}}

    monkeypatch.setattr(config, "CONVERSATIONS_DIR", tmp_path / "conversations")
    at = _configured_chat_app()

    with patch("scripts.ai_assistant.ui.streaming.stream_query", side_effect=fake_stream):
        _submit_chat_message(at, "show a chart")

    tools = at.session_state.filtered_state["messages_meta"][1]["tools_used"]
    assert tools == [
        {
            "name": "run_python_analysis",
            "content_preview": "rows: 1",
            "analysis_code": code,
        }
    ]


def test_web_chat_persists_provider_error_after_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "CONVERSATIONS_DIR", tmp_path / "conversations")
    at = _configured_chat_app()

    with patch(
        "scripts.ai_assistant.ui.streaming.stream_query",
        side_effect=RuntimeError("401 unauthorized: bad api key"),
    ):
        _submit_chat_message(at, "hi")

    messages = at.session_state.filtered_state["messages"]
    assert messages[0] == {"role": "user", "content": "hi"}
    assert messages[1]["role"] == "assistant"
    assert "Authentication failed" in messages[1]["content"]


def test_web_chat_refuses_phi_prompt_without_persisting_raw_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_dir = tmp_path / "conversations"
    monkeypatch.setattr(config, "CONVERSATIONS_DIR", conv_dir)
    at = _configured_chat_app()
    raw_prompt = "Aadhaar 1234 5678 9012"

    with patch("scripts.ai_assistant.ui.streaming.stream_query") as stream_query:
        _submit_chat_message(at, raw_prompt)

    stream_query.assert_not_called()
    state = at.session_state.filtered_state
    messages = state["messages"]
    assert messages[0]["content"] == "[PHI-REFUSED — content redacted]"
    assert raw_prompt not in json.dumps(messages)
    assert "AADHAAR" not in messages[0]["content"]
    assert messages[1]["role"] == "assistant"
    assert "personally identifiable value" in messages[1]["content"].lower()
    assert state["messages_meta"][0]["phi_refused"] is True
    assert raw_prompt not in "\n".join(
        path.read_text(encoding="utf-8") for path in conv_dir.glob("*.json")
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configured_chat_app() -> AppTest:
    at = AppTest.from_file("scripts/ai_assistant/web_ui.py")
    at.session_state["setup_complete"] = True
    at.session_state["pipeline_ready"] = True
    at.session_state["llm_provider_label"] = "OpenAI"
    at.session_state["api_key_saved"] = "sk-test"
    at.session_state["llm_model"] = "gpt-4.1"
    return at


def _submit_chat_message(at: AppTest, message: str) -> None:
    at.run(timeout=10)
    at.text_area[0].set_value(message)
    send = next(button for button in at.button if button.label == "Send")
    send.click().run(timeout=10)


def _write_conv_json(
    conv_dir: Path,
    cid: str,
    created_at: str,
    *,
    pinned: bool = False,
    messages: list[dict[str, Any]] | None = None,
    has_artifacts: bool | None = None,
) -> None:
    data: dict[str, Any] = {
        "id": cid,
        "title": f"Chat {cid[:6]}",
        "created_at": created_at,
        "updated_at": created_at,
        "pinned": pinned,
        "messages": messages or [{"role": "user", "content": "hello"}],
    }
    if has_artifacts is not None:
        data["has_artifacts"] = has_artifacts
    (conv_dir / f"{cid}.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Bucketed conversations
# ---------------------------------------------------------------------------


class TestBucketedConversations:
    def test_all_buckets_populated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        conv_dir = tmp_path / "conversations"
        conv_dir.mkdir()
        monkeypatch.setattr(config, "CONVERSATIONS_DIR", conv_dir)

        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        _write_conv_json(conv_dir, "pin-01", (now - timedelta(days=60)).isoformat(), pinned=True)
        _write_conv_json(conv_dir, "today-01", now.isoformat())
        _write_conv_json(conv_dir, "yest-01", (today_start - timedelta(hours=12)).isoformat())
        _write_conv_json(conv_dir, "last7-01", (today_start - timedelta(days=3)).isoformat())
        _write_conv_json(conv_dir, "last30-01", (today_start - timedelta(days=15)).isoformat())
        _write_conv_json(conv_dir, "older-01", (today_start - timedelta(days=40)).isoformat())

        buckets = _list_conversations_bucketed()

        assert any(c["id"] == "pin-01" for c in buckets["pinned"])
        assert any(c["id"] == "today-01" for c in buckets["today"])
        assert any(c["id"] == "yest-01" for c in buckets["yesterday"])
        assert any(c["id"] == "last7-01" for c in buckets["last7"])
        assert any(c["id"] == "last30-01" for c in buckets["last30"])
        assert any(c["id"] == "older-01" for c in buckets["older"])

    def test_pinned_excluded_from_date_buckets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conv_dir = tmp_path / "conversations"
        conv_dir.mkdir()
        monkeypatch.setattr(config, "CONVERSATIONS_DIR", conv_dir)

        now = datetime.now(UTC)
        _write_conv_json(conv_dir, "pin-today", now.isoformat(), pinned=True)

        buckets = _list_conversations_bucketed()

        assert any(c["id"] == "pin-today" for c in buckets["pinned"])
        assert all(c["id"] != "pin-today" for c in buckets["today"])

    def test_limit_caps_total_conversations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conv_dir = tmp_path / "conversations"
        conv_dir.mkdir()
        monkeypatch.setattr(config, "CONVERSATIONS_DIR", conv_dir)

        now = datetime.now(UTC)
        for i in range(10):
            _write_conv_json(conv_dir, f"conv-{i:02d}", now.isoformat())

        buckets = _list_conversations_bucketed(limit=3)
        total = sum(len(v) for v in buckets.values())
        assert total <= 3


# ---------------------------------------------------------------------------
# Artifact detection
# ---------------------------------------------------------------------------


class TestConversationHasArtifacts:
    def _setup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        d = tmp_path / "conversations"
        d.mkdir()
        monkeypatch.setattr(config, "CONVERSATIONS_DIR", d)
        return d

    def test_rpln_plotly_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = self._setup(tmp_path, monkeypatch)
        _write_conv_json(
            d,
            "a1",
            datetime.now(UTC).isoformat(),
            messages=[{"role": "assistant", "content": "Result <RPLN_PLOTLY:/tmp/p.json>"}],
        )
        assert _conversation_has_artifacts("a1") is True

    def test_rpln_figure_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = self._setup(tmp_path, monkeypatch)
        _write_conv_json(
            d,
            "a2",
            datetime.now(UTC).isoformat(),
            messages=[{"role": "assistant", "content": "See <RPLN_FIGURE:/tmp/f.png>"}],
        )
        assert _conversation_has_artifacts("a2") is True

    def test_rpln_analysis_returns_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = self._setup(tmp_path, monkeypatch)
        _write_conv_json(
            d,
            "a3",
            datetime.now(UTC).isoformat(),
            messages=[{"role": "assistant", "content": "Done <RPLN_ANALYSIS:/tmp/a.md>"}],
        )
        assert _conversation_has_artifacts("a3") is True

    def test_no_markers_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        d = self._setup(tmp_path, monkeypatch)
        _write_conv_json(
            d,
            "a4",
            datetime.now(UTC).isoformat(),
            messages=[{"role": "assistant", "content": "Plain text answer."}],
        )
        assert _conversation_has_artifacts("a4") is False

    def test_missing_file_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._setup(tmp_path, monkeypatch)
        assert _conversation_has_artifacts("does-not-exist") is False


# ---------------------------------------------------------------------------
# Export submenu
# ---------------------------------------------------------------------------


_FAKE_ZIP = b"PK\x03\x04fake"


class TestExportSubmenu:
    def test_no_artifact_shows_only_txt_and_md(self) -> None:
        with (
            patch("scripts.ai_assistant.web_ui._conversation_has_artifacts", return_value=False),
            patch("scripts.ai_assistant.web_ui._export_conversation_as_text", return_value=""),
            patch("scripts.ai_assistant.web_ui._export_conversation_as_md", return_value=""),
            patch("streamlit.popover"),
            patch("streamlit.markdown"),
            patch("streamlit.download_button") as mock_dl,
            patch("streamlit.caption") as mock_cap,
        ):
            _render_export_submenu("conv-abc")

        assert mock_dl.call_count == 2
        mock_cap.assert_not_called()

    def test_artifact_shows_six_download_buttons(self) -> None:
        with (
            patch("scripts.ai_assistant.web_ui._conversation_has_artifacts", return_value=True),
            patch("scripts.ai_assistant.web_ui._export_conversation_as_text", return_value=""),
            patch("scripts.ai_assistant.web_ui._export_conversation_as_md", return_value=""),
            patch("scripts.ai_assistant.web_ui._export_plots_as_zip", return_value=_FAKE_ZIP),
            patch("scripts.ai_assistant.web_ui._export_tables_as_zip", return_value=_FAKE_ZIP),
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("streamlit.popover"),
            patch("streamlit.markdown"),
            patch("streamlit.download_button") as mock_dl,
            patch("streamlit.caption") as mock_cap,
        ):
            _render_export_submenu("conv-def")

        assert mock_dl.call_count == 6  # txt + md + PNG + JPEG + CSV + XLSX
        mock_cap.assert_not_called()

    def test_missing_kaleido_shows_caption_note(self) -> None:
        with (
            patch("scripts.ai_assistant.web_ui._conversation_has_artifacts", return_value=True),
            patch("scripts.ai_assistant.web_ui._export_conversation_as_text", return_value=""),
            patch("scripts.ai_assistant.web_ui._export_conversation_as_md", return_value=""),
            patch("scripts.ai_assistant.web_ui._export_plots_as_zip", return_value=_FAKE_ZIP),
            patch("scripts.ai_assistant.web_ui._export_tables_as_zip", return_value=_FAKE_ZIP),
            patch("importlib.util.find_spec", return_value=None),
            patch("streamlit.popover"),
            patch("streamlit.markdown"),
            patch("streamlit.download_button"),
            patch("streamlit.caption") as mock_cap,
        ):
            _render_export_submenu("conv-ghi")

        mock_cap.assert_called_once()
        cap_text: str = mock_cap.call_args[0][0]
        assert "kaleido" in cap_text.lower()


# ---------------------------------------------------------------------------
# Shutdown routing — Flow 1 removes top-right End Chat; Log off in the
# sidebar Profile popover is the new shutdown trigger. These tests lock in:
#   1. The top bar no longer contains a key="rpln_end_chat" button.
#   2. Clicking "rpln_profile_logoff" in the sidebar Profile popover sets
#      st.query_params["shutdown"] = "1" (routing to the goodbye flow).
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_top_bar_has_no_end_chat_button(self) -> None:
        """Flow 1: the End Chat button must not be rendered in the top bar."""
        params: dict[str, str] = {}
        mock_ss = MagicMock()
        mock_ss.get.return_value = ""
        col_mocks = (MagicMock(), MagicMock())
        clicked_keys: list[str] = []

        def _btn(label: str, *, key: str = "", **_kw: object) -> bool:
            clicked_keys.append(key)
            return False

        with (
            patch("streamlit.markdown"),
            patch("streamlit.session_state", mock_ss),
            patch("streamlit.container"),
            patch("streamlit.columns", return_value=col_mocks),
            patch("streamlit.button", side_effect=_btn),
            patch("streamlit.query_params", params),
            patch("streamlit.rerun"),
        ):
            _render_conv_title_dropdown()

        assert "rpln_end_chat" not in clicked_keys
        assert params.get("shutdown") is None

    def test_profile_logoff_sets_shutdown_query_param(self) -> None:
        """Clicking Log off in the Profile popover must route to shutdown."""
        params: dict[str, str] = {}
        mock_ss = MagicMock()
        mock_ss.get.return_value = ""
        col_mocks = (MagicMock(), MagicMock())

        def _btn(label: str = "", *args: object, key: str = "", **_kw: object) -> bool:
            return key == "rpln_profile_logoff"

        with (
            patch("streamlit.markdown"),
            patch("streamlit.session_state", mock_ss),
            patch("streamlit.sidebar"),
            patch("streamlit.columns", return_value=col_mocks),
            patch("streamlit.button", side_effect=_btn),
            patch("streamlit.text_input", return_value=""),
            patch("streamlit.popover"),
            patch("streamlit.caption"),
            patch("streamlit.divider"),
            patch("streamlit.html"),
            patch("streamlit.query_params", params),
            patch("streamlit.rerun"),
            patch("scripts.ai_assistant.web_ui._list_conversations", return_value=[]),
            patch("scripts.ai_assistant.web_ui._search_conversations", return_value=[]),
        ):
            _render_sidebar()

        assert params.get("shutdown") == "1"
