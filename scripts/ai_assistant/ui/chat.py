"""Chat UI: welcome hero, thread rendering, composer, model pill."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from typing import Any

import streamlit as st

import config
from scripts.ai_assistant.agent_graph import reset_agent
from scripts.ai_assistant.phi_safe import guard_user_prompt
from scripts.ai_assistant.ui.conversations import _save_conversation
from scripts.ai_assistant.ui.providers import (
    _OTHER_MODEL_OPTION,
    _PROVIDER_CONFIG,
    _get_ollama_models,
    _is_ollama_chat_model,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Welcome hero
# ---------------------------------------------------------------------------


def hero() -> None:
    study = (config.STUDY_NAME or "").strip() or "Study"
    st.html(
        f"""
        <div class="rpln-column rpln-hero">
          <div class="rpln-hero-icon" aria-hidden="true" data-rpln-logo-slot>🔬</div>
          <div class="rpln-hero-title-wrap">
            <h1 class="rpln-hero-title">RePORT AI Portal</h1>
          </div>
          <div class="rpln-hero-tagline">{study}</div>
          <p class="rpln-hero-desc">
            Ask about variables, datasets, and cohorts in this clinical research study.
            RePORT AI Portal can analyze data, render plots, and cross-reference
            variables across datasets.
          </p>
        </div>
        """
    )


# ---------------------------------------------------------------------------
# Thread rendering — delegates to streaming.py for all message content
# ---------------------------------------------------------------------------


def render_thread() -> Any | None:
    from scripts.ai_assistant.ui.streaming import _render_chat_history

    if not st.session_state.get("messages"):
        hero()
        return st.empty() if st.session_state.get("rpln_pending_stream") else None
    _render_chat_history()
    return st.empty() if st.session_state.get("rpln_pending_stream") else None


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def composer(assistant_slot: Any | None = None) -> None:
    """Render chat composer; handle submit and streaming."""
    ss = st.session_state
    pending_stream = ss.get("rpln_pending_stream")

    # Prefill from Edit button — must happen before text_area widget is created
    if ss.get("rpln_composer_prefill") and not pending_stream:
        ss["rpln_composer_textarea"] = ss.pop("rpln_composer_prefill")

    _placeholder = (
        "How can I help you today?" if not ss.get("messages") else "Reply to RePORT AI Portal…"
    )
    _submit_glyph = "Generating response" if pending_stream else "Send"

    if pending_stream and assistant_slot is None:
        # Fallback for tests or direct callers. The normal web UI passes a
        # thread-local slot so the loader appears directly below the latest user
        # message instead of below the sticky composer.
        with st.container(key="rpln_stream_anchor"):
            assistant_slot = st.empty()

    submitted = False
    _typed = ""
    with st.container(key="rpln_composer_dock"):
        with st.container(key="rpln_composer_shell"):
            if pending_stream:
                st.markdown(
                    '<span class="rpln-composer-streaming-sentinel"'
                    ' aria-hidden="true" style="display:none;"></span>',
                    unsafe_allow_html=True,
                )
            # WP-F.05.11c — editing banner (matches reference HTML:832-847).
            # Shown while rpln_pending_edit sits in session, i.e. between the
            # Edit-rail click and the next submit (or Cancel).
            if ss.get("rpln_pending_edit"):
                st.markdown(
                    '<div class="rpln-editing-banner">'
                    '<span class="material-symbols-rounded">edit</span>'
                    "<span>Editing message — sending will regenerate from here.</span>"
                    '<span class="rpln-editing-spacer"></span>'
                    '<button type="button" data-rpln-action="cancel-edit">Cancel</button>'
                    "</div>",
                    unsafe_allow_html=True,
                )

            # Shell marker — used by tests + disclaimer-outside check.
            st.markdown(
                '<span data-rpln-composer-shell="1" aria-hidden="true"'
                ' style="display:none;"></span>',
                unsafe_allow_html=True,
            )

            # Composer layout: textarea + real submit button on the top row,
            # control rail below (model pill + retrieval filter).
            # The model-pill lives in a hidden container outside the form
            # (st.popover forbids nesting inside st.form) — bridge.js hoists its
            # DOM node into .rpln-composer-pill-slot so it appears in the lower
            # left control rail while preserving the existing popover behavior.
            with st.form(key="rpln_composer_form", clear_on_submit=True, border=False):
                _typed = st.text_area(
                    "Message",
                    placeholder=_placeholder,
                    key="rpln_composer_textarea",
                    label_visibility="collapsed",
                    height=68,
                )
                submitted = st.form_submit_button(
                    _submit_glyph,
                    width="content",
                    disabled=bool(pending_stream),
                    help=("Response in progress" if pending_stream else None),
                )

            # Lower control rail: pill moved to the model-selection position on
            # the left, retrieval filter beside it, shortcuts on the right.
            st.markdown(
                '<div class="rpln-composer-foot" data-rpln-composer-foot="1">'
                '  <span class="rpln-composer-foot-left">'
                '    <span class="rpln-composer-pill-slot" data-rpln-pill-slot="1"></span>'
                '    <button type="button" class="rpln-chip" data-rpln-chip="retrieval"'
                '            data-rpln-action="open-retrieval-popover">'
                '      <span class="material-symbols-rounded" aria-hidden="true">filter_list</span>'
                "      <span>Retrieval filter</span>"
                "    </button>"
                "  </span>"
                '  <span class="rpln-composer-spacer"></span>'
                '  <span class="rpln-composer-hint" aria-hidden="true">'
                '    <kbd>Enter</kbd><span class="rpln-composer-hint-word">send</span>'
                '    <span class="rpln-composer-hint-sep">·</span>'
                '    <kbd>Shift</kbd><span class="rpln-composer-hint-plus">+</span><kbd>Enter</kbd>'
                '    <span class="rpln-composer-hint-word">newline</span>'
                "  </span>"
                "</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<button type="button" class="rpln-submit-proxy" '
                'data-rpln-action="submit-composer" aria-hidden="true" tabindex="-1"></button>',
                unsafe_allow_html=True,
            )

            # Real model pill lives in a hidden container; bridge.js moves
            # the pill DOM node into .rpln-composer-pill-slot via appendChild.
            with st.container(key="rpln_model_pill_host"):
                _render_model_pill()

        # WP-F.05.11c — hidden bridge for the Cancel button in the editing banner.
        # Clears the pending-edit flag + wipes the prefilled composer text. The
        # edited history was already truncated when Edit was clicked, so there's
        # nothing to restore — Cancel simply discards the in-progress edit.
        if st.button("cancel edit", key="rpln_cancel_edit"):
            ss.pop("rpln_pending_edit", None)
            ss["rpln_composer_textarea"] = ""
            st.rerun()

        # Disclaimer OUTSIDE composer shell (spec §5.8) — single variant.
        st.markdown(
            '<p class="rpln-footer-disclaimer" data-rpln-disclaimer="1">'
            "RePORT AI Portal can analyze clinical data and render plots. It may"
            " make mistakes. Verify critical outputs.</p>",
            unsafe_allow_html=True,
        )

    st.markdown(
        '<button type="button" class="rpln-jump-latest" '
        'data-rpln-action="jump-latest" aria-label="Jump to latest response">'
        '<span class="material-symbols-rounded">arrow_downward</span>'
        "</button>",
        unsafe_allow_html=True,
    )

    # Streaming reply fills the slot created above the composer.
    if pending_stream and assistant_slot is not None:
        from scripts.ai_assistant.ui.streaming import _stream_response

        with assistant_slot.container(), st.chat_message("assistant", avatar="🔬"):
            result = _stream_response(pending_stream)
        answer, tools_used = result
        asst_idx = len(ss.messages)
        ss.messages.append({"role": "assistant", "content": answer})
        ss.messages_meta[asst_idx] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tools_used": tools_used,
            "has_figure": "<RPLN_FIGURE:" in answer,
        }
        if ss.pop("_rpln_stream_error", False):
            ss.messages_meta[asst_idx]["error"] = True
        _save_conversation()
        ss.pop("rpln_pending_stream", None)
        st.rerun()

    user_input = (_typed or "").strip() if submitted and _typed else None
    pending = ss.pop("pending_question", None)
    question = pending or user_input

    if question and not pending_stream:
        guard = guard_user_prompt(question)
        user_idx = len(ss.messages)
        user_content = (
            question
            if guard.ok
            else "[PHI-REFUSED — content redacted]"
        )
        ss.messages.append({"role": "user", "content": user_content})
        meta: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tools_used": [],
        }
        if not guard.ok:
            meta["phi_refused"] = True
            meta["phi_findings"] = list(guard.findings)
        # WP-F.05.09b.4 \u2014 mark submission as an edit so the bubble
        # renders an "edited" badge. The flag is set when the user clicks
        # the Edit rail button (see streaming.py::_render_user_message_actions).
        if ss.pop("rpln_pending_edit", False):
            meta["edited"] = True
        ss.messages_meta[user_idx] = meta
        if not guard.ok:
            asst_idx = len(ss.messages)
            ss.messages.append(
                {
                    "role": "assistant",
                    "content": guard.refusal_message or "I can't send that prompt to the LLM.",
                }
            )
            ss.messages_meta[asst_idx] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "tools_used": [],
                "has_figure": False,
            }
            _save_conversation()
            st.rerun()
        _save_conversation()
        ss.rpln_pending_stream = question
        st.rerun()


# ---------------------------------------------------------------------------
# Model pill — lives here since it renders inside the composer
# ---------------------------------------------------------------------------

_MODEL_DESCRIPTIONS: dict[str, str] = {
    # Anthropic
    "claude-opus-4-6": "Most capable Claude — deep reasoning",
    "claude-opus-4-5-20251101": "Opus 4.5 — long-horizon reasoning",
    "claude-sonnet-4-6": "Balanced Claude — fast and smart",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5 — daily driver",
    "claude-haiku-4-5-20251001": "Fastest Claude — low latency",
    # OpenAI
    "gpt-4.1": "GPT-4.1 — reliable all-rounder",
    "gpt-4.1-mini": "Smaller GPT-4.1 — cheaper",
    "gpt-4o": "Omni-modal flagship",
    "gpt-4o-mini": "Lightweight omni-modal",
    "o4-mini-2025-04-16": "Reasoning-focused mini",
    "o3-2025-04-16": "Advanced reasoning",
    # Google
    "gemini-3-flash": "Fast Gemini 3",
    "gemini-3-pro": "Capable Gemini 3",
    "gemini-2.5-pro": "Gemini 2.5 — strong reasoning",
    "gemini-2.5-flash": "Fast Gemini 2.5",
    "gemini-2.0-flash": "Gemini 2.0 — snappy",
    # Ollama (local)
    "qwen3:1.7b": "Local Qwen3 1.7B — lightweight",
    "qwen3:4b": "Local Qwen3 4B — balanced",
    "qwen3:8b": "Local Qwen3 8B — fast default",
    "qwen3:14b": "Local Qwen3 14B — more capable",
    "qwen3:32b": "Local Qwen3 32B — strongest local",
    "mistral:latest": "Local Mistral",
    "gemma3:9b": "Local Gemma 3 9B",
    "deepseek-r1:8b": "Local DeepSeek R1 — reasoning",
}

_PRETTY_MODEL_MAP: dict[str, str] = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-5-20251101": "Opus 4.5",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "gpt-4.1": "GPT-4.1",
    "gpt-4.1-mini": "GPT-4.1 mini",
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
}


def _pretty_model_label(model: str) -> str:
    return _PRETTY_MODEL_MAP.get(model, model)


def _pretty_model_label_compact(model: str) -> str:
    mapped = _PRETTY_MODEL_MAP.get(model)
    if mapped:
        return mapped
    base = model.split(":", 1)[0]
    import re as _re

    base = _re.sub(r"([a-z])(\d)", r"\1 \2", base)
    return base[:1].upper() + base[1:] if base else model


def _model_description(model: str, *, is_local: bool) -> str:
    desc = _MODEL_DESCRIPTIONS.get(model, "")
    if desc:
        return desc
    return "Local model" if is_local else ""


def _available_chat_models() -> tuple[list[str], str] | None:
    """Return (options, current) for the active provider, or None if unknown."""
    provider_label = st.session_state.get("llm_provider_label", "")
    if not provider_label or provider_label not in _PROVIDER_CONFIG:
        return None
    cfg = _PROVIDER_CONFIG[provider_label]

    if provider_label == "Ollama (local)":
        if "rpln_ollama_model_cache" not in st.session_state:
            discovered, _ = _get_ollama_models()
            chat = [m for m in discovered if m != _OTHER_MODEL_OPTION and _is_ollama_chat_model(m)]
            st.session_state.rpln_ollama_model_cache = chat or [
                m for m in cfg.get("models", []) if m != _OTHER_MODEL_OPTION
            ]
        models = list(st.session_state.rpln_ollama_model_cache)
    else:
        models = [m for m in cfg.get("models", []) if m != _OTHER_MODEL_OPTION]

    current = st.session_state.get("llm_model", cfg["default_model"])
    if current and current not in models:
        models.insert(0, current)
    if not models:
        return None
    return models, current


def _set_chat_model(model: str) -> None:
    st.session_state.llm_model = model
    config.LLM_MODEL = model  # type: ignore[attr-defined]
    reset_agent()


def _sync_adaptive_toggle() -> None:
    """WP-F.05.09b.1 — keep ``adaptive_thinking`` in step with the toggle."""
    st.session_state.adaptive_thinking = bool(st.session_state.get("rpln_adaptive_toggle", False))


def _render_model_pill() -> None:
    """Composer model pill: current model + Adaptive toggle + More models."""
    selector_state = _available_chat_models()
    if selector_state is None:
        return
    model_options, current_model = selector_state
    provider_label = st.session_state.get("llm_provider_label", "")
    is_local = provider_label == "Ollama (local)"
    adaptive_on = bool(st.session_state.get("adaptive_thinking", False))

    pill_css = (
        "[class*='st-key-rpln_composer_model']:has(.rpln-adaptive-sentinel-on)"
        " [data-testid='stPopoverButton'] p::after{"
        "content:' Adaptive';font-weight:400;color:#8a8a87;margin-left:4px;}"
    )
    st.markdown(f"<style>{pill_css}</style>", unsafe_allow_html=True)

    with st.container(key="rpln_composer_model"):
        if adaptive_on:
            st.markdown(
                '<div class="rpln-adaptive-sentinel-on" style="display:none;"></div>',
                unsafe_allow_html=True,
            )
        pop = st.popover(_pretty_model_label_compact(current_model), width="content")
    more_open = bool(st.session_state.get("rpln_model_more_open", False))
    with pop:
        st.markdown(
            '<div class="rpln-model-menu-header" style="display:none;"></div>',
            unsafe_allow_html=True,
        )

        if not more_open:
            cur_name = html.escape(_pretty_model_label(current_model))
            cur_desc = html.escape(
                _model_description(current_model, is_local=is_local) or ""
            )
            st.markdown(
                '<div class="rpln-current-model">'
                '<div class="rpln-current-model-text">'
                f'<div class="rpln-current-model-name">{cur_name}</div>'
                f'<div class="rpln-current-model-desc">{cur_desc}</div>'
                "</div>"
                '<div class="rpln-model-check-active">✓</div>'
                "</div>",
                unsafe_allow_html=True,
            )
            st.markdown('<hr class="rpln-popover-sep" />', unsafe_allow_html=True)

            with st.container(key="rpln_adaptive_section"):
                at_col_label, at_col_toggle = st.columns([5, 2], gap="small")
                with at_col_label:
                    st.markdown(
                        '<div class="rpln-adaptive-text">'
                        '<div class="rpln-title">Adaptive thinking'
                        ' <span class="rpln-beta-tag">beta</span></div>'
                        '<div class="rpln-sub">Thinks for more complex tasks</div>'
                        "</div>",
                        unsafe_allow_html=True,
                    )
                with at_col_toggle:
                    # WP-F.05.09b.1 — Use an on_change callback to mirror
                    # the toggle state into ``adaptive_thinking``. Streamlit
                    # reruns automatically on widget change; the previous
                    # explicit ``st.rerun()`` inside a popover container was
                    # the cause of the duplicate-pill paint mid-stream.
                    st.toggle(
                        "Adaptive thinking",
                        value=adaptive_on,
                        key="rpln_adaptive_toggle",
                        label_visibility="collapsed",
                        on_change=_sync_adaptive_toggle,
                    )

            st.markdown('<hr class="rpln-popover-sep" />', unsafe_allow_html=True)

            if st.button(
                "More models  \u203a", key="rpln_more_models_toggle", width="stretch"
            ):
                st.session_state.rpln_model_more_open = True
                st.rerun()
        else:
            if st.button("\u2039  Models", key="rpln_more_models_back", width="stretch"):
                st.session_state.rpln_model_more_open = False
                st.rerun()
            st.markdown('<hr class="rpln-popover-sep" />', unsafe_allow_html=True)
            for model in model_options:
                if model == current_model:
                    continue
                name = _pretty_model_label(model)
                if st.button(name, key=f"model_pill_{model}", width="stretch"):
                    _set_chat_model(model)
                    st.session_state.rpln_model_more_open = False
                    st.rerun()


def _render_composer_plus_menu() -> None:
    """Render the `+` popover (Upload file / Upload folder)."""
    with (
        st.container(key="rpln_composer_plus"),
        st.popover("+", width="content"),
    ):
        st.markdown(
            '<div class="rpln-plus-menu-header">Attach</div>',
            unsafe_allow_html=True,
        )
        if st.button("Upload file", key="rpln_plus_upload_file", width="stretch"):
            st.session_state["rpln_plus_upload_mode"] = "file"
        if st.button("Upload folder", key="rpln_plus_upload_folder", width="stretch"):
            st.session_state["rpln_plus_upload_mode"] = "folder"

    mode = st.session_state.get("rpln_plus_upload_mode")
    if mode:
        with st.container(key="rpln_plus_uploader_slot"):
            st.file_uploader(
                "Select files" if mode == "file" else "Select a folder of files",
                key="rpln_plus_uploader",
                accept_multiple_files=(mode == "folder"),
                label_visibility="collapsed",
            )
