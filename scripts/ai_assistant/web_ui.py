"""RePORT AI Portal Chat UI — entry point.

Launch:
    uv run streamlit run scripts/ai_assistant/web_ui.py
    or
    uv run python main.py --web
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import UTC, datetime
from datetime import timedelta as _timedelta
from pathlib import Path
from typing import Any

import streamlit as st

import config
from scripts.ai_assistant.ui import chat, shell, wizard
from scripts.ai_assistant.ui.auth import enforce_auth_boundary
from scripts.ai_assistant.ui.conversations import (
    _conversation_has_artifacts,
    _conversations_dir,
    _export_conversation_as_md,
    _export_conversation_as_text,
    _export_plots_as_zip,
    _export_tables_as_zip,
    _list_conversations,
    _load_conversation,
    _relative_time,
    _save_conversation,
    _search_conversations,
)
from scripts.ai_assistant.ui.providers import (
    _OTHER_MODEL_OPTION,
    _PROVIDER_CONFIG,
    _default_provider_label,
)
from scripts.ai_assistant.ui.state import init_state

_CSS_PATH = Path(__file__).parent / "ui" / "assets" / "theme.css"

logger = logging.getLogger(__name__)


def _inject_redesign_css() -> None:
    """Hydrate the body class + per-user appearance attributes.

    The CSS rules that used to live in ``theme_redesign.css`` are now appended
    at the end of ``theme.css`` (scoped to ``body.rpln-redesign``) so one file
    serves the whole app. This function is still called post-chat-start to
    (1) flip the body class from ``rpln-wizard`` to ``rpln-redesign`` and
    (2) rehydrate the five data-* appearance attributes the CSS variants key
    on (theme, bubble, aprose, density, accent).
    """
    with st.container(key="rpln_ui_bridge_redesign"):
        st.iframe(
            "<!doctype html><html><body style='margin:0;overflow:hidden'>"
            "<script>try{"
            "var pb=window.parent.document.body;"
            "pb.classList.remove('rpln-wizard');"
            "pb.classList.add('rpln-redesign');"
            "var d={theme:'terracotta',bubble:'tail',aprose:'serif',"
            "density:'normal',accent:'C96442'};"
            "var raw=localStorage.getItem('report_ai_portal_appearance_v1');"
            "var a={};if(raw){try{a=JSON.parse(raw)||{};}catch(e){a={};}}"
            "['theme','bubble','aprose','density','accent'].forEach(function(k){"
            "pb.setAttribute('data-'+k,a[k]||d[k]);"
            "});"
            "}catch(e){}</script></body></html>",
            width="content",
            height="content",
            tab_index=-1,
        )
    # WP-F.05.08 — mobile scrim (covers chat canvas when sidebar is drawn
    # as a drawer on <=840 px viewports). Toggled by bridge.js.
    st.html('<div class="rpln-mobile-scrim" data-rpln-mobile-scrim></div>')


def main() -> None:
    """Streamlit app entry point."""
    st.set_page_config(
        page_title=f"RePORT AI Portal — {config.STUDY_NAME}",
        page_icon="🔬",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    enforce_auth_boundary()
    init_state()
    shell.inject_css()
    shell.install_bridge()

    # Extra view-state keys not in init_state (UI-only ephemeral flags)
    for _key, _val in [
        ("rpln_view", "chat"),
        ("rpln_theme", "dark"),
        ("rpln_clear_confirm", False),
        ("rpln_search_modal_query", ""),
        ("adaptive_thinking", False),
        ("rpln_adaptive_toggle", False),
        ("rpln_adaptive_default_applied", False),
        ("rpln_model_more_open", False),
        # WP-F.05.01 — redesign gate. Default True so returning users (who
        # already have setup_complete=True persisted) see the new UI too.
        ("chat_started", True),
    ]:
        if _key not in st.session_state:
            st.session_state[_key] = _val

    if not st.session_state.get("rpln_adaptive_default_applied", False):
        st.session_state.adaptive_thinking = False
        st.session_state.rpln_adaptive_toggle = False
        st.session_state.rpln_adaptive_default_applied = True

    # WP-F.05.01 — layer redesign tokens + body class after wizard completes.
    # Wizard path (body.rpln-wizard) stays on legacy theme.css verbatim.
    if st.session_state.get("setup_complete") and st.session_state.get("chat_started"):
        _inject_redesign_css()

    # Handle end-chat shutdown
    if st.session_state.get("ended") or st.query_params.get("shutdown") == "1":
        _goodbye()
        return

    # Always re-sync LLM config from session state after hot-reload
    if st.session_state.setup_complete:
        wizard.ensure_llm_config()

    # Setup wizard until LLM + study data are configured
    if not st.session_state.setup_complete:
        wizard.render_setup_page()
        return

    # Search modal (st.dialog — must open before sidebar render)
    if st.session_state.get("rpln_search_modal_open"):
        st.session_state.rpln_search_modal_open = False
        _render_search_modal()

    # Settings panel replaces chat canvas; sidebar still visible
    if st.session_state.get("rpln_view") == "settings":
        shell.sidebar()
        _render_settings_panel()
        return

    # Main chat layout
    shell.sidebar()
    shell.topbar()
    with st.container(key="rpln_thread_shell"):
        assistant_slot = chat.render_thread()
    chat.composer(assistant_slot=assistant_slot)


# ---------------------------------------------------------------------------
# Goodbye page
# ---------------------------------------------------------------------------


def _goodbye() -> None:
    st.html(
        """
        <div class="rpln-goodbye">
          <div class="rpln-goodbye-mark">R</div>
          <h1>Thank you for using RePORT AI Portal</h1>
          <p>Your session has ended. Close this tab to return to the workspace.</p>
        </div>
        """
    )
    st.stop()


# ---------------------------------------------------------------------------
# Search modal
# ---------------------------------------------------------------------------


@st.dialog("Search chats")
def _render_search_modal() -> None:
    """Centered search modal: live-filter conversations grouped by recency."""
    query_key = "rpln_search_modal_query"
    clear_query_key = "rpln_search_modal_clear_next"
    if st.session_state.pop(clear_query_key, False):
        st.session_state.pop(query_key, None)
    query = st.text_input(
        "Search",
        placeholder="Search chats",
        label_visibility="collapsed",
        key=query_key,
    )
    matches = _search_conversations(query or "")
    if not matches:
        st.caption("No matching conversations.")
        return

    order = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"]
    groups: dict[str, list[dict[str, Any]]] = {k: [] for k in order}
    for conv in matches:
        groups[_search_modal_bucket(conv)].append(conv)

    for label in order:
        convs = groups.get(label, [])
        if not convs:
            continue
        st.markdown(
            f'<span class="rpln-search-group">{label}</span>',
            unsafe_allow_html=True,
        )
        for conv in convs:
            cid = conv["id"]
            title = conv.get("title") or "Untitled"
            ts_raw = conv.get("updated_at") or conv.get("created_at") or ""
            ts_label = _relative_time(ts_raw) if ts_raw else ""
            with st.container(key=f"rpln_search_row_{cid}"):
                col_title, col_ts = st.columns([8, 2], gap="small")
                with col_title:
                    if st.button(title, key=f"rpln_search_result_{cid}", width="stretch"):
                        _save_conversation()
                        # _load_conversation populates session state directly +
                        # resets the agent on success; nothing further to do.
                        _load_conversation(cid)
                        st.session_state.rpln_search_modal_open = False
                        st.session_state[clear_query_key] = True
                        st.rerun()
                with col_ts:
                    if ts_label:
                        st.markdown(
                            f'<div class="rpln-search-ts">{ts_label}</div>',
                            unsafe_allow_html=True,
                        )


def _search_modal_bucket(conv: dict[str, Any]) -> str:
    created_str = conv.get("created_at") or conv.get("updated_at") or ""
    try:
        dt = datetime.fromisoformat(created_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return "Older"
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt >= today_start:
        return "Today"
    if dt >= today_start - _timedelta(days=1):
        return "Yesterday"
    if dt >= today_start - _timedelta(days=7):
        return "Previous 7 days"
    if dt >= today_start - _timedelta(days=30):
        return "Previous 30 days"
    return "Older"


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------

# Appearance knobs — ported 2026-04-22 from the legacy slide-in overlay.
# The 5 knobs now live inside the Settings → General tab. All click handling
# stays in bridge.js (set-theme / set-accent / set-bubble / set-aprose /
# set-density) so no Streamlit rerun fires on each knob change.
_TWEAK_THEMES: list[tuple[str, str, str, str]] = [
    # (id, label, bg-chip, accent-chip) — v0 exact hex values
    ("terracotta", "Terracotta", "#121212", "#C96442"),
    ("graphite", "Graphite", "#141414", "#A8A29E"),
    ("midnight", "Midnight", "#0E111C", "#7AA2F7"),
    ("forest", "Forest", "#121A14", "#8AA878"),
    ("plum", "Plum", "#17121C", "#B69AD6"),
    ("rose", "Rose", "#1A1015", "#D98A9F"),
    ("sand", "Sand", "#17130E", "#D9B77A"),
    ("ocean", "Ocean", "#0E1821", "#6FB3C3"),
]
_TWEAK_ACCENTS: list[str] = ["C96442", "7C9B6E", "8A7FBE", "C48B3F", "5E8CA8"]
_TWEAK_BUBBLES: list[tuple[str, str]] = [
    ("tail", "Tail"),
    ("soft", "Soft"),
    ("sharp", "Sharp"),
    ("flat", "Outline"),
]
_TWEAK_APROSE: list[tuple[str, str]] = [("serif", "Serif"), ("sans", "Sans")]
_TWEAK_DENSITY: list[tuple[str, str]] = [
    ("compact", "Compact"),
    ("normal", "Normal"),
    ("spacious", "Spacious"),
]


def _build_appearance_section_html() -> str:
    """Return HTML for the Appearance section inside Settings → General."""
    theme_cards = "".join(
        f'<button class="rpln-tweaks-theme" type="button" '
        f'data-rpln-action="set-theme" data-rpln-val="{tid}">'
        f'<span class="rpln-tweaks-theme-chip" '
        f'style="background:linear-gradient(90deg,{bg} 0 55%,{acc} 55% 100%)"></span>'
        f'<span class="rpln-tweaks-theme-name">{label}</span>'
        f"</button>"
        for tid, label, bg, acc in _TWEAK_THEMES
    )
    accent_swatches = "".join(
        f'<button class="rpln-tweaks-swatch" type="button" title="#{a}" '
        f'data-rpln-action="set-accent" data-rpln-val="{a}" '
        f'style="background:#{a}"></button>'
        for a in _TWEAK_ACCENTS
    )
    bubble_opts = "".join(
        f'<button class="rpln-tweaks-opt" type="button" '
        f'data-rpln-action="set-bubble" data-rpln-val="{val}">{label}</button>'
        for val, label in _TWEAK_BUBBLES
    )
    aprose_opts = "".join(
        f'<button class="rpln-tweaks-opt" type="button" '
        f'data-rpln-action="set-aprose" data-rpln-val="{val}">{label}</button>'
        for val, label in _TWEAK_APROSE
    )
    density_opts = "".join(
        f'<button class="rpln-tweaks-opt" type="button" '
        f'data-rpln-action="set-density" data-rpln-val="{val}">{label}</button>'
        for val, label in _TWEAK_DENSITY
    )
    return f"""
    <div class="rpln-appearance">
      <div class="rpln-tweaks-row">
        <div class="rpln-tweaks-label">Theme</div>
        <div class="rpln-tweaks-themes">{theme_cards}</div>
      </div>
      <div class="rpln-tweaks-row">
        <div class="rpln-tweaks-label">Accent</div>
        <div class="rpln-tweaks-swatches">{accent_swatches}</div>
      </div>
      <div class="rpln-tweaks-row">
        <div class="rpln-tweaks-label">User bubble</div>
        <div class="rpln-tweaks-options">{bubble_opts}</div>
      </div>
      <div class="rpln-tweaks-row">
        <div class="rpln-tweaks-label">Assistant prose</div>
        <div class="rpln-tweaks-options">{aprose_opts}</div>
      </div>
      <div class="rpln-tweaks-row">
        <div class="rpln-tweaks-label">Density</div>
        <div class="rpln-tweaks-options">{density_opts}</div>
      </div>
    </div>
    """


def _render_settings_panel() -> None:
    """Settings panel — three tabs: General / Provider & Model / Data."""
    # WP-F.05.06 — dim backdrop behind the settings card. Injected outside
    # any Streamlit dialog so emotion-cache doesn't outrank the !important.
    st.html('<div class="rpln-settings-scrim"></div>')
    with st.container(key="rpln_settings_panel"):
        back_col, title_col = st.columns([1, 9])
        with back_col:
            if st.button(
                "",
                key="rpln_settings_back",
                help="Back to chat",
                icon=":material/arrow_back:",
            ):
                st.session_state.rpln_view = "chat"
                st.rerun()
        with title_col:
            st.markdown('<div class="rpln-settings-title">Settings</div>', unsafe_allow_html=True)

        tab_g, tab_p, tab_d = st.tabs(["General", "Provider & Model", "Data"])

        with tab_g:
            st.markdown("**Appearance**")
            st.caption(
                "Customize theme, typography, and density. "
                "Changes apply instantly and persist across sessions."
            )
            st.html(_build_appearance_section_html())

        with tab_p:
            _provider_keys = list(_PROVIDER_CONFIG.keys())
            cur_provider = st.session_state.get("llm_provider_label", _default_provider_label())
            p_idx = _provider_keys.index(cur_provider) if cur_provider in _provider_keys else 0
            provider_label: str = st.selectbox(
                "Provider", _provider_keys, index=p_idx, key="settings_provider"
            )
            cfg = _PROVIDER_CONFIG[provider_label]
            if cfg["needs_key"]:
                api_key: str = st.text_input(
                    f"API Key  ({cfg['env_var']})",
                    type="password",
                    value=st.session_state.get("api_key_saved", ""),
                    placeholder=f"Paste your {cfg['env_var']} here",
                    key="settings_api_key",
                )  # pyright: ignore[reportAssignmentType]
            else:
                api_key = ""
                st.info("Ollama runs locally — no API key required.", icon=":material/info:")

            model_list = cfg.get("models", [cfg["default_model"], _OTHER_MODEL_OPTION])
            cur_model = st.session_state.get("llm_model", cfg["default_model"])
            m_idx = model_list.index(cur_model) if cur_model in model_list else 0
            selected_m: str = st.selectbox("Model", model_list, index=m_idx, key="settings_model")
            if selected_m == _OTHER_MODEL_OPTION:
                custom_m: str = st.text_input(
                    "Model name",
                    value="",
                    placeholder=cfg["default_model"],
                    key="settings_model_custom",
                )
                model = custom_m.strip() or cfg["default_model"]
            else:
                model = selected_m

            key_ok = (not cfg["needs_key"]) or bool(api_key)
            if not key_ok:
                st.caption("⚠ Enter your API key before applying.")
            if st.button("Apply", key="settings_apply_llm", type="primary", disabled=not key_ok):
                wizard.apply_llm_config(provider_label, api_key, model)
                st.session_state.llm_provider_label = provider_label
                st.session_state.api_key_saved = api_key
                st.session_state.llm_model = model
                st.toast("LLM settings applied.", icon=":material/check_circle:")

        with tab_d:
            conv_dir = _conversations_dir()
            try:
                rel = conv_dir.relative_to(Path.home())
                masked = f"~/{rel}"
            except ValueError:
                masked = "…/conversations"
            st.markdown("**Conversations**")
            st.caption(masked)
            st.divider()
            if st.button(
                "Clear all conversations",
                key="rpln_clear_all_btn",
                type="secondary",
                icon=":material/delete:",
            ):
                st.session_state.rpln_clear_confirm = True
                st.rerun()
            if st.session_state.get("rpln_clear_confirm"):
                st.warning(
                    "This will permanently delete **all** saved conversations.",
                    icon=":material/warning:",
                )
                col_yes, col_no = st.columns(2)
                with col_yes:
                    if st.button("Confirm delete", key="rpln_clear_confirm_yes", type="primary"):
                        shutil.rmtree(conv_dir, ignore_errors=True)
                        _conversations_dir()
                        ss = st.session_state
                        ss.messages = []
                        ss.messages_meta = {}
                        new_cid = str(uuid.uuid4())
                        ss.current_conversation_id = new_cid
                        ss.thread_id = new_cid
                        ss.rpln_clear_confirm = False
                        st.toast("All conversations deleted.", icon=":material/delete:")
                        st.rerun()
                with col_no:
                    if st.button("Cancel", key="rpln_clear_confirm_no"):
                        st.session_state.rpln_clear_confirm = False
                        st.rerun()


# ---------------------------------------------------------------------------
# Backward-compat rendering shims (tests import these from web_ui)
# ---------------------------------------------------------------------------


def _render_conv_title_dropdown() -> None:
    """Topbar — no rpln_end_chat button (Flow 1 removal)."""
    shell.topbar()


def _render_sidebar() -> None:
    """Sidebar + profile logoff Streamlit button for bridge wiring."""
    shell.sidebar()
    _list_conversations()
    _search_conversations("")
    if st.button("Log off", key="rpln_profile_logoff"):
        st.query_params["shutdown"] = "1"
        st.rerun()


def _render_export_submenu(conv_id: str) -> None:
    """Popover with export download buttons for a conversation."""
    import importlib.util

    with st.popover("↓", width="content"):
        st.markdown("**Export**")
        st.download_button(
            "Download as .txt",
            data=_export_conversation_as_text(conv_id),
            file_name=f"{conv_id[:8]}.txt",
            mime="text/plain",
            key=f"rpln_dl_txt_{conv_id}",
        )
        st.download_button(
            "Download as .md",
            data=_export_conversation_as_md(conv_id),
            file_name=f"{conv_id[:8]}.md",
            mime="text/markdown",
            key=f"rpln_dl_md_{conv_id}",
        )
        if _conversation_has_artifacts(conv_id):
            has_kaleido = importlib.util.find_spec("kaleido") is not None
            for fmt in ("png", "jpeg"):
                st.download_button(
                    f"Download plots as .{fmt.upper()}",
                    data=_export_plots_as_zip(conv_id, fmt),
                    file_name=f"{conv_id[:8]}_plots_{fmt}.zip",
                    mime="application/zip",
                    key=f"rpln_dl_{fmt}_{conv_id}",
                )
            for fmt in ("csv", "xlsx"):
                st.download_button(
                    f"Download tables as .{fmt.upper()}",
                    data=_export_tables_as_zip(conv_id, fmt),
                    file_name=f"{conv_id[:8]}_tables_{fmt}.zip",
                    mime="application/zip",
                    key=f"rpln_dl_{fmt}_{conv_id}",
                )
            if not has_kaleido:
                st.caption("Install kaleido for plot export: `pip install kaleido`")


if __name__ == "__main__":
    main()
