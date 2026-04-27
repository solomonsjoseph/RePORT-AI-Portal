"""Shell: CSS injection, JS bridge, topbar, sidebar."""
from __future__ import annotations

import html as _html
import uuid
from pathlib import Path

import streamlit as st

from scripts.ai_assistant.agent_graph import reset_agent
from scripts.ai_assistant.ui.conversations import (
    _delete_conversation,
    _list_conversations,
    _load_conversation,
    _rename_conversation,
    _save_conversation,
    _toggle_pin,
)

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_SIDEBAR_PIN_LIMIT = 5
_SIDEBAR_VISIBLE_CHAT_LIMIT = 10
_TITLE_DISPLAY_LIMIT = 48


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _normalize_title(title: str | None) -> str:
    normalized = " ".join((title or "").split())
    return normalized or "Untitled"


def _truncate_title(title: str, *, limit: int = _TITLE_DISPLAY_LIMIT) -> tuple[str, bool]:
    if limit <= 3 or len(title) <= limit:
        return title, False
    return f"{title[: limit - 3].rstrip()}...", True


def inject_css() -> None:
    css = _read(_ASSETS_DIR / "theme.css")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def install_bridge() -> None:
    js = _read(_ASSETS_DIR / "bridge.js")
    with st.container(key="rpln_ui_bridge_shell"):
        st.iframe(
            f"<!doctype html><html><body style='margin:0;overflow:hidden'><script>{js}</script></body></html>",
            width="content",
            height="content",
            tab_index=-1,
        )


def _reset_session_to_new_conversation() -> None:
    ss = st.session_state
    new_id = str(uuid.uuid4())
    ss.current_conversation_id = new_id
    ss.current_conversation_title = "New conversation"
    ss.thread_id = new_id
    ss.messages = []
    ss.messages_meta = {}
    ss.rename_target = None
    reset_agent()


def topbar() -> None:
    ss = st.session_state
    title = _normalize_title(ss.get("current_conversation_title") or "New conversation")
    title_label, title_truncated = _truncate_title(title)

    # Topbar shell renders the mobile menu in HTML and layers native
    # Streamlit popovers for the title dropdown and the separate Share
    # control on top of the sticky header track.
    conv_id = ss.get("current_conversation_id", "")
    has_messages = bool(ss.get("messages"))
    current_conv = next((conv for conv in _list_conversations() if conv["id"] == conv_id), None)
    is_pinned = bool(current_conv and current_conv.get("pinned"))
    with st.container(key="rpln_topbar_shell"):
        st.html(
            """
            <div class="rpln-topbar">
                <button class="rpln-topbar-btn rpln-mobile-menu-btn" type="button"
                        title="Menu" data-rpln-action="toggle-mobile-nav">
                    <span class="material-symbols-rounded">menu</span>
                </button>
            </div>
            """
        )
        with st.container(key="rpln_topbar_title_menu"):
            st.markdown(
                (
                    '<div class="rpln-topbar-title-overlay" aria-hidden="true" '
                    f'title="{_html.escape(title, quote=True)}">'
                    f'{_html.escape(title_label)}'
                    '</div>'
                ),
                unsafe_allow_html=True,
            )
            with st.popover(
                title_label,
                width="content",
                disabled=not has_messages,
                help=(
                    title
                    if title_truncated
                    else (
                        "Conversation actions"
                        if has_messages
                        else "Start a chat to enable conversation actions"
                    )
                ),
            ):
                if conv_id and has_messages:
                    rename_key = f"rpln_topbar_title_{conv_id}"
                    rename_seed_key = f"{rename_key}_seed"
                    rename_mode_key = f"rpln_topbar_mode_{conv_id}"
                    if ss.get(rename_mode_key) not in {"menu", "rename"}:
                        ss[rename_mode_key] = "menu"
                    if ss.get(rename_seed_key) != title:
                        ss[rename_key] = title
                        ss[rename_seed_key] = title
                    st.markdown(
                        '<div class="rpln-topbar-menu-sentinel" aria-hidden="true"></div>',
                        unsafe_allow_html=True,
                    )
                    if ss.get(rename_mode_key) == "rename":
                        st.text_input(
                            "Conversation title",
                            key=rename_key,
                            label_visibility="collapsed",
                            placeholder="Rename conversation",
                        )
                        save_col, cancel_col = st.columns(2, gap="small")
                        if save_col.button(
                            "Save",
                            key=f"rpln_topbar_rename_save_{conv_id}",
                            width="stretch",
                        ):
                            new_title = (ss.get(rename_key) or "").strip()
                            if new_title:
                                _rename_conversation(conv_id, new_title)
                                ss.current_conversation_title = new_title
                            ss[rename_mode_key] = "menu"
                            st.rerun()
                        if cancel_col.button(
                            "Cancel",
                            key=f"rpln_topbar_rename_cancel_{conv_id}",
                            width="stretch",
                        ):
                            ss[rename_mode_key] = "menu"
                            st.rerun()
                    else:
                        st.markdown(
                            '<div class="rpln-topbar-action-list" aria-hidden="true"></div>',
                            unsafe_allow_html=True,
                        )
                        if st.button(
                            "Rename",
                            key=f"rpln_topbar_rename_open_{conv_id}",
                            width="stretch",
                        ):
                            ss[rename_mode_key] = "rename"
                            st.rerun()
                        if st.button(
                            ("\u2605 Unstar" if is_pinned else "\u2606 Star"),
                            key=f"rpln_topbar_pin_{conv_id}",
                            width="stretch",
                        ):
                            _toggle_pin(conv_id)
                            ss[rename_mode_key] = "menu"
                            st.rerun()
                        if st.button(
                            "Delete",
                            key=f"rpln_topbar_delete_{conv_id}",
                            width="stretch",
                        ):
                            _delete_conversation(conv_id)
                            ss[rename_mode_key] = "menu"
                            _reset_session_to_new_conversation()
                            st.rerun()
                else:
                    st.caption("Start a chat to enable conversation actions.")

        with st.container(key="rpln_topbar_share"), st.popover(
            "Share",
            width="content",
            disabled=not has_messages,
            help=(
                "Share and export"
                if has_messages
                else "Start a chat to enable sharing"
            ),
        ):
            if conv_id and has_messages:
                from scripts.ai_assistant.ui.conversations import (
                    _export_conversation_as_md,
                    _export_conversation_as_text,
                )

                st.markdown(
                    '<div class="rpln-topbar-share-sentinel" aria-hidden="true"></div>',
                    unsafe_allow_html=True,
                )
                st.download_button(
                    "Markdown (.md)",
                    data=_export_conversation_as_md(conv_id),
                    file_name=f"{conv_id[:8]}.md",
                    mime="text/markdown",
                    key=f"rpln_share_export_md_{conv_id}",
                    width="stretch",
                )
                st.download_button(
                    "Plain text (.txt)",
                    data=_export_conversation_as_text(conv_id),
                    file_name=f"{conv_id[:8]}.txt",
                    mime="text/plain",
                    key=f"rpln_share_export_txt_{conv_id}",
                    width="stretch",
                )
            else:
                st.caption("Start a chat to enable sharing.")


def sidebar() -> None:
    ss = st.session_state

    with st.sidebar:
        st.html(
            """
            <div class="rpln-side-head">
              <div class="rpln-brand">
                <div class="rpln-brand-mark">R</div>
                <div>
                  <div class="rpln-brand-name">
                    RePORT AI Portal
                  </div>
                  <div class="rpln-brand-sub">Clinical AI Assistant</div>
                </div>
              </div>
            </div>

            <button class="rpln-new-chat" type="button" data-rpln-action="new-chat">
              <span class="material-symbols-rounded">edit_square</span>
              <span>New chat</span>
            </button>

            <div class="rpln-side-search" data-rpln-action="open-search"
                 role="button" tabindex="0">
              <span class="material-symbols-rounded">search</span>
              <span>Search chats</span>
            </div>
            """
        )

        # Hidden Streamlit button wired to open-search keyboard / click.
        if st.button("open search", key="rpln_open_search"):
            ss.rpln_search_modal_open = True
            st.rerun()

        # WP-F.05.11c — hidden rename bridge. Wrapped in st.form so the
        # text_input value syncs to session_state atomically when the form
        # submit fires (Streamlit commits ALL form inputs on submit, unlike
        # free-standing widgets which only sync on blur). Bridge.js stuffs
        # "<cid>||<title>" into the input via native setter, then clicks the
        # submit button — the form-submit rerun reads the payload and renames.
        with st.form(key="rpln_rename_form", clear_on_submit=True, border=False):
            st.text_input(
                "rename payload",
                key="rpln_rename_payload",
                label_visibility="collapsed",
            )
            if st.form_submit_button("rename apply"):
                payload = ss.get("rpln_rename_payload", "") or ""
                if "||" in payload:
                    cid, new_title = payload.split("||", 1)
                    cid = cid.strip()
                    new_title = new_title.strip()
                    if cid and new_title:
                        _rename_conversation(cid, new_title)
                st.rerun()

        # Hidden Streamlit button wired to new-chat bridge action
        if st.button("new chat", key="rpln_new_chat"):
            _save_conversation()
            _reset_session_to_new_conversation()
            st.rerun()

        all_conversations = _list_conversations()
        pinned_conversations = [conv for conv in all_conversations if conv.get("pinned")]
        recent_conversations = [conv for conv in all_conversations if not conv.get("pinned")]
        visible_pins = pinned_conversations[:_SIDEBAR_PIN_LIMIT]
        remaining_slots = max(0, _SIDEBAR_VISIBLE_CHAT_LIMIT - len(visible_pins))
        visible_recent = recent_conversations[:remaining_slots]
        hidden_conversation_count = (
            max(0, len(pinned_conversations) - len(visible_pins))
            + max(0, len(recent_conversations) - len(visible_recent))
        )

        rendered_any = False
        with st.container(key="rpln_sidebar_list_shell"):
            if visible_pins:
                _render_group("pinned", "Pinned", visible_pins)
                rendered_any = True
            if visible_recent:
                _render_group("recent", "Recent", visible_recent)
                rendered_any = True
            if not rendered_any:
                st.html('<p class="rpln-empty-recents">No chats yet</p>')
            if hidden_conversation_count > 0:
                st.html(
                    """
                    <div class="rpln-sidebar-footer">
                      <button class="rpln-all-chats" type="button" data-rpln-action="open-search">
                        <span class="rpln-all-chats-icon" aria-hidden="true">
                          <span class="material-symbols-rounded">more_horiz</span>
                        </span>
                        <span>All chats</span>
                      </button>
                    </div>
                    """
                )

        # Profile dock — click opens the JS-toggled popover above.
        st.html(
            """
            <div class="rpln-profile-root">
              <div class="rpln-profile-dock" data-rpln-action="toggle-profile-menu">
                <div class="rpln-avatar">R</div>
                <div class="rpln-profile-text">
                  <div class="rpln-profile-name">Researcher</div>
                  <div class="rpln-profile-role">Local workspace</div>
                </div>
                <button class="rpln-icon-btn rpln-pd-caret" type="button"
                        title="Open menu"
                        data-rpln-action="toggle-profile-menu">
                  <span class="material-symbols-rounded">expand_less</span>
                </button>
              </div>
              <div class="rpln-profile-popover" data-rpln-profile-popover>
                <button class="rpln-profile-row" type="button"
                        data-rpln-action="profile-settings">
                  <span class="material-symbols-rounded">settings</span>
                  <span class="rpln-pm-label">Settings</span>
                </button>
                <div class="rpln-pm-sep"></div>
                <button class="rpln-profile-row rpln-profile-row-danger" type="button"
                        data-rpln-action="profile-logout">
                  <span class="material-symbols-rounded">logout</span>
                  <span class="rpln-pm-label">Log out</span>
                </button>
              </div>
            </div>
            """
        )

        # Hidden bridge targets — bridge.js fires these on menu-row click.
        if st.button("profile-settings", key="rpln_profile_settings_btn"):
            ss.rpln_view = "settings"
            st.rerun()
        if st.button("profile-logout", key="rpln_profile_logout_btn"):
            st.query_params["shutdown"] = "1"
            st.rerun()


def _render_group(group_name: str, label: str, convs: list[dict]) -> None:
    ss = st.session_state
    current_id = ss.get("current_conversation_id", "")
    rows_html = []
    for conv in convs:
        cid = conv["id"]
        full_title = _normalize_title(conv.get("title"))
        display_title, _ = _truncate_title(full_title)
        title = _html.escape(display_title)
        escaped_full_title = _html.escape(full_title)
        active = "1" if cid == current_id else "0"
        pin_mark = (
            '<span class="material-symbols-rounded rpln-pin-mark">push_pin</span>'
            if group_name == "pinned"
            else ""
        )
        rows_html.append(
            f"""
            <div class="rpln-conv-row" data-rpln-active="{active}"
                      data-rpln-action="switch-conv" data-rpln-conv-id="{cid}"
                      title="{escaped_full_title}" aria-label="{escaped_full_title}">
              {pin_mark}
                  <span class="rpln-conv-title" data-rpln-conv-id="{cid}"
                          title="{escaped_full_title}" aria-label="{escaped_full_title}">{title}</span>
              <button class="rpln-row-close" type="button"
                                            data-rpln-action="pin-conv" data-rpln-conv-id="{cid}"
                                            title="{'Unpin' if group_name == 'pinned' else 'Pin'}">
                                <span class="material-symbols-rounded">
                                    {'close' if group_name == 'pinned' else 'push_pin'}
                                </span>
              </button>
              <div class="rpln-row-menu-wrap">
                <button class="rpln-row-kebab" type="button"
                        data-rpln-action="toggle-conv-menu" data-rpln-conv-id="{cid}"
                                                title="More actions for {escaped_full_title}"
                                                aria-label="More actions for {escaped_full_title}">
                  <span class="material-symbols-rounded">more_horiz</span>
                </button>
                <div class="rpln-row-menu" data-rpln-conv-id="{cid}">
                  <button type="button" data-rpln-action="rename-conv"
                          data-rpln-conv-id="{cid}">
                    <span class="material-symbols-rounded">edit</span>
                    <span>Rename</span>
                  </button>
                  <button type="button" data-rpln-action="pin-conv"
                          data-rpln-conv-id="{cid}">
                    <span class="material-symbols-rounded">
                      {'keep_off' if group_name == 'pinned' else 'keep'}
                    </span>
                    <span>{'Unpin' if group_name == 'pinned' else 'Pin'}</span>
                  </button>
                  <button type="button" data-rpln-action="delete-conv"
                          data-rpln-conv-id="{cid}" class="rpln-row-menu-danger">
                    <span class="material-symbols-rounded">delete</span>
                    <span>Delete</span>
                  </button>
                </div>
              </div>
            </div>
            """
        )

    st.html(
        f"""
        <div class="rpln-group" data-group="{group_name}" data-open="1">
          <div class="rpln-group-header" data-rpln-action="toggle-group">
            <span class="rpln-chev">&rsaquo;</span>
            <span>{label}</span>
          </div>
          <div class="rpln-conv-list">
            {''.join(rows_html)}
          </div>
        </div>
        """
    )

    for conv in convs:
        cid = conv["id"]
        if st.button("switch", key=f"rpln_switch_{cid}"):
            _save_conversation()
            # _load_conversation populates session state directly + resets the
            # agent on success; nothing further to do on this side.
            _load_conversation(cid)
            st.rerun()
        if st.button("delete", key=f"rpln_del_{cid}"):
            _delete_conversation(cid)
            if ss.current_conversation_id == cid:
                _reset_session_to_new_conversation()
            st.rerun()
        if st.button("pin", key=f"rpln_pin_{cid}"):
            _toggle_pin(cid)
            st.rerun()
